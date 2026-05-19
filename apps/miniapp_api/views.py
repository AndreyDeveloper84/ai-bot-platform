"""Customer Mini App HTTP views — Phase 0b.

Endpoints under ``/api/v1/customer/`` consumed by the MAX Mini App
(``apps/miniapp/`` — Phase 0c). Every endpoint requires a verified
``Authorization: MaxInitData <raw>`` header; the :func:`require_init_data`
decorator resolves the calling :class:`BotUser` + :class:`Tenant` and
attaches them to ``request`` for the view body.

Endpoints
---------

* ``POST /auth/verify`` — round-trip the initData and return the
  resolved identity (debugging + bootstrap).
* ``GET /slots?master_id&service_id&date_from&date_to`` — list free
  slots in the date range, given the catalog master + service.

Both endpoints return JSON. Errors use the shape
``{"error": "<slug>", "detail": "<message>"}`` with semantically-mapped
HTTP statuses (401 unauthorized, 400 bad request, 404 not found).

Tenant context
--------------

After auth resolution we enter :func:`apps.tenancy.context.tenant_scope`
for the duration of the view, so ORM queries that use
:class:`TenantScopedManager` automatically scope to the right tenant.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timedelta
from functools import wraps
from typing import Any, Callable
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.miniapp_api.auth import (
    InitDataBadSignature,
    InitDataError,
    InitDataMalformed,
    InitDataNotConfigured,
    InitDataStale,
    VerifiedInitData,
    extract_init_data,
    verify_init_data,
)
from apps.scheduling.services.resolver import (
    collect_time_block_intervals,
    compute_free_slots,
    get_slot_config,
    resolve_working_blocks,
)
from apps.tenancy.context import tenant_scope

logger = logging.getLogger(__name__)


# --- auth decorator --------------------------------------------------------


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def require_init_data(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Decorator: verify ``Authorization: MaxInitData …`` and resolve identity.

    On success, attaches to the request:

    * ``request.verified_init_data`` — :class:`VerifiedInitData`
    * ``request.bot_user`` — :class:`BotUser` matching the user.id
    * ``request.tenant`` — the :class:`Tenant` owning the BotUser

    Then enters :func:`tenant_scope` for the duration of the view call.
    On failure: 401 (bad signature / stale / not configured), 400
    (malformed), 404 (user not yet registered with any tenant).
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        header = request.headers.get("Authorization", "")
        try:
            raw = extract_init_data(header)
            verified = verify_init_data(raw)
        except InitDataNotConfigured:
            logger.error("miniapp_api.auth.not_configured")
            return _error("server_misconfigured", "MAX bot token not configured", 500)
        except InitDataBadSignature:
            return _error("bad_signature", "initData signature mismatch", 401)
        except InitDataStale:
            return _error("stale", "initData expired — reopen the Mini App", 401)
        except InitDataMalformed as exc:
            return _error("malformed", str(exc), 400)
        except InitDataError as exc:  # safety net
            return _error("unauthorized", str(exc), 401)

        bot_user = (
            BotUser.all_tenants.filter(
                channel="max",
                channel_user_id=verified.user_id,
                deleted_at__isnull=True,
            )
            .select_related("tenant")
            .order_by("-last_seen")
            .first()
        )
        if bot_user is None:
            return _error(
                "user_not_registered",
                "first interact with the bot before opening the Mini App",
                404,
            )

        request.verified_init_data = verified  # type: ignore[attr-defined]
        request.bot_user = bot_user  # type: ignore[attr-defined]
        request.tenant = bot_user.tenant  # type: ignore[attr-defined]
        with tenant_scope(bot_user.tenant):
            return view_func(request, *args, **kwargs)

    return wrapper


# --- /auth/verify ----------------------------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def auth_verify(request: HttpRequest) -> HttpResponse:
    """Return the resolved identity for the calling Mini App user.

    The Mini App calls this once on launch as a "who am I" probe and
    to surface configuration errors early. The response carries only
    non-sensitive fields; PII like phone is NOT returned here even if
    the BotUser has it on file (the profile endpoint, Phase 3, is the
    authorized surface for that).
    """

    verified: VerifiedInitData = request.verified_init_data  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    return JsonResponse(
        {
            "user": {
                "id": str(bot_user.id),
                "channel_user_id": bot_user.channel_user_id,
                "display_name": bot_user.display_name or verified.user.get("first_name", ""),
                "client_name": bot_user.client_name,
            },
            "tenant": {
                "slug": bot_user.tenant.slug,
                "name": bot_user.tenant.name,
                "timezone": bot_user.tenant.timezone,
            },
        }
    )


# --- /slots ----------------------------------------------------------------


MAX_SLOT_DATE_RANGE_DAYS = 14
"""Hard cap on the date_from..date_to window. Prevents pathological queries."""

DEFAULT_OCCUPIED_DURATION_MIN = 60
"""Fallback occupancy length when a booking row's ``duration_min`` is NULL.

