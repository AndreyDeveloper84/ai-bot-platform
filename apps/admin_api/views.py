"""Admin REST views — master roster CRUD (PR 2 / MM1-MM3).

Endpoints under ``/api/v1/admin/masters/``:

* ``GET    /``                — list (filters, search, cursor pagination)
* ``GET    /<master_id>/``    — detail (full + linked bot user + services)
* ``PATCH  /<master_id>/``    — edit name / bio / specialization / experience /
                                 is_active (last gated to Owner only)
* ``POST   /<master_id>/photo/`` — multipart photo upload
* ``GET    /<master_id>/audit/`` — audit feed for this master

All views are gated via :func:`apps.admin_api.auth.require_admin_role`.
Cross-tenant access is impossible by construction — the auth decorator
enters :func:`tenant_scope` so the default ``CatalogMaster.objects``
manager filters by ``current_tenant()``. Cross-tenant master ids fall
through to 404 (deliberate; surfacing 403 would leak existence).

### Audit slugs

Two new slugs registered in :mod:`apps.events.vocabulary`:

* ``master.profile_updated_by_admin`` — PATCH success.
* ``master.photo_updated_by_admin`` — photo upload success.

The PATCH payload includes ``fields_changed`` + ``previous_values`` so
forensic review can rebuild the diff. The photo payload carries
``size_bytes`` + ``mime`` (no file content).

### Scope cuts (separate PRs)

* MM2 invite create / resend / cancel — PR 3.
* MM4 services M2M editing — separate PR.
* MM5 deactivation cascade (booking reassignment, customer notify) —
  separate PR. This PR allows Owner to flip ``is_active=False`` but
  does **not** cascade (just sets the flag + writes audit).
* Master role change (master ↔ admin) — separate PR.
* Photo resize / S3 storage / CDN — TODO comments only; raw upload
  saved to ``MEDIA_ROOT/master_photos/``.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import RoleContext, require_admin_role
from apps.audit.models import AuditLog
from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.events.vocabulary import (
    MASTER_PHOTO_UPDATED_BY_ADMIN,
    MASTER_PROFILE_UPDATED_BY_ADMIN,
)
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


# --- constants ------------------------------------------------------------

DEFAULT_LIST_LIMIT = 25
MAX_LIST_LIMIT = 50

MAX_NAME_LEN = 200
MAX_SPECIALIZATION_LEN = 255
MAX_BIO_LEN = 1000
MAX_EXPERIENCE_LEN = 255

PHOTO_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_PHOTO_MIMES = ("image/jpeg", "image/png", "image/webp")
MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

PATCH_EDITABLE_FIELDS = {"name", "specialization", "bio", "experience", "is_active"}

# Audit feed pagination — separate constants to leave list-endpoint
# tuning independent.
AUDIT_DEFAULT_LIMIT = 25
AUDIT_MAX_LIMIT = 50


# --- helpers --------------------------------------------------------------


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


def _parse_bool_tristate(value: str | None) -> bool | None:
    """Tri-state filter: ``true`` / ``false`` / anything-else (default).

    Returns True / False on the literal strings; returns None on
    missing OR ``both`` OR any unrecognized value (caller treats that
    as 'no filter applied').
    """

    if not value:
        return None
    value = value.strip().lower()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    return None


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _encode_cursor(name: str, master_id: str) -> str:
    """Build an opaque cursor for the masters-list endpoint.

    Stable order = ``(-is_active, name asc, id asc)``. The cursor packs
    the last-seen ``(name, master_id)`` of the previous page so the
    next page resumes from there. Base64-encoded JSON makes it opaque
    to clients (they MUST NOT parse it) but trivially debuggable on
    the server.
    """

    raw = json.dumps({"n": name, "id": master_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        return str(data["n"]), str(data["id"])
    except (binascii.Error, ValueError, KeyError, UnicodeDecodeError):
        return None


def _encode_audit_cursor(created_at: datetime, log_id: str) -> str:
    raw = json.dumps({"t": created_at.isoformat(), "id": log_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_audit_cursor(cursor: str) -> tuple[datetime, str] | None:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        return datetime.fromisoformat(data["t"]), str(data["id"])
    except (binascii.Error, ValueError, KeyError, UnicodeDecodeError):
        return None


_PHONE_TAIL = __import__("re").compile(r"(\d{2})$")


def _mask_phone(phone: str) -> str:
    """E.164 phone → ``+• ••• ••• ••67`` (last 2 digits visible).

    Mirrors the helper in :mod:`apps.master_api.views`. Defensive:
    empty input → empty string; short input → ``•••``.
    """

    if not phone:
        return ""
    tail = _PHONE_TAIL.search(phone)
    if tail is None:
        return "•••"
    return f"+• ••• ••• ••{tail.group(1)}"


# --- list response builders ----------------------------------------------


def _row_to_list_item(master: CatalogMaster, services_count: int) -> dict[str, Any]:
    """Compact master payload for the MM1 roster row."""

    bot_user_last_seen: str | None = None
    if master.linked_bot_user_id is not None:
        # The linked_bot_user is already on master via select_related; we
        # don't want a per-row query. Annotating in the queryset feeds the
        # ``_bu_last_seen`` attr below.
        last_seen = getattr(master, "_bu_last_seen", None)
        if last_seen is not None:
            bot_user_last_seen = last_seen.isoformat()

    return {
        "id": str(master.id),
        "name": master.name,
        "specialization": master.specialization,
        "photo_url": master.photo_url,
        "is_active": master.is_active,
        "invite_status": master.invite_status,
        "last_seen_at": bot_user_last_seen,
        "services_count": services_count,
    }


def _detail_payload(master: CatalogMaster, *, include_audit: bool = False) -> dict[str, Any]:
    """Full master detail — all CatalogMaster fields except ``raw`` +
    linked BotUser (masked phone + handle) + services list + working-hours
    summary.

    ``include_audit`` is a convenience hook so future endpoints can
    embed a small recent-events list; PR 2 keeps audit in its own
    endpoint and leaves the flag at False.
    """

    linked_bot_user: dict[str, Any] | None = None
    if master.linked_bot_user_id is not None and master.linked_bot_user is not None:
        bu = master.linked_bot_user
        linked_bot_user = {
            "id": str(bu.id),
            "display_name": bu.display_name,
            "phone_masked": _mask_phone(bu.phone),
            "last_seen_at": bu.last_seen.isoformat() if bu.last_seen else None,
        }

    # Services list — read MasterService mapping (M2M editing is MM4 / a
    # later PR; we only READ here).
    service_ids = list(
        MasterService.all_tenants.filter(tenant=master.tenant_id, master=master).values_list(
            "service_id", flat=True
        )
    )
    services = list(
        CatalogService.all_tenants.filter(
            tenant=master.tenant_id, id__in=service_ids, is_active=True
        ).values("id", "name")
    )
    services_payload = [{"id": str(s["id"]), "name": s["name"]} for s in services]

    return {
        "id": str(master.id),
        "name": master.name,
        "specialization": master.specialization,
        "bio": master.bio,
        "experience": master.experience,
        "rating": str(master.rating) if master.rating is not None else None,
        "is_active": master.is_active,
        "invite_status": master.invite_status,
        "mode": master.mode,
        "photo_url": master.photo_url,
        "max_handle": master.max_handle,
        "yclients_staff_id": master.yclients_staff_id,
        "invited_at": master.invited_at.isoformat() if master.invited_at else None,
        "archived_at": master.archived_at.isoformat() if master.archived_at else None,
        "linked_bot_user": linked_bot_user,
        "services": services_payload,
        "working_hours_summary": _working_hours_summary(master),
    }


def _working_hours_summary(master: CatalogMaster) -> str:
    """Compact one-liner for the detail screen.

    Reads :class:`apps.scheduling.models.WorkingHours` when present.
    Phase 1 keeps this string-only — the proper structured payload
    lives in the Schedule Management API (separate handoff). When no
    rows exist (newly-invited master, no schedule seeded yet) we
    return the placeholder the master_api also uses.
    """

    try:
        from apps.scheduling.models import WorkingHours
    except ImportError:
        return "Расписание уточнит салон"

    rows = list(
        WorkingHours.all_tenants.filter(tenant=master.tenant_id, master=master).order_by(
            "day_of_week"
        )
    )
    if not rows:
        return "Расписание уточнит салон"

    working = [r for r in rows if r.is_working]
    if not working:
        return "Выходной"

    # Compact "Пн-Пт 10:00–19:00" — render the most common start/end pair.
    weekday_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    days = [weekday_short[r.day_of_week] for r in working if 0 <= r.day_of_week < 7]
    if not days:
        return "Расписание уточнит салон"
    first, last = working[0], working[-1]
    # mypy: TimeField may be None on the model; narrow before strftime.
    start_t = first.start_time
    end_t = last.end_time
    if start_t is None or end_t is None:
        return "Расписание уточнит салон"
    return f"{days[0]}-{days[-1]} {start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')}"


# --- GET /api/v1/admin/masters/ ------------------------------------------


@require_http_methods(["GET"])
@require_admin_role
def masters_list(request: HttpRequest) -> HttpResponse:
    """Roster list — filters, search, opaque cursor pagination.

    Query parameters (all optional):

    * ``is_active`` — ``true`` / ``false`` / anything else = no filter
    * ``invite_status`` — comma-separated list of
      ``pending`` / ``accepted`` / ``expired`` / ``cancelled``
    * ``search`` — substring match on ``name`` (case-insensitive,
      ``__icontains`` for RU support)
    * ``cursor`` — opaque cursor from previous page (``next_cursor``)
    * ``limit`` — int, max ``MAX_LIST_LIMIT`` (50), default
      ``DEFAULT_LIST_LIMIT`` (25)

    Response shape::

        {
          "items": [{
            "id", "name", "specialization", "photo_url",
            "is_active", "invite_status", "last_seen_at",
            "services_count"
          }, ...],
          "next_cursor": str | None,
          "total_count": int
        }

    Ordering is ``(-is_active, name, id)`` — active first, then alpha.
    ``services_count`` is annotated to avoid N+1.
    """

    tenant = request.tenant  # type: ignore[attr-defined]

    is_active = _parse_bool_tristate(request.GET.get("is_active"))
    invite_statuses = _split_csv(request.GET.get("invite_status"))
    search = (request.GET.get("search") or "").strip()
    cursor = request.GET.get("cursor", "")

    try:
        limit = int(request.GET.get("limit", DEFAULT_LIST_LIMIT))
    except ValueError:
        return _error("bad_request", "limit must be an integer", 400)
    if limit < 1:
        return _error("bad_request", "limit must be positive", 400)
    limit = min(limit, MAX_LIST_LIMIT)

    # Validate invite_status values — defence in depth + stable error.
    valid_statuses = set(CatalogMaster.InviteStatus.values)
    for s in invite_statuses:
        if s not in valid_statuses:
            return _error(
                "bad_request",
                f"invite_status {s!r} not in {sorted(valid_statuses)}",
                400,
            )

    qs = (
        CatalogMaster.objects.filter(tenant=tenant)
        .select_related("linked_bot_user")
        .annotate(
            _services_count=Count("services_offered", distinct=True),
        )
    )

    if is_active is True:
        qs = qs.filter(is_active=True)
    elif is_active is False:
        qs = qs.filter(is_active=False)
    if invite_statuses:
        qs = qs.filter(invite_status__in=invite_statuses)
    if search:
        qs = qs.filter(name__icontains=search)

    # Total before cursor slicing — gives the UI the unfiltered-by-page
    # "found N matching" count (still scoped to tenant + filters).
    total_count = qs.count()

    # Stable order. ``-is_active`` is Postgres-correct (True > False);
    # paired with name+id it's also the cursor's tie-breaker.
    qs = qs.order_by("-is_active", "name", "id")

    if cursor:
        decoded = _decode_cursor(cursor)
        if decoded is None:
            return _error("bad_request", "invalid cursor", 400)
        last_name, last_id = decoded
        # Cursor advances within the "active-first, name asc" partition.
        # We page within the same is_active value to keep the boundary
        # tight; the user can't cross the active/inactive split with one
        # cursor (cleaner than juggling tri-state ordering compares).
        qs = qs.filter(Q(name__gt=last_name) | (Q(name=last_name) & Q(id__gt=last_id)))

    # Fetch limit+1 to compute next_cursor without a second query.
    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    # Stitch linked_bot_user's last_seen onto the row for the response.
    for row in rows:
        if row.linked_bot_user_id is not None and row.linked_bot_user is not None:
            row._bu_last_seen = row.linked_bot_user.last_seen  # type: ignore[attr-defined]

    items = [_row_to_list_item(row, row._services_count) for row in rows]  # type: ignore[attr-defined]
    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encode_cursor(last.name, str(last.id))

    return JsonResponse(
        {
            "items": items,
            "next_cursor": next_cursor,
            "total_count": total_count,
        }
    )


# --- GET /api/v1/admin/masters/<master_id>/ -------------------------------


def _get_master_or_404(tenant_id: Any, master_id: str) -> CatalogMaster | None:
    """Tenant-safe master fetch. Returns None for cross-tenant + bad UUID."""

    import uuid

    try:
        mid = uuid.UUID(master_id)
    except (TypeError, ValueError):
        return None
    return (
        CatalogMaster.objects.filter(tenant=tenant_id, id=mid)
        .select_related("linked_bot_user")
        .first()
    )


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_admin_role
def master_detail(request: HttpRequest, master_id: str) -> HttpResponse:
    """Dispatch GET (read) vs PATCH (edit) on ``/masters/<id>/``.

    GET → :func:`_master_detail_get`. PATCH → :func:`_master_update`.
    Kept under one URL so the spec's contract holds:
    ``GET /api/v1/admin/masters/<id>/`` and
    ``PATCH /api/v1/admin/masters/<id>/`` share the path.
    """

    if request.method == "PATCH":
        return _master_update(request, master_id)
    return _master_detail_get(request, master_id)


def _master_detail_get(request: HttpRequest, master_id: str) -> HttpResponse:
    """Full master detail. 404 on bad UUID, missing, or cross-tenant."""

    tenant = request.tenant  # type: ignore[attr-defined]
    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)
    return JsonResponse({"master": _detail_payload(master)})


# --- PATCH /api/v1/admin/masters/<master_id>/ -----------------------------


def _master_update(request: HttpRequest, master_id: str) -> HttpResponse:
    """Edit name / specialization / bio / experience / is_active.

    Validation:

    * ``name`` — required-non-blank when provided, ≤ 200 chars.
    * ``specialization`` — ≤ 255 chars (blank OK).
    * ``bio`` — ≤ 1000 chars.
    * ``experience`` — ≤ 255 chars.
    * ``is_active`` — boolean. **Owner-only** to set False. Both
      Owner and Admin can set True (reactivation is benign).

    Fields NOT editable here: ``tenant``, ``yclients_staff_id``,
    ``invite_status``, ``linked_bot_user``, ``archived_at``,
    ``max_handle``, ``photo_url``. Those have dedicated endpoints
    (or live in MM2/MM4/MM5 PRs).

    Side effects:

    * Atomic with ``transaction.atomic``. No ``select_for_update`` —
      we follow CatalogMaster's existing concurrent-edit pattern
      (last-write-wins; the editing UI handles conflicts at the
      banner level).
    * Audit slug ``master.profile_updated_by_admin`` with payload
      ``{master_id, actor_role, fields_changed, previous_values}``.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    if not body:
        return _error("bad_request", "request body is required", 400)

    # Reject unknown fields outright — better than silently ignoring.
    unknown = set(body.keys()) - PATCH_EDITABLE_FIELDS
    if unknown:
        return _error(
            "bad_request",
            f"fields not editable here: {sorted(unknown)}",
            400,
        )

    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    # --- field-level validation ---
    if "name" in body:
        name = body["name"]
        if not isinstance(name, str):
            return _error("bad_request", "name must be a string", 400)
        if not name.strip():
            return _error("bad_request", "name cannot be blank", 400)
        if len(name) > MAX_NAME_LEN:
            return _error("bad_request", f"name exceeds {MAX_NAME_LEN} chars", 400)

    if "specialization" in body:
        spec = body["specialization"]
        if not isinstance(spec, str):
            return _error("bad_request", "specialization must be a string", 400)
        if len(spec) > MAX_SPECIALIZATION_LEN:
            return _error(
                "bad_request",
                f"specialization exceeds {MAX_SPECIALIZATION_LEN} chars",
                400,
            )

    if "bio" in body:
        bio = body["bio"]
        if not isinstance(bio, str):
            return _error("bad_request", "bio must be a string", 400)
        if len(bio) > MAX_BIO_LEN:
            return _error("bad_request", f"bio exceeds {MAX_BIO_LEN} chars", 400)

    if "experience" in body:
        exp = body["experience"]
        if not isinstance(exp, str):
            return _error("bad_request", "experience must be a string", 400)
        if len(exp) > MAX_EXPERIENCE_LEN:
            return _error(
                "bad_request",
                f"experience exceeds {MAX_EXPERIENCE_LEN} chars",
                400,
            )

    if "is_active" in body:
        is_active = body["is_active"]
        if not isinstance(is_active, bool):
            return _error("bad_request", "is_active must be boolean", 400)
        # Owner-only deactivation gate — see MM3 §"Permissions per field"
        # row "Deactivate: Owner ✓, Admin ❌". Reactivation (False→True)
        # is benign and allowed for Admin.
        if is_active is False and not role_ctx.is_owner:
            return _error(
                "forbidden",
                "only the salon owner can deactivate a master",
                403,
            )

    # --- compute diff for audit + apply ---
    fields_changed: list[str] = []
    previous_values: dict[str, Any] = {}
    update_fields: list[str] = []

    for field in ("name", "specialization", "bio", "experience"):
        if field in body:
            new_value = body[field]
            old_value = getattr(master, field)
            if new_value != old_value:
                previous_values[field] = old_value
                fields_changed.append(field)
                setattr(master, field, new_value)
                update_fields.append(field)

    if "is_active" in body and body["is_active"] != master.is_active:
        previous_values["is_active"] = master.is_active
        fields_changed.append("is_active")
        master.is_active = body["is_active"]
        update_fields.append("is_active")

    if update_fields:
        with transaction.atomic():
            master.save(update_fields=update_fields)
            write_audit(
                MASTER_PROFILE_UPDATED_BY_ADMIN,
                target="catalog.CatalogMaster",
                target_id=master.id,
                payload={
                    "master_id": str(master.id),
                    "actor_role": role_ctx.primary_role,
                    "fields_changed": fields_changed,
                    "previous_values": previous_values,
                },
                actor_id=bot_user.id,
            )

    # Re-fetch to get linked_bot_user populated cleanly after save.
    master = _get_master_or_404(tenant.id, master_id)
    assert master is not None  # we just saved it
    return JsonResponse({"master": _detail_payload(master)})


