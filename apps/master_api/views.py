"""Master Mini App HTTP views — PR 1 / M0 onboarding.

Endpoints under ``/api/v1/master/``:

* ``POST /onboarding/claim``  — token → master profile preview (Step 1)
* ``POST /onboarding/accept`` — bind BotUser, transition PENDING→ACCEPTED,
                                issue session token (Step 2/3 gate)
* ``POST /onboarding/reject`` — transition PENDING→CANCELLED (no linkage)
* ``PATCH /onboarding/profile`` — Step 3 bio + photo, idempotent
* ``GET  /me``                — bootstrap state for a linked master

The first three accept init-data WITHOUT a linked master (the master
account isn't bound until accept succeeds). The latter two require a
linked + active master via :func:`require_master_init_data`.

### Idempotency

* ``claim``  — pure read; safe to call repeatedly.
* ``accept`` — if the master is already accepted AND linked to the
  current BotUser, returns the same 200 shape (and re-issues a session
  token so the client can refresh). Wrong BotUser → 403.
* ``reject`` — once CANCELLED, calling reject again returns 409
  (already-used invite). Symmetric with accept.
* ``profile`` — last-write-wins on each call. Always idempotent.

### Tenant safety

All endpoints resolve tenant from the BotUser (HMAC-verified init-data
→ unique (channel, channel_user_id) lookup → BotUser → Tenant). The
caller can't forge a different tenant: init-data is signed by MAX's
HMAC and the BotUser FK is tenant-scoped at the DB level. Invite
token lookup is filtered by tenant explicitly as defence-in-depth.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.master_api.services.dashboard import build_dashboard
from apps.events.services import emit
from apps.events.vocabulary import (
    MASTER_ONBOARDING_ACCEPTED,
    MASTER_ONBOARDING_REJECTED,
    MASTER_ONBOARDING_STARTED,
    MASTER_PROFILE_INITIALIZED,
)
from apps.identity.models import BotUser
from apps.master_api.auth import (
    INVITE_TOKEN_SLUG_TO_STATUS,
    InviteTokenError,
    issue_master_session_token,
    require_init_data_only,
    require_master_init_data,
    validate_invite_token,
)

logger = logging.getLogger(__name__)


MAX_BIO_LENGTH = 280
"""Per master-mobile §M0 Step 3 — twitter-length bio limit."""


# --- helpers ---------------------------------------------------------------


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _parse_json_body(request: HttpRequest) -> dict[str, Any] | JsonResponse:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(data, dict):
        return _error("bad_request", "JSON body must be an object", 400)
    return data


_PHONE_TAIL_RE = re.compile(r"(\d{2})$")


def _mask_phone(phone: str) -> str:
    """E.164-style phone → ``+7 ••• ••• ••67`` (last two digits visible).

    Used in the M0 Step 1 identity-confirm card. Defensive: empty input
    returns empty string; short input returns ``•••``.
    """

    if not phone:
        return ""
    tail = _PHONE_TAIL_RE.search(phone)
    if tail is None:
        return "•••"
    return f"+• ••• ••• ••{tail.group(1)}"


def _services_for_master(master: CatalogMaster) -> list[dict[str, Any]]:
    """Resolved services this master performs.

    Driven by ``MasterService`` mapping rows — same surface used by the
    customer-side master_detail endpoint (apps/miniapp_api/views.py).
    Inactive services are excluded; the master's own profile screen
    shouldn't show services they can't be booked for.
    """

    service_ids = MasterService.all_tenants.filter(
        master_id=master.id, tenant=master.tenant
    ).values_list("service_id", flat=True)
    services = CatalogService.all_tenants.filter(
        id__in=list(service_ids),
        tenant=master.tenant,
        is_active=True,
    ).order_by("name")
    return [
        {
            "id": str(s.id),
            "name": s.name,
            "duration_min": s.duration_min,
        }
        for s in services
    ]


def _master_card(master: CatalogMaster, *, include_services: bool = True) -> dict[str, Any]:
    """Compact master payload for M0 screens.

    ``include_services`` is False for the reject path's audit context
    where we deliberately don't leak the master profile.
    """

    payload: dict[str, Any] = {
        "id": str(master.id),
        "name": master.name,
        "specialization": master.specialization,
        "bio": master.bio,
        "photo_url": master.photo_url,
    }
    if include_services:
        payload["services"] = _services_for_master(master)
        # Working hours editing is a separate PR; expose a hardcoded
        # one-line summary so the M0 Step 1 card has something to render.
        # TODO(master PR 4+): real schedule summary from ScheduleException
        # + WorkingHours.
        payload["working_hours_summary"] = "Расписание уточнит салон"
    return payload


def _audit_payload(
    master: CatalogMaster, bot_user: BotUser, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Standard audit-row payload for master.onboarding_* events."""

    out: dict[str, Any] = {
        "tenant_id": str(master.tenant_id),
        "master_id": str(master.id),
        "bot_user_id": str(bot_user.id),
    }
    if extra:
        out.update(extra)
    return out