Pre-4a backfilled rows have NULL ``duration_min`` (the booking was
created when only ``BookingRequest.service_name`` string was captured).
60 min is the conservative default — slightly over-blocks but never
under-blocks.
"""


def _parse_date(s: str | None) -> date_cls | None:
    if not s:
        return None
    try:
        return date_cls.fromisoformat(s)
    except ValueError:
        return None


def _collect_occupied(
    *,
    tenant_id: Any,
    master_id: Any,
    date_from: date_cls,
    date_to: date_cls,
    tz: ZoneInfo,
) -> list[tuple[datetime, datetime]]:
    """Build the occupied-intervals list from active BookingRequests.

    Reads :class:`apps.booking.models.BookingRequest` directly now that
    4a adds ``visit_at`` + ``duration_min`` on the booking itself.
    Pre-4a rows with NULL ``visit_at`` are skipped — they can't be
    placed on the calendar anyway.

    Includes only ``status=CONFIRMED`` rows; cancelled/rescheduled ones
    free their slots back to the resolver.
    """

    from apps.booking.models import BookingRequest

    window_start_local = datetime.combine(date_from, datetime.min.time(), tzinfo=tz)
    window_end_local = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    qs = BookingRequest.all_tenants.filter(
        tenant_id=tenant_id,
        master_id=master_id,
        status=BookingRequest.Status.CONFIRMED,
        visit_at__isnull=False,
        visit_at__gte=window_start_local,
        visit_at__lt=window_end_local,
    ).values_list("visit_at", "duration_min")

    intervals: list[tuple[datetime, datetime]] = []
    for visit_at, duration_min in qs:
        if visit_at is None:  # belt-and-suspenders — filter already excludes
            continue
        minutes = duration_min if duration_min else DEFAULT_OCCUPIED_DURATION_MIN
        intervals.append((visit_at, visit_at + timedelta(minutes=minutes)))
    return intervals


@require_http_methods(["GET"])
@require_init_data
def slots(request: HttpRequest) -> HttpResponse:
    """List free slot starts for ``master_id`` × ``service_id`` over a date range.

    Query parameters
    ----------------
    master_id : UUID (required) — :class:`CatalogMaster.id`
    service_id : UUID (required) — :class:`CatalogService.id`
    date_from : ISO 8601 date (required)
    date_to : ISO 8601 date (required, ≤ date_from + 14 days)

    Returns
    -------
    ``{"slots": [{"date": "2026-05-18", "start": "2026-05-18T10:00:00+03:00"}, …]}``

    A flat array — the frontend groups by date for the picker UI.
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = bot_user.tenant
    tz = ZoneInfo(tenant.timezone)

    master_id = request.GET.get("master_id", "")
    service_id = request.GET.get("service_id", "")
    date_from = _parse_date(request.GET.get("date_from"))
    date_to = _parse_date(request.GET.get("date_to"))

    if not master_id or not service_id:
        return _error("bad_request", "master_id and service_id are required", 400)
    if date_from is None or date_to is None:
        return _error("bad_request", "date_from and date_to (ISO 8601) are required", 400)
    if date_to < date_from:
        return _error("bad_request", "date_to must be >= date_from", 400)
    if (date_to - date_from).days > MAX_SLOT_DATE_RANGE_DAYS:
        return _error(
            "bad_request",
            f"window exceeds {MAX_SLOT_DATE_RANGE_DAYS} days",
            400,
        )

    # Per master-management handoff: only is_active=True AND
    # invite_status='accepted' masters are bookable from customer surfaces.
    try:
        master = CatalogMaster.objects.bookable().get(id=master_id)
    except CatalogMaster.DoesNotExist:
        return _error("not_found", "master not found or not bookable", 404)

    try:
        service = CatalogService.objects.get(id=service_id, is_active=True)
    except CatalogService.DoesNotExist:
        return _error("not_found", "service not found", 404)

    if not service.duration_min:
        return _error(
            "service_unbookable",
            "service has no duration configured",
            409,
        )

    # Per master-management handoff §MM4: customer can book a master
    # for a service only if the (master, service) mapping exists.
    if not MasterService.objects.filter(master_id=master.id, service_id=service.id).exists():
        return _error(
            "not_found",
            "master does not perform this service",
            404,
        )

    config = get_slot_config(tenant)

    # Clamp date_to to tenant's max_advance_days policy.
    today_local = timezone.now().astimezone(tz).date()
    advance_cap = today_local + timedelta(days=config.max_advance_days)
    if date_to > advance_cap:
        date_to = advance_cap
        if date_to < date_from:
            return JsonResponse({"slots": []})

    booking_occupied = _collect_occupied(
        tenant_id=tenant.id,
        master_id=master.id,
        date_from=date_from,
        date_to=date_to,
        tz=tz,
    )
    block_occupied = collect_time_block_intervals(
        tenant=tenant,
        master=master,
        date_from=date_from,
        date_to=date_to,
        tz=tz,
    )
    occupied = booking_occupied + block_occupied

    now = timezone.now()
    out: list[dict[str, str]] = []
    current = date_from
    while current <= date_to:
        blocks = resolve_working_blocks(tenant=tenant, master=master, on_date=current)
        if blocks:
            free = compute_free_slots(
                working_blocks=blocks,
                occupied=occupied,
                service_duration_min=int(service.duration_min),
                on_date=current,
                tz=tz,
                config=config,
                now=now,
            )
            for slot in free:
                out.append(
                    {
                        "date": current.isoformat(),
                        "start": slot.start.isoformat(),
                    }
                )
        current += timedelta(days=1)

    return JsonResponse({"slots": out})