# --- POST /api/v1/admin/masters/<master_id>/photo/ -----------------------


def _save_photo_to_media(master: CatalogMaster, file_obj: Any, mime: str) -> tuple[str, int]:
    """Write the upload to ``MEDIA_ROOT/master_photos/`` and return URL + bytes.

    Phase 1: raw upload only — no Pillow, no resize, no S3, no CDN.
    The file name is ``<master_id>.<ext>``. Overwriting an existing
    photo is intentional (this is the admin edit path; one photo per
    master). TODO(media-pipeline): resize to 800×800 + thumbnail in a
    follow-up PR; route uploads through a signed-URL flow for S3.
    """

    ext = MIME_TO_EXT.get(mime, ".jpg")
    media_root = Path(getattr(settings, "MEDIA_ROOT", "media"))
    media_url = getattr(settings, "MEDIA_URL", "/media/")
    photos_dir = media_root / "master_photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    out_path = photos_dir / f"{master.id}{ext}"
    written = 0
    with open(out_path, "wb") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)
            written += len(chunk)

    return f"{media_url.rstrip('/')}/master_photos/{master.id}{ext}", written


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_photo_upload(request: HttpRequest, master_id: str) -> HttpResponse:
    """Multipart photo upload — JPEG / PNG / WebP only, ≤ 10 MB.

    Body: ``multipart/form-data`` with a single file field ``photo``.

    Validation:

    * Content-Type must be one of ``image/jpeg``, ``image/png``,
      ``image/webp``. The server trusts the multipart-stated MIME for
      Phase 1; content-sniffing lands with the media-pipeline PR.
    * Size ≤ 10 MB. Frontend pre-checks but we double-check here.

    Side effects:

    * File saved to ``MEDIA_ROOT/master_photos/<master_id>.<ext>``.
    * ``CatalogMaster.photo_url`` updated to the public URL.
    * Audit slug ``master.photo_updated_by_admin`` with payload
      ``{master_id, actor_role, size_bytes, mime}``.

    Response: ``{"photo_url": "<url>"}``.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    file_obj = request.FILES.get("photo")
    if file_obj is None:
        return _error("bad_request", "photo field is required", 400)

    mime = (file_obj.content_type or "").lower()
    if mime not in ALLOWED_PHOTO_MIMES:
        return _error(
            "bad_request",
            f"unsupported image type {mime!r}; use JPEG/PNG/WebP",
            400,
        )

    size = file_obj.size or 0
    if size > PHOTO_MAX_BYTES:
        return _error(
            "bad_request",
            f"image exceeds {PHOTO_MAX_BYTES} bytes ({size} given)",
            400,
        )

    with transaction.atomic():
        photo_url, written = _save_photo_to_media(master, file_obj, mime)
        master.photo_url = photo_url
        master.save(update_fields=["photo_url"])
        write_audit(
            MASTER_PHOTO_UPDATED_BY_ADMIN,
            target="catalog.CatalogMaster",
            target_id=master.id,
            payload={
                "master_id": str(master.id),
                "actor_role": role_ctx.primary_role,
                "size_bytes": written,
                "mime": mime,
            },
            actor_id=bot_user.id,
        )

    return JsonResponse({"photo_url": photo_url})


# --- GET /api/v1/admin/masters/<master_id>/audit/ ------------------------


@require_http_methods(["GET"])
@require_admin_role
def master_audit_feed(request: HttpRequest, master_id: str) -> HttpResponse:
    """Recent audit events relevant to this master.

    Filter strategy: matches an :class:`AuditLog` row when either:

    * ``payload->>'master_id' == <master_id>``, **or**
    * ``target == 'catalog.CatalogMaster' AND target_id == <master_id>``.

    The dual filter covers both the M0 onboarding rows (which use
    ``target='catalog.CatalogMaster' + target_id=master.id``) AND any
    future emitters that only carry ``master_id`` in the payload.
    The two predicates UNION naturally via Django's ``Q``.

    Ordering: ``-created_at`` (most recent first). Pagination via opaque
    cursor packing ``(created_at, log_id)``.

    Cross-tenant isolation: the auth decorator entered ``tenant_scope``,
    so the default ``AuditLog.objects`` manager filters by current
    tenant. Plus we re-filter by tenant explicitly as defence in depth.
    """

    tenant = request.tenant  # type: ignore[attr-defined]

    master = _get_master_or_404(tenant.id, master_id)
    if master is None:
        return _error("not_found", "master not found", 404)

    try:
        limit = int(request.GET.get("limit", AUDIT_DEFAULT_LIMIT))
    except ValueError:
        return _error("bad_request", "limit must be an integer", 400)
    if limit < 1:
        return _error("bad_request", "limit must be positive", 400)
    limit = min(limit, AUDIT_MAX_LIMIT)

    cursor = request.GET.get("cursor", "")

    master_id_str = str(master.id)
    qs = (
        AuditLog.all_tenants.filter(tenant=tenant)
        .filter(
            Q(payload__master_id=master_id_str)
            | Q(target="catalog.CatalogMaster", target_id=master.id)
        )
        .order_by("-created_at", "-id")
    )

    if cursor:
        decoded = _decode_audit_cursor(cursor)
        if decoded is None:
            return _error("bad_request", "invalid cursor", 400)
        last_created, last_id = decoded
        # Strictly older than the cursor's (created_at, id) pair.
        qs = qs.filter(
            Q(created_at__lt=last_created) | (Q(created_at=last_created) & Q(id__lt=last_id))
        )

    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items = [
        {
            "id": str(row.id),
            "action": row.action,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "actor_role": row.payload.get("actor_role"),
            "occurred_at": row.created_at.isoformat(),
            "payload": row.payload,
        }
        for row in rows
    ]
    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encode_audit_cursor(last.created_at, str(last.id))

    return JsonResponse({"items": items, "next_cursor": next_cursor})


__all__ = [
    "master_audit_feed",
    "master_detail",
    "master_photo_upload",
    "masters_list",
]


# ``timezone`` import is kept for future timestamp comparisons (e.g. an
# inactive-since filter); silence ruff with an explicit reference.
_ = timezone