# --- POST /onboarding/claim -----------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data_only
def onboarding_claim(request: HttpRequest) -> HttpResponse:
    """Validate token + return the M0 Step 1 identity-confirm payload.

    Idempotent — no DB mutation. The caller can hit this endpoint
    multiple times during step 1 (e.g. if the user backs out and
    re-opens the deeplink).

    Cross-tenant safety: ``validate_invite_token`` filters by the
    BotUser's tenant. A token from another tenant returns 404 (deliberate
    information-leak suppression).

    Wrong-recipient guard: if the master is already linked to a DIFFERENT
    BotUser, returns 403 — someone forwarded the deeplink. We do NOT
    consume the token in this case; the rightful owner can still claim.
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    token = body.get("token") or ""
    if not token:
        return _error("bad_request", "token is required", 400)

    try:
        with transaction.atomic():
            master = validate_invite_token(token, bot_user.tenant)
    except InviteTokenError as exc:
        return _error(
            exc.slug,
            exc.detail,
            INVITE_TOKEN_SLUG_TO_STATUS.get(exc.slug, 400),
        )

    # Wrong-recipient guard. A linked master here means the row's owner
    # already accepted; if the current BotUser isn't them, it's a
    # forwarded link. (Note: validate_invite_token rejects already-used
    # invites already, so a linked master with status=PENDING is an
    # impossible state — defence-in-depth check anyway.)
    if master.linked_bot_user_id is not None and master.linked_bot_user_id != bot_user.id:
        return _error(
            "wrong_recipient",
            "this invite was sent to a different MAX account",
            403,
        )

    write_audit(
        MASTER_ONBOARDING_STARTED,
        target="catalog.CatalogMaster",
        target_id=master.id,
        payload=_audit_payload(master, bot_user),
        actor_id=bot_user.id,
    )
    emit(MASTER_ONBOARDING_STARTED, properties=_audit_payload(master, bot_user))

    max_user = {
        "first_name": bot_user.display_name
        or request.verified_init_data.user.get("first_name", ""),  # type: ignore[attr-defined]
        "phone_masked": _mask_phone(bot_user.phone),
        "max_handle": master.max_handle,
    }
    return JsonResponse(
        {
            "master": _master_card(master),
            "salon": {
                "tenant_id": str(master.tenant_id),
                "name": master.tenant.name,
            },
            "max_user": max_user,
        }
    )


# --- POST /onboarding/accept ----------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data_only
def onboarding_accept(request: HttpRequest) -> HttpResponse:
    """Bind BotUser, transition PENDING → ACCEPTED, issue session token.

    Atomic via ``transaction.atomic`` + ``select_for_update`` (inside
    :func:`validate_invite_token`). The transition + linkage + token
    nulling happen as one DB write.

    Idempotency: if the master is ALREADY linked to the calling
    BotUser AND status == ACCEPTED, returns the same 200 shape with a
    freshly-minted session token. This lets the Mini App safely retry
    the call (network blip, user double-tapped). A DIFFERENT BotUser →
    403.

    Side effects:
      * ``invite_token = None`` (one-shot consumption)
      * ``invite_status = ACCEPTED``
      * ``mode = INVITE`` (this master now has login access)
      * ``linked_bot_user = current bot_user``
      * Audit row + event ``master.onboarding_accepted``
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    token = body.get("token") or ""
    if not token:
        return _error("bad_request", "token is required", 400)

    # Idempotency probe — if the SAME bot_user already accepted, reuse
    # the existing row + re-issue a session token. We look up by
    # linked_bot_user (NOT invite_token, which is cleared on accept).
    try:
        token_uuid = uuid.UUID(str(token))
    except (TypeError, ValueError):
        return _error("invalid_invite_token", "token is not a valid UUID", 404)

    existing = (
        CatalogMaster.all_tenants.filter(
            linked_bot_user=bot_user,
            tenant=bot_user.tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        .select_related("tenant")
        .first()
    )
    if existing is not None:
        # The accepted row may have cleared invite_token already, so we
        # can't match on the wire token. We trust the linkage: the
        # SAME BotUser arriving with ANY token after accepting is a
        # retry. Spec says "return same shape (200), do not error".
        new_token, exp = issue_master_session_token(
            master_id=existing.id,
            tenant_id=existing.tenant_id,
            bot_user_id=bot_user.id,
        )
        return JsonResponse(
            {
                "master_id": str(existing.id),
                "session_token": new_token,
                "expires_at": datetime.fromtimestamp(exp, tz=dt_timezone.utc).isoformat(),
            }
        )

    # Fresh accept path. Atomic + locked.
    try:
        with transaction.atomic():
            master = validate_invite_token(token_uuid, bot_user.tenant)

            # Wrong-recipient guard (mirrors claim's check; can't happen
            # with status=PENDING but defence-in-depth — see claim's
            # comment).
            if master.linked_bot_user_id is not None and master.linked_bot_user_id != bot_user.id:
                return _error(
                    "wrong_recipient",
                    "this invite was sent to a different MAX account",
                    403,
                )

            master.linked_bot_user = bot_user
            master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
            master.mode = CatalogMaster.Mode.INVITE
            master.invite_token = None  # one-shot consumption
            master.save(
                update_fields=[
                    "linked_bot_user",
                    "invite_status",
                    "mode",
                    "invite_token",
                ]
            )

            write_audit(
                MASTER_ONBOARDING_ACCEPTED,
                target="catalog.CatalogMaster",
                target_id=master.id,
                payload=_audit_payload(master, bot_user),
                actor_id=bot_user.id,
            )
            emit(
                MASTER_ONBOARDING_ACCEPTED,
                properties=_audit_payload(master, bot_user),
            )
    except InviteTokenError as exc:
        return _error(
            exc.slug,
            exc.detail,
            INVITE_TOKEN_SLUG_TO_STATUS.get(exc.slug, 400),
        )

    session_token, exp_ts = issue_master_session_token(
        master_id=master.id,
        tenant_id=master.tenant_id,
        bot_user_id=bot_user.id,
    )
    return JsonResponse(
        {
            "master_id": str(master.id),
            "session_token": session_token,
            "expires_at": datetime.fromtimestamp(exp_ts, tz=dt_timezone.utc).isoformat(),
        }
    )


# --- POST /onboarding/reject ----------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data_only
def onboarding_reject(request: HttpRequest) -> HttpResponse:
    """Transition PENDING → CANCELLED. No BotUser linkage.

    Returns 204. We deliberately do NOT echo any master profile data —
    a malicious actor with a token shouldn't be able to learn the
    master's name by rejecting on someone else's behalf.

    Audit captures the bot_user_id of whoever clicked reject — useful
    for ops if a master complains their invite was rejected before they
    saw it.
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    token = body.get("token") or ""
    if not token:
        return _error("bad_request", "token is required", 400)

    try:
        with transaction.atomic():
            master = validate_invite_token(token, bot_user.tenant)
            master.invite_status = CatalogMaster.InviteStatus.CANCELLED
            # Keep invite_token populated on reject — ops may want to
            # forensically tie the cancellation to the original token in
            # the audit row. The unique constraint still holds because
            # subsequent invites get fresh UUIDs.
            master.save(update_fields=["invite_status"])

            write_audit(
                MASTER_ONBOARDING_REJECTED,
                target="catalog.CatalogMaster",
                target_id=master.id,
                payload=_audit_payload(master, bot_user),
                actor_id=bot_user.id,
            )
            emit(
                MASTER_ONBOARDING_REJECTED,
                properties=_audit_payload(master, bot_user),
            )
    except InviteTokenError as exc:
        return _error(
            exc.slug,
            exc.detail,
            INVITE_TOKEN_SLUG_TO_STATUS.get(exc.slug, 400),
        )

    return HttpResponse(status=204)


# --- PATCH /onboarding/profile --------------------------------------------


def _save_master_photo(master: CatalogMaster, file_obj: Any) -> str:
    """Save the uploaded photo + return the absolute URL.

    Phase 1 (this PR): raw upload only, no resize pipeline. Lives under
    ``MEDIA_ROOT/master_photos/<master_id>.<ext>`` and the URL is
    ``MEDIA_URL + master_photos/<master_id>.<ext>``.

    TODO(master PR 4+): proper resize pipeline (Pillow → 800×800 JPEG
    + thumbnail). Track via media-pipeline ticket. For now we accept
    PNG/JPEG/WEBP and trust the extension; content-type sniffing is
    a follow-up.
    """

    ext = (Path(file_obj.name).suffix or ".jpg").lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"

    media_root = Path(getattr(settings, "MEDIA_ROOT", "media"))
    media_url = getattr(settings, "MEDIA_URL", "/media/")
    photos_dir = media_root / "master_photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    out_path = photos_dir / f"{master.id}{ext}"
    with open(out_path, "wb") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)

    return f"{media_url.rstrip('/')}/master_photos/{master.id}{ext}"


@csrf_exempt
@require_http_methods(["PATCH"])
@require_master_init_data
def onboarding_profile(request: HttpRequest) -> HttpResponse:
    """M0 Step 3 — populate bio + photo. Idempotent.

    Accepts multipart (for photo) OR JSON (bio-only). The bio comes from
    either ``request.POST['bio']`` (multipart) or the JSON body.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    bio: str | None = None
    photo_file = None

    content_type = request.headers.get("Content-Type", "")
    if content_type.startswith("multipart/form-data"):
        # Django only auto-parses multipart on POST; on PATCH we drive
        # MultiPartParser manually so request.FILES / request.POST get
        # populated. See django/http/request.py::_load_post_and_files.
        from django.http.multipartparser import MultiPartParser

        try:
            post, files = MultiPartParser(
                request.META, request, request.upload_handlers, request.encoding
            ).parse()
        except Exception:  # noqa: BLE001 — malformed multipart → 400
            return _error("bad_request", "malformed multipart body", 400)
        if "bio" in post:
            bio = str(post["bio"])
        photo_file = files.get("photo")
    else:
        body = _parse_json_body(request)
        if isinstance(body, JsonResponse):
            return body
        if "bio" in body:
            bio = body["bio"]

    fields_populated: list[str] = []
    update_fields: list[str] = []

    if bio is not None:
        if len(bio) > MAX_BIO_LENGTH:
            return _error(
                "bad_request",
                f"bio exceeds {MAX_BIO_LENGTH} characters",
                400,
            )
        master.bio = bio
        update_fields.append("bio")
        if bio.strip():
            fields_populated.append("bio")

    if photo_file is not None:
        master.photo_url = _save_master_photo(master, photo_file)
        update_fields.append("photo_url")
        fields_populated.append("photo")

    if update_fields:
        master.save(update_fields=update_fields)
        write_audit(
            MASTER_PROFILE_INITIALIZED,
            target="catalog.CatalogMaster",
            target_id=master.id,
            payload=_audit_payload(master, bot_user, {"fields_populated": fields_populated}),
            actor_id=bot_user.id,
        )
        emit(
            MASTER_PROFILE_INITIALIZED,
            properties=_audit_payload(master, bot_user, {"fields_populated": fields_populated}),
        )

    return JsonResponse(
        {
            "master": {
                "id": str(master.id),
                "name": master.name,
                "bio": master.bio,
                "photo_url": master.photo_url,
            }
        }
    )


# --- GET /me ---------------------------------------------------------------


@require_http_methods(["GET"])
@require_master_init_data
def me(request: HttpRequest) -> HttpResponse:
    """Bootstrap state for a linked master.

    Read-only — no audit row. The Mini App calls this on launch to
    rebuild local state after the session token is loaded from
    DeviceStorage.

    Permissions block: PR 1 hardcodes all three to True. The full
    permission model (PR 11+) will compute these from role + tenant
    settings.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    return JsonResponse(
        {
            "master": {
                "id": str(master.id),
                "name": master.name,
                "specialization": master.specialization,
                "bio": master.bio,
                "photo_url": master.photo_url,
                "services": _services_for_master(master),
            },
            "salon": {
                "tenant_id": str(master.tenant_id),
                "name": master.tenant.name,
            },
            "permissions": {
                "can_edit_schedule": True,
                "can_edit_services": True,
                "can_message_customers": True,
            },
        }
    )


# --- GET /dashboard --------------------------------------------------------


@require_http_methods(["GET"])
@require_master_init_data
def dashboard(request: HttpRequest) -> HttpResponse:
    """M1 master-mobile home aggregator (master-mobile §M1).

    Spec quote (§M1 layout block):

        «Screen M1 — Master mobile dashboard (home) … СЕЙЧАС … СЛЕДУЮЩИЙ
        КЛИЕНТ … ТРЕБУЮТ ВНИМАНИЯ (2) … СЕГОДНЯ … [🏠] [📅] [💬 2] [👤]»

    Single GET — composes :func:`apps.master_api.services.dashboard.build_dashboard`.
    Read-only; no audit row. Tenant TZ taken from
    :attr:`apps.tenancy.models.Tenant.timezone` (default Europe/Moscow,
    fallback UTC on invalid IANA names).
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    snapshot = build_dashboard(master, dj_timezone.now())
    return JsonResponse(snapshot.to_dict())