# --- catalog read endpoints (4a) -------------------------------------------


def _service_to_dict(s: CatalogService) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "slug": s.slug,
        "name": s.name,
        "short_description": s.short_description,
        "description": s.description,
        "price_from": str(s.price_from) if s.price_from is not None else None,
        "duration_min": s.duration_min,
        "is_popular": s.is_popular,
        "contraindications": s.contraindications,
    }


def _master_to_dict(m: CatalogMaster) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "name": m.name,
        "specialization": m.specialization,
        "bio": m.bio,
        "experience": m.experience,
        "rating": str(m.rating) if m.rating is not None else None,
        "photo_url": m.photo_url,
    }


@require_http_methods(["GET"])
@require_init_data
def services_list(request: HttpRequest) -> HttpResponse:
    """List active CatalogService rows for the tenant.

    Customer-handoff §F1 — catalog screen consumes this.
    Returns all active services in one shot (typical salon: 10–40
    services; no pagination needed at this scale).
    """

    qs = CatalogService.objects.filter(is_active=True).order_by("name")
    return JsonResponse({"services": [_service_to_dict(s) for s in qs]})


@require_http_methods(["GET"])
@require_init_data
def service_detail(request: HttpRequest, service_id: str) -> HttpResponse:
    try:
        service = CatalogService.objects.get(id=service_id, is_active=True)
    except CatalogService.DoesNotExist:
        return _error("not_found", "service not found", 404)
    return JsonResponse({"service": _service_to_dict(service)})


