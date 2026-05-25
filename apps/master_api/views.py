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
from datetime import datetime, timedelta, timezone as dt_timezone
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
from apps.master_api.services.conversations import (
    ConversationsListError,
    DEFAULT_LIMIT as CONVERSATIONS_DEFAULT_LIMIT,
    MAX_LIMIT as CONVERSATIONS_MAX_LIMIT,
    list_master_conversations,
)
from apps.master_api.services.ai_draft_limits import check_and_consume_rate_limit
from apps.master_api.services.ai_drafts import (
    generate_draft_for_conversation,
    release_draft_to_ai,
    send_draft_as_master,
)
from apps.master_api.services.conversation_detail import (
    ConversationDetailError,
    get_conversation_detail,
    mark_conversation_read,
    promote_to_human_locked,
    send_master_message,
)
from apps.master_api.services.dashboard import build_dashboard
from apps.master_api.services.notification_prefs import (
    NotificationPrefsError,
    get_or_create_prefs,
    serialise_prefs,
    update_prefs,
)
from apps.master_api.services.schedule import (
    AvailabilityRequestError,
    DEFAULT_RANGE_DAYS,
    MAX_RANGE_DAYS,
    build_schedule,
    list_pending_requests,
    request_availability_change,
)
from apps.events.services import emit
from apps.events.vocabulary import (
    MASTER_AVAILABILITY_CHANGE_REQUESTED,
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


def _draft_error_response(exc: Any) -> JsonResponse:
    """Map a :class:`DraftActionError` to a JSON response.

    Mirrors the inline mapping in
    :func:`conversation_draft_generate` for the ``generate_in_flight``
    slug (Issue #550). Generalised here so the send/release endpoints
    surface ``conversation_busy`` (Issue #551 — lock symmetry) with the
    same shape: JSON body carries ``retry_after_seconds`` AND the
    response has a ``Retry-After`` header for clients that read it.

    Slugs handled with Retry-After:
      * ``generate_in_flight`` — Issue #550 (generate path)
      * ``conversation_busy`` — Issue #551 (send / release path)
      * ``cost_cap_exceeded`` — fixed 1h hint
    """

    retry_after = exc.extra.get("retry_after_seconds") if exc.extra else None
    body: dict[str, Any] = {"error": exc.slug, "detail": exc.detail}
    if isinstance(retry_after, int) and retry_after > 0:
        body["retry_after_seconds"] = retry_after
    resp = JsonResponse(body, status=exc.status)
    if exc.slug in ("generate_in_flight", "conversation_busy"):
        secs = retry_after if isinstance(retry_after, int) and retry_after > 0 else 3
        resp["Retry-After"] = str(secs)
    elif exc.slug == "cost_cap_exceeded":
        resp["Retry-After"] = "3600"
    return resp


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


# --- M3 schedule + availability (PR Tier1.2) -------------------------------


def _parse_iso_date(raw: str):  # type: ignore[no-untyped-def]
    """Parse YYYY-MM-DD. Returns ``datetime.date | None`` (lazy import)."""

    from datetime import date

    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _parse_iso_datetime(raw: str) -> datetime | None:
    """Parse ISO datetime. Accepts trailing 'Z' (UTC). Returns None on bad input."""

    if not isinstance(raw, str) or not raw:
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Naive — treat as UTC. Production clients always include TZ
        # via Mini App's locale-aware Date.toISOString().
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def _maybe_send_manager_dm(*, tenant: Any, master: CatalogMaster, request_id: uuid.UUID) -> None:
    """Dispatch the «Анна просит выходной» DM to the salon manager.

    Per master-mobile §M3 line 458: «server marks slot blocked → owner
    notified (audit + bot DM)». No-op when ``manager_chat_id`` is
    empty — degraded mode aligned with the reminder-escalation pattern
    (apps/bookings/tasks::escalate_stale_reminders).

    Imports are local because the function is wired via
    ``transaction.on_commit`` and the channels module isn't needed by
    every master endpoint.
    """

    chat_id = (tenant.manager_chat_id or "").strip()
    if not chat_id:
        logger.info(
            "master_api.availability.no_manager_chat_id tenant=%s master=%s",
            tenant.id,
            master.id,
        )
        return

    from apps.channels.max.outbound import MaxAPIError, send_message

    # Admin Mini App deeplink — settings-overridable so staging can point
    # at the staging admin URL. The default value mirrors the customer-
    # side ADMIN_MINI_APP_URL convention from PR #450.
    admin_url = getattr(
        settings,
        "ADMIN_MINI_APP_URL",
        "https://admin.formulatela.ru/availability",
    )
    text = (
        f"{master.name} просит изменить расписание. "
        f"[Открыть запрос]({admin_url}?request_id={request_id})"
    )
    try:
        send_message(chat_id=chat_id, text=text)
    except MaxAPIError:
        # Best-effort: the audit + DB row are the source of truth. DM
        # failures are logged for ops but don't propagate.
        logger.warning(
            "master_api.availability.manager_dm_failed tenant=%s request=%s",
            tenant.id,
            request_id,
            exc_info=True,
        )


@csrf_exempt
@require_http_methods(["GET"])
@require_master_init_data
def schedule(request: HttpRequest) -> HttpResponse:
    """M3 schedule read — own bookings + free windows + conflicts over a range.

    Spec quote (master-mobile §M3 line 474):

        «`GET /api/master/schedule?from=...&to=...` — returns bookings
        + free/blocked slots + work hours for master»

    Spec quote (§M3 line 466):

        «Conflict (admin double-booked you): «⚠ Конфликт расписания —
        посмотрите» + tap → conversation with admin / banner with
        «уточнить у админа» button»

    Query params:
      from: YYYY-MM-DD (tenant-local). Defaults to today.
      to:   YYYY-MM-DD inclusive. Defaults to from + 7 days.

    Max range 31 days; wider → 400. ``from > to`` → 400.

    Cross-master + cross-tenant isolation enforced via
    :func:`require_master_init_data` + the service helper's explicit
    master_id + tenant_id filters.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]

    # Resolve defaults in tenant-local TZ so «today» means today for
    # the master, not for UTC.
    from apps.master_api.services.schedule import get_tenant_tz

    tz = get_tenant_tz(tenant)
    today_local = dj_timezone.now().astimezone(tz).date()

    raw_from = request.GET.get("from", "").strip()
    raw_to = request.GET.get("to", "").strip()

    if raw_from:
        from_date = _parse_iso_date(raw_from)
        if from_date is None:
            return _error("bad_request", "'from' must be YYYY-MM-DD", 400)
    else:
        from_date = today_local

    if raw_to:
        to_date = _parse_iso_date(raw_to)
        if to_date is None:
            return _error("bad_request", "'to' must be YYYY-MM-DD", 400)
    else:
        to_date = from_date + timedelta(days=DEFAULT_RANGE_DAYS - 1)

    if from_date > to_date:
        return _error("bad_request", "'from' must be <= 'to'", 400)
    if (to_date - from_date).days >= MAX_RANGE_DAYS:
        return _error(
            "bad_request",
            f"range exceeds {MAX_RANGE_DAYS} days",
            400,
        )

    payload = build_schedule(
        master,
        from_date=from_date,
        to_date=to_date,
        now=dj_timezone.now(),
    )
    return JsonResponse(payload.to_dict())


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def availability_request(request: HttpRequest) -> HttpResponse:
    """M3 «Помечу как недоступно» — master proposes an off-time window.

    Spec quote (master-mobile §M3 line 458):

        «Mark unavailable / off-day: master taps empty slot → sheet
        `Помечу как недоступно` → confirmation → server marks slot
        blocked → owner notified (audit + bot DM)»

    Body:
      {
        "start": "<iso datetime>",
        "end":   "<iso datetime>",
        "reason_class": "vacation"|"sick"|"personal"|"other",
        "reason_text": ""  // optional, ≤200 chars
      }

    Returns 201 on success with the created request_id. Conflict
    against an existing approved exception → 400 «overlap». Invalid
    body → 400.

    Side effects:
      * Audit row: ``master.availability_change_requested``.
      * Event emit (same slug) for analytics fanout.
      * MAX DM to ``tenant.manager_chat_id`` post-commit. No-op when
        empty (degraded mode, matches reminder-escalation pattern).
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = request.tenant  # type: ignore[attr-defined]

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    start = _parse_iso_datetime(body.get("start") or "")
    end = _parse_iso_datetime(body.get("end") or "")
    if start is None or end is None:
        return _error("bad_request", "start + end must be ISO datetimes", 400)

    reason_class = (body.get("reason_class") or "").strip()
    reason_text = (body.get("reason_text") or "").strip()

    try:
        with transaction.atomic():
            req = request_availability_change(
                master,
                start=start,
                end=end,
                reason_class=reason_class,
                reason_text=reason_text,
                actor=bot_user,
            )
            payload = {
                "tenant_id": str(master.tenant_id),
                "master_id": str(master.id),
                "request_id": str(req.id),
                "bot_user_id": str(bot_user.id),
                "requested_start": (
                    req.requested_start.isoformat() if req.requested_start else None
                ),
                "requested_end": (req.requested_end.isoformat() if req.requested_end else None),
                "reason_class": req.reason_class,
            }
            write_audit(
                MASTER_AVAILABILITY_CHANGE_REQUESTED,
                target="scheduling.ScheduleChangeRequest",
                target_id=req.id,
                payload=payload,
                actor_id=bot_user.id,
            )
            emit(
                MASTER_AVAILABILITY_CHANGE_REQUESTED,
                properties=payload,
            )

            request_id = req.id
            transaction.on_commit(
                lambda: _maybe_send_manager_dm(
                    tenant=tenant,
                    master=master,
                    request_id=request_id,
                )
            )
    except AvailabilityRequestError as exc:
        return _error(exc.slug, exc.detail, 400)

    return JsonResponse(
        {
            "request_id": str(req.id),
            "status": req.status,
            "requested_start": (req.requested_start.isoformat() if req.requested_start else None),
            "requested_end": (req.requested_end.isoformat() if req.requested_end else None),
            "reason_class": req.reason_class,
            "created_at": req.created_at.isoformat(),
        },
        status=201,
    )


@require_http_methods(["GET"])
@require_master_init_data
def conversations_list(request: HttpRequest) -> HttpResponse:
    """M5 master conversations list (master-mobile §M5, PR Tier1.3).

    Spec quote (§M5 line 552):

        «Screen M5 — Master conversation list (their conversations only)»

    Spec quote (§M5 lines 608-614):

        «Master's card is **stripped down** vs admin's: ❌ No LTV /
        financial signal … ✅ Customer first name only … ✅ Last name
        as 1-letter initial»

    Query params (all optional):
      filter: "active" (default) | "all" | "resolved"
      search: substring on customer first name (case-insensitive)
      cursor: opaque base64-JSON signed token
      limit:  default 25, max 50

    Returns 200 with::

        {
          "items": [...],
          "section_counts": {...},
          "next_cursor": null | "<token>"
        }

    Read-only. No audit row, no event emit. Cross-master + cross-tenant
    isolation enforced by :func:`require_master_init_data` plus the
    explicit ``master_id`` + ``tenant_id`` filters in the service layer.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]

    raw_filter = (request.GET.get("filter") or "active").strip().lower()
    raw_search = (request.GET.get("search") or "").strip()
    raw_cursor = (request.GET.get("cursor") or "").strip() or None
    raw_limit = (request.GET.get("limit") or "").strip()
    if raw_limit:
        try:
            limit = int(raw_limit)
        except ValueError:
            return _error("bad_request", "'limit' must be an integer", 400)
    else:
        limit = CONVERSATIONS_DEFAULT_LIMIT
    if limit < 1 or limit > CONVERSATIONS_MAX_LIMIT:
        return _error(
            "bad_request",
            f"'limit' must be in 1..{CONVERSATIONS_MAX_LIMIT}",
            400,
        )

    try:
        response = list_master_conversations(
            master,
            filter=raw_filter,  # type: ignore[arg-type]
            search=raw_search,
            cursor=raw_cursor,
            limit=limit,
            now=dj_timezone.now(),
        )
    except ConversationsListError as exc:
        return _error(exc.slug, exc.detail, 400)

    return JsonResponse(response.to_dict())


# --- M6 conversation detail (PR M6.1) -------------------------------------


@require_http_methods(["GET"])
@require_master_init_data
def conversation_detail(request: HttpRequest, conversation_id: str) -> HttpResponse:
    """M6 master conversation detail (master-mobile §M6).

    Spec quote (§M6 lines 706-712):

        «When master taps «Отправить от себя» on a draft, the message
        renders to the customer as «Помощник: …». Same single assistant
        identity. Master's authorship is recorded in attribution
        metadata (``actor_type=master``, ``composed_by=master_id``)»

    Cross-master + cross-tenant isolation enforced by
    :func:`apps.master_api.services.conversation_detail._verify_master_involved`.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    try:
        response = get_conversation_detail(master, conversation_id)
    except ConversationDetailError as exc:
        return _error(exc.slug, exc.detail, exc.status)
    return JsonResponse(response.to_dict())


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def conversation_send_message(request: HttpRequest, conversation_id: str) -> HttpResponse:
    """M6 master compose endpoint (§M6 lines 668, 674).

    Stamps the message with attribution metadata
    ``{"actor_type": "master", "composed_by": <master_id>}`` so audit /
    admin views can distinguish bot-authored from master-authored text.

    Rejects HUMAN_LOCKED with 403 ``tier_locked``.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    content = body.get("content") or ""
    try:
        response = send_master_message(
            master,
            conversation_id,
            content=content,
            actor_bot_user=bot_user,
        )
    except ConversationDetailError as exc:
        return _error(exc.slug, exc.detail, exc.status)
    return JsonResponse(response.to_dict(), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def conversation_mark_read(request: HttpRequest, conversation_id: str) -> HttpResponse:
    """M6 mark-read endpoint — debounced (one audit row per call)."""

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        marked = mark_conversation_read(
            master,
            conversation_id,
            actor_bot_user=bot_user,
        )
    except ConversationDetailError as exc:
        return _error(exc.slug, exc.detail, exc.status)
    return JsonResponse({"marked_count": marked})


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def conversation_promote(request: HttpRequest, conversation_id: str) -> HttpResponse:
    """M6 safety promote — escalate to HUMAN_LOCKED (§M6 line 765).

    Returns 409 ``already_locked`` if the conversation is already
    locked; 400 on missing/invalid reason_class.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    reason_class = body.get("reason_class") or ""
    reason_text = body.get("reason_text") or ""
    try:
        response = promote_to_human_locked(
            master,
            conversation_id,
            reason_class=reason_class,
            reason_text=reason_text,
            actor_bot_user=bot_user,
        )
    except ConversationDetailError as exc:
        return _error(exc.slug, exc.detail, exc.status)
    return JsonResponse(response.to_dict())


# --- M6 AI drafts (Bundle B / item 4 backend) -----------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def conversation_draft_generate(request: HttpRequest, conversation_id: str) -> HttpResponse:
    """Generate a fresh AI draft for the master on this conversation.

    Spec quote (master-mobile §M6 lines 662-671):

        «✨ Предложенный ответ ... [Отправить от себя] [Отредактировать]
        [Пусть помощник ответит]»

    Body: empty (any JSON dict ignored — keeps the endpoint a pure
    «generate now» trigger).

    Status codes:
      200  — fresh draft (or idempotent re-serve within 60s window)
      400  — ``conversation_locked``: HUMAN_LOCKED tier
      404  — master not involved / conversation not found
      429  — ``rate_limit_exceeded`` (per-master 10/min, 100/day) /
             ``cost_cap_exceeded`` (per-master $X/day cumulative) /
             ``generate_in_flight`` (another concurrent generate holds
             the Conversation row lock; issue #550). All three include
             a ``Retry-After`` header.
      503  — ``llm_unavailable``: provider raised; refer to logs
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    # Blocker #2: per-master rate limit at the view layer — fail fast
    # BEFORE any service work so a rogue loop can't burn budget.
    rate = check_and_consume_rate_limit(master.id)
    if not rate.allowed:
        resp = _error(rate.slug, rate.detail, 429)
        if rate.retry_after_seconds > 0:
            resp["Retry-After"] = str(rate.retry_after_seconds)
        return resp

    try:
        response = generate_draft_for_conversation(
            conversation_id=conversation_id,
            master=master,
            actor_bot_user=bot_user,
        )
    except ConversationDetailError as exc:
        # Issue #550 + #551: ``generate_in_flight`` /
        # ``conversation_busy`` carry ``extra={"retry_after_seconds": 3}``
        # so the frontend gets a concrete hint how long «Помощник уже
        # думает…» should hold before re-enabling the tap. The shared
        # :func:`_draft_error_response` helper surfaces the field in
        # the JSON body AND sets the ``Retry-After`` header for clients
        # that don't read JSON shims.
        return _draft_error_response(exc)
    return JsonResponse(response.to_dict())


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def conversation_draft_send_as_me(
    request: HttpRequest, conversation_id: str, draft_id: str
) -> HttpResponse:
    """Send the draft text (or override) as a master-attributed message.

    Spec quote (master-mobile §M6 lines 706-712):

        «When master taps «Отправить от себя» on a draft, the message
        renders to the customer as «Помощник: …». Same single assistant
        identity. Master's authorship is recorded in attribution metadata
        (``actor_type=master``, ``composed_by=master_id``)»

    Body (optional):
      ``{"override_content": "edited text"}`` — the «Отредактировать»
      path. When present, replaces the draft's LLM text with the
      master's edited version. ≤ 2000 chars.

    Status codes:
      201  — message created; draft marked SENT_AS_MASTER
      400  — ``draft_already_acted`` (non-ACTIVE) / ``bad_request``
             (override too long or empty)
      403  — ``tier_locked``: HUMAN_LOCKED
      404  — master not involved / draft not found
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    override_content = body.get("override_content")
    if override_content is not None and not isinstance(override_content, str):
        return _error("bad_request", "override_content must be a string", 400)

    try:
        response = send_draft_as_master(
            conversation_id=conversation_id,
            draft_id=draft_id,
            master=master,
            actor_bot_user=bot_user,
            override_content=override_content,
        )
    except ConversationDetailError as exc:
        return _draft_error_response(exc)
    return JsonResponse(response.to_dict(), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@require_master_init_data
def conversation_draft_release_to_ai(
    request: HttpRequest, conversation_id: str, draft_id: str
) -> HttpResponse:
    """Let the AI auto-send the draft (no master attribution).

    Spec quote (master-mobile §M6 line 670):

        «[Пусть помощник ответит]  Releases to AI auto-send»

    Body: empty.

    Status codes:
      201  — message created; draft marked RELEASED_TO_AI
      400  — ``draft_already_acted``
      403  — ``tier_locked``
      404  — master not involved / draft not found
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        response = release_draft_to_ai(
            conversation_id=conversation_id,
            draft_id=draft_id,
            master=master,
            actor_bot_user=bot_user,
        )
    except ConversationDetailError as exc:
        return _draft_error_response(exc)
    return JsonResponse(response.to_dict(), status=201)


@require_http_methods(["GET"])
@require_master_init_data
def availability_pending(request: HttpRequest) -> HttpResponse:
    """M3 list of master's own pending + recently-decided requests.

    Spec quote (master-mobile §M3 line 476):

        «`GET /api/master/availability/pending` — list of pending
        availability changes»

    Filter: pending OR (decided within last 14 days). Ordered
    created_at desc. Read-only — no audit, no event emit.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    items = list_pending_requests(master, now=dj_timezone.now())
    return JsonResponse({"items": items})


# --- M7 notification preferences (Bundle B / item 3) -----------------------


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_master_init_data
def notification_prefs(request: HttpRequest) -> HttpResponse:
    """M7 per-master notification toggles + quiet-hours window.

    Spec quote (master-mobile §M7 line 835):

        «Settings persisted server-side (also writes to MAX bot
        subscription DB)»

    Spec quote (master-mobile §M7 line 805):

        «Срочно (HUMAN_LOCKED) … нельзя выключить»

    * ``GET``   — returns the master's prefs. First call lazily
      creates the row with §M7 defaults; second call returns the
      existing row without duplicate insert. No audit (read-only).
    * ``PATCH`` — partial update with a JSON body. Validation:
      - unknown keys → 400 ``bad_request``
      - non-bool toggle / non-``HH:MM`` time → 400 ``bad_request`` /
        ``time_invalid``
      - ``urgent=False`` → 400 ``urgent_forced_on``
      - quiet hours enabled + ``quiet_start == quiet_end`` → 400
        ``time_invalid``
      - quiet hours enabled + ``quiet_start > quiet_end`` is LEGAL
        (overnight window per §M7 default 21:00 → 09:00)
      Successful PATCH writes one ``master.notification_prefs_updated``
      audit row with a ``changes`` diff. No-op PATCH (caller sent
      only unchanged values) returns 200 without audit churn.

    The MAX bot subscription DB write hinted at by §835 is deferred —
    bot-platform does not own the out-of-band push channel; MAX has no
    push beyond chat per the platform capabilities memo.
    """

    master: CatalogMaster = request.master  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if request.method == "GET":
        prefs = get_or_create_prefs(master)
        return JsonResponse({"prefs": serialise_prefs(prefs)})

    # PATCH
    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    try:
        prefs = update_prefs(master, patch=body, actor=bot_user)
    except NotificationPrefsError as exc:
        return _error(exc.slug, exc.detail, 400)

    return JsonResponse({"prefs": serialise_prefs(prefs)})