@require_http_methods(["GET"])
@require_init_data
def masters_list(request: HttpRequest) -> HttpResponse:
    """List bookable masters; optionally filter by service.

    Customer-handoff §F2 — masters browse + booking-flow master picker.

    Query
    -----
    ``?service_id=<uuid>`` — only return masters who perform this
    service (via the MasterService mapping). When omitted, returns
    all bookable masters in the tenant.
    """

    qs = CatalogMaster.objects.bookable().order_by("name")
    service_id = request.GET.get("service_id")
    if service_id:
        # Existence join via MasterService. Filter via FK lookup so
        # Django coerces the string UUID; raw service_id= would fail
        # mypy strict UUID type check.
        master_ids = MasterService.objects.filter(service__id=service_id).values_list(
            "master_id", flat=True
        )
        qs = qs.filter(id__in=list(master_ids))
    return JsonResponse({"masters": [_master_to_dict(m) for m in qs]})


@require_http_methods(["GET"])
@require_init_data
def master_detail(request: HttpRequest, master_id: str) -> HttpResponse:
    try:
        master = CatalogMaster.objects.bookable().get(id=master_id)
    except CatalogMaster.DoesNotExist:
        return _error("not_found", "master not found or not bookable", 404)

    # Include the service IDs this master performs so the Mini App can
    # disable services the master doesn't offer.
    service_ids = [
        str(sid)
        for sid in MasterService.objects.filter(master_id=master.id).values_list(
            "service_id", flat=True
        )
    ]
    payload = _master_to_dict(master)
    payload["service_ids"] = service_ids
    return JsonResponse({"master": payload})


# --- POST /bookings (4a) ---------------------------------------------------


def _parse_iso_datetime(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # `fromisoformat` accepts the "Z" suffix from Python 3.11+.
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


_ERROR_SLUG_TO_STATUS = {
    "service_not_found": 404,
    "master_not_bookable": 404,
    "service_not_offered": 404,
    "service_unbookable": 409,
    "visit_in_past": 400,
    "slot_unavailable": 409,
    "master_archived": 409,
    "tenant_mismatch": 403,
    # Phase 2 reschedule slugs
    "not_found": 404,
    "forbidden": 403,
    "not_reschedulable": 409,
    "legacy_row": 409,
}


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def create_booking(request: HttpRequest) -> HttpResponse:
    """Thin view: parses body, delegates to
    :func:`apps.booking.services.create.create_customer_booking`,
    maps :class:`BookingCreateError.slug` to HTTP status.

    The atomic + race-safety logic lives in the service layer per 4a
    hardening review (select_for_update inside transaction, partial
    unique index as DB backstop, booking.created event emit).
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = bot_user.tenant

    import json

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)

    service_id = body.get("service_id") or ""
    master_id = body.get("master_id") or ""
    visit_at = _parse_iso_datetime(body.get("visit_at"))

    if not service_id or not master_id or visit_at is None:
        return _error(
            "bad_request",
            "service_id, master_id and visit_at (ISO 8601) are required",
            400,
        )

    from apps.booking.services.create import (
        BookingCreateError,
        CreateBookingInput,
        create_customer_booking,
    )

    correlation_id = request.headers.get("X-Correlation-Id", "")

    try:
        booking = create_customer_booking(
            inp=CreateBookingInput(
                tenant=tenant,
                bot_user=bot_user,
                service_id=service_id,
                master_id=master_id,
                visit_at=visit_at,
            ),
            correlation_id=correlation_id or None,
        )
    except BookingCreateError as exc:
        return _error(exc.slug, exc.detail, _ERROR_SLUG_TO_STATUS.get(exc.slug, 400))

    return JsonResponse(
        {
            "booking": {
                "id": str(booking.id),
                "service_name": booking.service_name,
                "master_name": booking.master_name,
                "visit_at": booking.visit_at.isoformat() if booking.visit_at else "",
                "duration_min": booking.duration_min,
                "status": booking.status,
            }
        },
        status=201,
    )


# --- Phase 2: visits + reschedule -----------------------------------------


def _visit_to_dict(b, *, now=None) -> dict[str, Any]:
    """Serialise a BookingRequest for the customer's visit list.

    `now` is injected so the visits_list view can compute `can_rate`
    against a single wall-clock for the whole batch.
    """
    moment = now or timezone.now()
    is_past = bool(b.visit_at and b.visit_at < moment)
    can_rate = is_past and b.status == "confirmed" and b.rating is None
    return {
        "id": str(b.id),
        "service_name": b.service_name,
        "master_name": b.master_name,
        "visit_at": b.visit_at.isoformat() if b.visit_at else None,
        "duration_min": b.duration_min,
        "status": b.status,
        "service_id": str(b.service_id) if b.service_id else None,
        "master_id": str(b.master_id) if b.master_id else None,
        # Phase 4 — F5 rating exposure
        "rating": b.rating,
        "can_rate": can_rate,
    }


@require_http_methods(["GET"])
@require_init_data
def visits_list(request: HttpRequest) -> HttpResponse:
    """List the calling customer's bookings.

    Per customer-handoff §12 F3 — three tabs (upcoming / past / all).
    Query ``?status=upcoming|past|all`` (default ``upcoming``).

    Rules:
    * Scoped to ``bot_user`` (customer sees only own bookings).
    * Past = ``visit_at < now`` regardless of status; upcoming =
      ``visit_at >= now AND status=CONFIRMED``.
    * Returned sorted: upcoming asc by visit_at, past desc.
    """

    from apps.booking.models import BookingRequest

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = bot_user.tenant
    status_filter = request.GET.get("status", "upcoming")

    qs = BookingRequest.all_tenants.filter(
        tenant_id=tenant.id,
        bot_user_id=bot_user.id,
        visit_at__isnull=False,
    )

    now = timezone.now()
    if status_filter == "upcoming":
        qs = qs.filter(visit_at__gte=now, status=BookingRequest.Status.CONFIRMED).order_by(
            "visit_at"
        )
    elif status_filter == "past":
        qs = qs.filter(visit_at__lt=now).order_by("-visit_at")
    elif status_filter == "all":
        qs = qs.order_by("-visit_at")
    else:
        return _error("bad_request", "status must be one of upcoming/past/all", 400)

    return JsonResponse({"visits": [_visit_to_dict(b, now=now) for b in qs]})


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def reschedule_booking(request: HttpRequest, booking_id) -> HttpResponse:
    """Reschedule customer's existing booking.

    Body: ``{"visit_at": "<new ISO 8601>"}``. Returns 201 with the NEW
    booking. The old row gets ``status=RESCHEDULED`` (terminal) per
    customer-handoff §11 + Q12-α (new row is ai_direct +
    created_by=execute_reschedule → billable=False).
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = bot_user.tenant

    import json

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)

    new_visit_at = _parse_iso_datetime(body.get("visit_at"))
    if new_visit_at is None:
        return _error("bad_request", "visit_at (ISO 8601) is required", 400)

    from apps.booking.services.create import BookingCreateError
    from apps.booking.services.reschedule import reschedule_customer_booking

    correlation_id = request.headers.get("X-Correlation-Id", "")

    try:
        booking = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(booking_id),
            new_visit_at=new_visit_at,
            correlation_id=correlation_id or None,
        )
    except BookingCreateError as exc:
        return _error(exc.slug, exc.detail, _ERROR_SLUG_TO_STATUS.get(exc.slug, 400))

    return JsonResponse(
        {
            "booking": {
                "id": str(booking.id),
                "service_name": booking.service_name,
                "master_name": booking.master_name,
                "visit_at": booking.visit_at.isoformat() if booking.visit_at else "",
                "duration_min": booking.duration_min,
                "status": booking.status,
            }
        },
        status=201,
    )


# --- /me — profile read / update / delete (Phase 3 / F4) -------------------


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_init_data
def me(request: HttpRequest) -> HttpResponse:
    """Read or partially update the current customer's profile.

    GET — returns the F4 snapshot (BotUser core + UserPreferences +
    favorites). PATCH — applies allowed field updates; unknown keys 400.
    """
    from apps.identity.services.profile import (
        ProfileUpdateError,
        get_profile,
        update_profile,
    )

    import json

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if request.method == "GET":
        snap = get_profile(bot_user)
        return JsonResponse(_profile_to_dict(snap))

    # PATCH
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    try:
        snap = update_profile(bot_user, body)
    except ProfileUpdateError as exc:
        return _error("invalid_field", str(exc), 400)
    return JsonResponse(_profile_to_dict(snap))


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def delete_me(request: HttpRequest) -> HttpResponse:
    """Soft-delete the current customer (scrub PII + drop prefs).

    Body: ``{"confirmation": "УДАЛИТЬ"}``. Mismatch → 400. After the
    write the BotUser row is invisible to ``require_init_data`` (which
    filters ``deleted_at__isnull=True``), so subsequent Mini App calls
    return 404 user_not_registered until the user re-onboards via the
    bot DM.
    """
    from apps.identity.services.profile import (
        DELETE_CONFIRMATION_TOKEN,
        DeletionConfirmationMismatch,
        soft_delete_user,
    )

    import json

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    confirmation = body.get("confirmation", "")
    try:
        soft_delete_user(bot_user, confirmation)
    except DeletionConfirmationMismatch:
        return _error(
            "confirmation_mismatch",
            f"body.confirmation must equal {DELETE_CONFIRMATION_TOKEN!r}",
            400,
        )
    return JsonResponse({"deleted": True}, status=200)


def _profile_to_dict(snap) -> dict:
    """Serialise a :class:`ProfileSnapshot` for the JSON response."""
    return {
        "bot_user_id": snap.bot_user_id,
        "display_name": snap.display_name,
        "client_name": snap.client_name,
        "phone_masked": snap.phone_masked,
        "timezone": snap.timezone,
        "joined_at": snap.joined_at,
        "preferences": snap.preferences,
        "favorites": {
            "master_name": snap.favorite_master_name,
            "service_name": snap.favorite_service_name,
        },
    }


# --- /bookings/<id>/feedback — post-visit rating (Phase 4 / F5) ------------


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def submit_feedback(request: HttpRequest, booking_id) -> HttpResponse:  # type: ignore[no-untyped-def]
    """Persist a 1-5 rating for a past visit; rating ≤ 3 escalates.

    Body: ``{"rating": int, "comment": str?}``. The service module
    handles attribution-policy fields + handoff; the view's job is to
    parse, scope-check the booking to ``request.bot_user``, and map
    service errors to HTTP statuses.
    """
    from apps.booking.models import BookingRequest
    from apps.booking.services.feedback import (
        AlreadyRated,
        FeedbackError,
        InvalidRating,
        NotCompletedYet,
        submit_feedback as service_submit_feedback,
    )

    import json

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    booking = (
        BookingRequest.objects.filter(pk=booking_id, bot_user=bot_user)
        .select_related("conversation")
        .first()
    )
    if booking is None:
        return _error("not_found", "booking does not belong to this user", 404)

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)

    rating = body.get("rating")
    comment = body.get("comment", "")

    try:
        result = service_submit_feedback(booking, rating=rating, comment=comment)
    except (InvalidRating, AlreadyRated, NotCompletedYet) as exc:
        return _error(exc.slug, str(exc), 400)
    except FeedbackError as exc:
        return _error(exc.slug, str(exc), 400)

    return JsonResponse(
        {
            "booking_id": result.booking_id,
            "rating": result.rating,
            "comment": result.comment,
            "feedback_at": result.feedback_at,
            "handoff_created": result.handoff_created,
            "task_id": result.task_id,
        },
        status=200,
    )
