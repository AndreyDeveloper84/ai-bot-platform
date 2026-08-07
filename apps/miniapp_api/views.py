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
import re
import uuid
from datetime import date as date_cls, datetime, timedelta
from functools import wraps
from typing import Any, Callable
from zoneinfo import ZoneInfo

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.integrations.ayla.payments_client import (
    AylaClientPaymentsClient,
    ClientPaymentsConflictError,
    ClientPaymentsError,
    ClientPaymentsNotFoundError,
)
from apps.integrations.ayla.user_proxy import external_user_id_for

from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant
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
from apps.miniapp_api.dev_bypass import try_dev_bypass
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


def _lazy_register_bot_user(tenant: Tenant, verified: VerifiedInitData) -> BotUser:
    """Create a BotUser from a verified initData on first Mini App tap.

    Used when the customer DM'd the bot through the legacy mysite
    backend (so the row exists in mysite_stage but not in the platform
    DB) — or, post-cutover, simply hadn't opened the bot yet but found
    the Mini App URL another way. Either way, the HMAC proves they
    have legitimate access to the bot.
    """
    user = verified.user
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    display = (f"{first} {last}".strip() or f"max:{verified.user_id}")[:200]

    chat_id = ""
    if verified.chat and "id" in verified.chat:
        chat_id = str(verified.chat.get("id", ""))[:128]

    bot_user, created = BotUser.all_tenants.get_or_create(
        tenant=tenant,
        channel="max",
        channel_user_id=verified.user_id,
        defaults={
            "display_name": display,
            "chat_id": chat_id,
            "timezone": tenant.timezone,
        },
    )
    if created:
        logger.info(
            "miniapp_api.auth.lazy_register tenant=%s channel_user_id=%s display=%r",
            tenant.slug,
            verified.user_id,
            display,
        )
    return bot_user


def require_init_data(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Decorator: verify ``Authorization: MaxInitData …`` and resolve identity.

    On success, attaches to the request:

    * ``request.verified_init_data`` — :class:`VerifiedInitData`
    * ``request.bot_user`` — :class:`BotUser` matching the user.id
    * ``request.tenant`` — the :class:`Tenant` owning the BotUser

    It does **NOT** enter :func:`tenant_scope` (#1019 / EPIC #1014): the
    nationwide bot serves discovery tenant-less, so per-request scope is no
    longer a blanket decorator concern. Views that read/write tenant-scoped
    models MUST stack :func:`with_request_tenant` below this decorator to enter
    ``tenant_scope(request.tenant)`` for their body. Tenant-less surfaces
    (pure proxies) run without a scope.

    On failure: 401 (bad signature / stale / not configured), 400
    (malformed), 500 (bot tenant not configured). Unknown users are
    LAZY-CREATED on first verified contact (pre-cutover design).
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        # Dev-bypass (DEBUG-gated, header-opt-in, loudly logged) — see
        # apps/miniapp_api/dev_bypass.py. In prod, the first line of
        # try_dev_bypass is `if not settings.DEBUG: return None`, so this
        # branch is dead code in production regardless of header presence.
        bypass = try_dev_bypass(request)
        if bypass is not None:
            bot_user_b, _bypass_tenant = bypass
            request.verified_init_data = None  # type: ignore[attr-defined]
            request.bot_user = bot_user_b  # type: ignore[attr-defined]
            request.tenant = bot_user_b.tenant  # type: ignore[attr-defined]
            return view_func(request, *args, **kwargs)

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

        # Tenant resolution: in single-bot mode the env binds the bot to
        # exactly one tenant. Multi-tenant ingress will rewire this later
        # via the channel-token map (CHANNEL_TOKEN_TO_TENANT_SLUG).
        bot_tenant_slug = getattr(settings, "MAX_BOT_TENANT_SLUG", "")
        bot_tenant = (
            Tenant.objects.filter(slug=bot_tenant_slug).first() if bot_tenant_slug else None
        )

        # Look up scoped to that tenant — including soft-deleted rows so
        # we can return a distinct error for those users (they need to
        # contact support, not silently re-onboard).
        existing = (
            BotUser.all_tenants.filter(
                tenant=bot_tenant,
                channel="max",
                channel_user_id=verified.user_id,
            )
            .select_related("tenant")
            .order_by("-last_seen")
            .first()
            if bot_tenant is not None
            else None
        )

        if existing is not None and existing.deleted_at is not None:
            return _error(
                "user_deleted",
                "Аккаунт удалён. Чтобы восстановить, напишите в поддержку студии.",
                403,
            )

        if existing is not None:
            bot_user = existing
        else:
            # Lazy-create. The HMAC has already proven the user is a
            # legitimate MAX user of the bot owning this Mini App; we
            # don't gate twice. Pre-cutover, this fills the gap where
            # webhook is still pointing at mysite and no BotUser exists
            # in ai_bot_platform_dev yet.
            if bot_tenant is None:
                logger.error(
                    "miniapp_api.auth.no_tenant_configured slug=%r — cannot lazy-create BotUser",
                    bot_tenant_slug,
                )
                return _error(
                    "server_misconfigured",
                    "Bot tenant not configured — contact support.",
                    500,
                )
            bot_user = _lazy_register_bot_user(bot_tenant, verified)

        request.verified_init_data = verified  # type: ignore[attr-defined]
        request.bot_user = bot_user  # type: ignore[attr-defined]
        request.tenant = bot_user.tenant  # type: ignore[attr-defined]
        return view_func(request, *args, **kwargs)

    return wrapper


def with_request_tenant(view_func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Enter ``tenant_scope(request.tenant)`` for the wrapped view body (#1019).

    Stack **below** :func:`require_init_data` (so ``request.tenant`` is already
    attached). Required on every view that reads/writes tenant-scoped models via
    the default (``.objects``) manager — booking writes AND catalog reads
    (``slots`` / ``services`` / ``masters``) — because :func:`require_init_data`
    no longer enters a scope and those reads would otherwise raise
    ``CrossTenantError`` in strict mode. Booking still enters the correct
    tenant scope this way; discovery / pure-proxy surfaces stay tenant-less.
    """

    @wraps(view_func)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        tenant = getattr(request, "tenant", None)
        with tenant_scope(tenant):
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

    # pending_booking_intent (P0 PRE_PILOT, anonymous-gate restoration)

    Per memory `project_booking_flow_implementation_cut` founder cut #6:
    the Mini App may pass an optional `pending_booking_intent` object in
    the POST body to preserve the draft booking the user was assembling
    when the OAuth gate fired. Backend caches it (Redis, 10min TTL,
    keyed by BotUser.id) and echoes the current value in the response so
    the frontend can restore the booking flow state post-OAuth.

    Request body (optional):
      ```
      {
        "pending_booking_intent": {
          "master_id": "...uuid...",
          "service_id": "...uuid...",
          "slot_iso": "2026-07-15T14:00:00+03:00",
          "price_quoted": 1800,
          "note": "массаж лица",
          "loyalty_apply": true
        }
      }
      ```

    Response always includes `pending_booking_intent` (the current
    cached value OR null if nothing cached / expired).
    """
    import json

    from apps.miniapp_api.pending_intent import (
        PendingIntentInvalid,
        get_intent,
        store_intent,
        validate_intent,
    )

    verified: VerifiedInitData = request.verified_init_data  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    # Optional body — Mini App may call /auth/verify without any pending
    # intent on first launch. Only attempt JSON parse when the caller
    # explicitly declares `Content-Type: application/json`; multipart /
    # form / empty bodies are treated as «no intent» without error.
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body)
        except ValueError:
            return _error("malformed", "body is not valid JSON", 400)
        if not isinstance(body, dict):
            return _error("malformed", "body must be a JSON object", 400)
        if "pending_booking_intent" in body:
            try:
                sanitised = validate_intent(body["pending_booking_intent"])
            except PendingIntentInvalid as exc:
                return _error("invalid_intent", str(exc), 400)
            store_intent(bot_user.id, sanitised)

    cached_intent = get_intent(bot_user.id)

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
            "pending_booking_intent": cached_intent,
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
    # Active states that still hold the slot — CONFIRMED plus the two
    # interim "reversible" states from customer-cancellation-reschedule
    # spec §2. A booking in CANCEL_REQUESTED is still bookable to the
    # original customer (5-sec undo); a booking in RESCHEDULE_REQUESTED
    # has stashed a candidate but the original slot is still theirs.
    qs = BookingRequest.all_tenants.filter(
        tenant_id=tenant_id,
        master_id=master_id,
        status__in=(
            BookingRequest.Status.CONFIRMED,
            BookingRequest.Status.CANCEL_REQUESTED,
            BookingRequest.Status.RESCHEDULE_REQUESTED,
        ),
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
@with_request_tenant
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
@with_request_tenant
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
@with_request_tenant
def service_detail(request: HttpRequest, service_id: str) -> HttpResponse:
    try:
        service = CatalogService.objects.get(id=service_id, is_active=True)
    except CatalogService.DoesNotExist:
        return _error("not_found", "service not found", 404)
    return JsonResponse({"service": _service_to_dict(service)})


@require_http_methods(["GET"])
@require_init_data
@with_request_tenant
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
@with_request_tenant
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
}


def _create_booking_via_ayla(
    *,
    bot_user,
    tenant,
    service_id: str,
    master_id: str,
    visit_at,
    payment_required: bool,
) -> HttpResponse:
    """Ayla-first booking create (BOOKING_VIA_AYLA_REST ON).

    Local mirror rows ground the Ayla ids; a miss fails CLOSED (no
    silent local write on the Ayla path — the #1034 gate semantics).
    ``payment_required`` passes through verbatim; the response keeps
    the existing BookingItem shape with the canonical appointment id
    and Ayla's status (``confirmed`` / ``awaiting_payment``) verbatim.
    """
    import hashlib

    from apps.catalog.models import CatalogMaster, CatalogService
    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingBadRequestError,
        BookingUnavailableError,
        get_ayla_booking_client,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    ayla_user_id = getattr(bot_user, "ayla_user_id", None)
    if not ayla_user_id:
        return _error(
            "identity_not_linked",
            "user is not linked to Ayla yet — booking unavailable",
            403,
        )

    try:
        service = CatalogService.objects.get(id=service_id, is_active=True)
    except CatalogService.DoesNotExist:
        return _error("not_found", "service not found", 404)
    try:
        master = CatalogMaster.objects.bookable().get(id=master_id)
    except CatalogMaster.DoesNotExist:
        return _error("not_found", "master not found or not bookable", 404)

    if not service.ayla_service_id or not master.ayla_user_id:
        # Fail closed per the booking health-check gate (#1034): on the
        # Ayla path an ungrounded row must NOT silently book anywhere.
        logger.warning(
            "miniapp_api.create_booking.ayla_grounding_miss service_id=%s master_id=%s",
            service_id,
            master_id,
        )
        return _error(
            "service_unbookable",
            "service is not synced to Ayla yet",
            409,
        )

    seed = "|".join(
        [
            external_user_id_for(bot_user),
            "create",
            str(master.ayla_user_id),
            str(service.ayla_service_id),
            visit_at.isoformat(),
            str(payment_required),
        ]
    )
    idempotency_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

    try:
        record = get_ayla_booking_client().create_appointment(
            external_user_id=external_user_id_for(bot_user),
            client_id=str(ayla_user_id),
            # #1027 / W4-эскалация №2: Ayla create expects the
            # SpecialistProfile UUID (= CatalogMaster.id per the masters
            # mirror mapping), NOT master.ayla_user_id (the Ayla User
            # UUID — that one is the AMD-005 BILLING key only).
            specialist_id=str(master.id),
            service_id=str(service.ayla_service_id),
            start_datetime=visit_at.isoformat(),
            idempotency_key=idempotency_key,
            payment_required=payment_required,
        )
    except BookingBadRequestError as exc:
        if (exc.code or "").lower() == "subscription_past_due":
            # C1: neutral surface — no debt semantics to the client
            # (frozen W4 slug).
            return _error(
                "unavailable",
                "Запись к этому специалисту сейчас недоступна",
                409,
            )
        logger.info("miniapp_api.create_booking.ayla_bad_request err=%s", exc)
        slug = "slot_unavailable" if "slot" in (exc.code or "") else "bad_request"
        return _error(slug, "booking rejected", 409 if slug == "slot_unavailable" else 400)
    except BookingUnavailableError:
        logger.warning("miniapp_api.create_booking.ayla_unavailable")
        return _error(
            "upstream_unavailable",
            "booking upstream is temporarily unavailable",
            502,
        )
    except BookingAPIError:
        logger.exception("miniapp_api.create_booking.ayla_error")
        return _error(
            "upstream_unavailable",
            "booking upstream is temporarily unavailable",
            502,
        )

    status = record.raw.get("status", "")
    return JsonResponse(
        {
            "booking": {
                "id": record.appointment_id,
                "service_name": service.name,
                "master_name": master.name,
                "visit_at": visit_at.isoformat(),
                "duration_min": service.duration_min,
                # Ayla verbatim: confirmed (payment_required=false) or
                # awaiting_payment (true, pending Payment created).
                "status": status,
            }
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
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

    # AMD-019 / D6: miniapp accepts payment_required (default FALSE —
    # the pilot no-prepayment baseline; FE sends true explicitly for the
    # online path). On the Ayla path (BOOKING_VIA_AYLA_REST) the value
    # rides to Ayla's create: false → CONFIRMED without Payment,
    # true → AWAITING_PAYMENT + pending Payment. The chat flow's
    # execute_confirm default (True) is intentionally NOT shared here.
    payment_required = bool(body.get("payment_required", False))
    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return _create_booking_via_ayla(
            bot_user=bot_user,
            tenant=tenant,
            service_id=service_id,
            master_id=master_id,
            visit_at=visit_at,
            payment_required=payment_required,
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


# --- bookings: list / detail / cancel / reschedule -------------------------
# Customer cancel + reschedule per
# docs/design/policies/customer-cancellation-reschedule-spec.md §3-§5.


_TERMINAL_STATUSES = (
    "cancelled",
    "rescheduled",
)
_ACTIVE_STATUSES = (
    "confirmed",
    "cancel_requested",
    "reschedule_requested",
)
"""States where the booking still exists from the customer's POV."""


_BOOKINGS_PAGE_DEFAULT = 20
_BOOKINGS_PAGE_MAX = 50


_AYLA_MARKER_RE = re.compile(r"yclients_record_id=([0-9a-fA-F-]{36})")


def _ayla_appointment_id_of(booking) -> str | None:
    """Canonical Ayla appointment UUID of a local BookingRequest, if any.

    On the Ayla path (BOOKING_VIA_AYLA_REST) execute_confirm writes it
    into the ``yclients_record_id=<uuid>`` comment marker (same marker
    key across providers — the reader convention is one). Returns None
    for legacy/local-only rows.
    """
    match = _AYLA_MARKER_RE.search(booking.comment or "")
    return match.group(1) if match else None


def _booking_to_dict(b, *, now=None) -> dict[str, Any]:
    from apps.booking.services.transitions import UNDO_WINDOW_SECONDS

    visit_at_iso = b.visit_at.isoformat() if b.visit_at else ""
    cancel_requested_iso = b.cancel_requested_at.isoformat() if b.cancel_requested_at else None
    # Cancellable + reschedulable derived flags. Action buttons in the
    # Mini App use these directly — spec §3.4 + §5.3.
    cancellable = b.status in (
        "confirmed",
        "reschedule_requested",
    )
    # reschedulable requires both an active status AND live FKs to
    # service+master. If the catalog row was deleted (e.g. dev catalog
    # re-seed) the booking keeps service_name / master_name strings but
    # has NULL FKs — the reschedule slot lookup can't run without them.
    reschedulable = b.status == "confirmed" and b.service_id is not None and b.master_id is not None
    # Phase 4 — post-visit rating exposure. can_rate is computed against
    # the shared `now` so a batch listing is internally consistent.
    moment = now or timezone.now()
    is_past = bool(b.visit_at and b.visit_at < moment)
    can_rate = is_past and b.status == "confirmed" and b.rating is None
    out = {
        "id": str(b.id),
        "status": b.status,
        "service_id": str(b.service_id) if b.service_id else None,
        "service_name": b.service_name,
        "master_id": str(b.master_id) if b.master_id else None,
        "master_name": b.master_name,
        "visit_at": visit_at_iso,
        "duration_min": b.duration_min,
        "cancel_requested_at": cancel_requested_iso,
        "undo_window_seconds": UNDO_WINDOW_SECONDS,
        "cancellable": cancellable,
        "reschedulable": reschedulable,
        # Phase 4 — F5 rating exposure
        "rating": b.rating,
        "can_rate": can_rate,
    }
    # C7.3: optional payment read-model — present only when the event
    # stream produced a mirror row (hold signal or a payment.* event).
    appt_id = _ayla_appointment_id_of(b)
    if appt_id:
        from apps.booking.models import PaymentMirror

        mirror = PaymentMirror.all_tenants.filter(appointment_id=appt_id).first()
        if mirror is not None:
            out["payment"] = {
                "capture_state": mirror.capture_state,
                "amount": (f"{mirror.amount:.2f}" if mirror.amount is not None else None),
            }
    return out


@require_http_methods(["GET"])
@require_init_data
@with_request_tenant
def bookings_list(request: HttpRequest) -> HttpResponse:
    """List the bot_user's bookings.

    Query
    -----
    ``status`` (optional, repeatable) — filter to specific statuses.
       Special value ``past`` toggles the past-bookings view (visit_at
       < now, terminal statuses).
       Default (no ``status`` param): upcoming — visit_at >= now,
       active statuses (CONFIRMED + RESCHEDULE_REQUESTED).
    ``limit`` (optional, default 20, max 50) — page size.
    ``before`` (optional, ISO datetime) — cursor: only items with
       visit_at < ``before`` (descending pagination).

    Returns
    -------
    ``{"items": [...], "next_cursor": "ISO" | null}``
    """

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    # W4 escalation №3 (BOOKING_VIA_AYLA_REST): on the Ayla path the
    # canonical read model is RemoteBookingProxy — the Ayla-first create
    # deliberately never writes BookingRequest (no dual-write).
    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return _bookings_list_ayla(request, bot_user)

    from apps.booking.models import BookingRequest

    statuses = request.GET.getlist("status")
    is_past_view = "past" in statuses
    if is_past_view:
        statuses = [s for s in statuses if s != "past"]

    try:
        limit = int(request.GET.get("limit", _BOOKINGS_PAGE_DEFAULT))
    except ValueError:
        return _error("bad_request", "limit must be integer", 400)
    if limit <= 0 or limit > _BOOKINGS_PAGE_MAX:
        return _error("bad_request", f"limit must be 1..{_BOOKINGS_PAGE_MAX}", 400)

    before = _parse_iso_datetime(request.GET.get("before"))

    from django.db.models import Q

    qs = BookingRequest.all_tenants.filter(
        tenant=bot_user.tenant,
        bot_user=bot_user,
    )
    now = timezone.now()
    if is_past_view:
        # Past = either visit_at strictly before now OR a terminal status.
        # Covers customers who cancelled (terminal but visit_at could be
        # in either direction) AND walk-aways still on CONFIRMED whose
        # visit time has passed.
        qs = qs.filter(Q(visit_at__lt=now) | Q(status__in=_TERMINAL_STATUSES))
        qs = qs.order_by("-visit_at", "-created_at")
    else:
        # Upcoming: visit_at >= now, status in CONFIRMED /
        # RESCHEDULE_REQUESTED (default) OR caller-supplied list.
        wanted = statuses or [
            BookingRequest.Status.CONFIRMED,
            BookingRequest.Status.RESCHEDULE_REQUESTED,
        ]
        qs = qs.filter(status__in=wanted, visit_at__gte=now)
        qs = qs.order_by("visit_at", "created_at")

    if before is not None:
        qs = qs.filter(visit_at__lt=before)

    # Fetch one extra to compute next_cursor without a second query.
    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = last.visit_at.isoformat() if last.visit_at else None

    return JsonResponse({"items": [_booking_to_dict(b) for b in rows], "next_cursor": next_cursor})


def _get_booking_owned(bot_user: BotUser, booking_id: str):
    """Fetch a BookingRequest scoped to (tenant, bot_user).

    Returns the row or None. Tenant + bot_user guard prevents
    cross-customer + cross-tenant access (spec §11).
    """
    from apps.booking.models import BookingRequest

    try:
        return BookingRequest.all_tenants.get(
            id=booking_id,
            tenant=bot_user.tenant,
            bot_user=bot_user,
        )
    except BookingRequest.DoesNotExist:
        return None


# ── Ayla-path read model (RemoteBookingProxy) — W4 escalation №3 ────────────
#
# BOOKING_VIA_AYLA_REST ON: the Ayla-first create never writes
# BookingRequest (no dual-write, by design), so list/detail/cancel read
# the proxy mirror instead. The proxy itself is written ONLY by event
# consumers (booking.* round-trip) — never by these views.

# Statuses the customer considers "upcoming" on the Ayla path (analog of
# CONFIRMED + RESCHEDULE_REQUESTED on the local path). ``pending_payment``
# is the event-contract enum value consumers write; ``awaiting_payment``
# is Ayla's wire status (the create response returns it verbatim) — kept
# defensively so a verbatim-mirrored row still reads as upcoming.
_AYLA_UPCOMING_STATUSES = ("confirmed", "awaiting_payment", "pending_payment")
_AYLA_TERMINAL_STATUSES = ("cancelled", "completed", "no_show")


def _proxy_booking_to_dict(proxy) -> dict[str, Any]:
    """BookingItem shape from a RemoteBookingProxy row.

    Field-for-field identical to the local ``_booking_to_dict`` so the FE
    never branches: names resolve through the catalog mirrors
    (CatalogService.ayla_service_id / CatalogMaster.id), duration from
    the schedule window, status verbatim from the proxy. Mirror lookups
    go through the tenant-scoped manager (``with_request_tenant`` sets
    the context; the proxy row itself was fetched under the same
    tenant) — no ``all_tenants`` carve-out here (MKT1, #1018).
    """
    from apps.catalog.models import CatalogMaster, CatalogService

    service = None
    if proxy.service_id:
        service = CatalogService.objects.filter(ayla_service_id=proxy.service_id).first()
    master = None
    if proxy.specialist_id:
        master = CatalogMaster.objects.filter(id=proxy.specialist_id).first()

    duration_min = 0
    if proxy.start_at and proxy.end_at:
        duration_min = max(int((proxy.end_at - proxy.start_at).total_seconds() // 60), 0)

    out = {
        "id": str(proxy.appointment_id),
        "status": proxy.status,
        "service_id": str(proxy.service_id) if proxy.service_id else None,
        "service_name": service.name if service else "",
        "master_id": str(master.id) if master else None,
        "master_name": master.name if master else "",
        "visit_at": proxy.start_at.isoformat() if proxy.start_at else "",
        "duration_min": duration_min,
        # Immediate-cancel path — no two-step undo flow on the Ayla path.
        "cancel_requested_at": None,
        "undo_window_seconds": 0,
        "cancellable": proxy.status in _AYLA_UPCOMING_STATUSES,
        # Reschedule seam is out of the W4 №3 scope — keep it hidden.
        "reschedulable": False,
        # No rating read model on the Ayla path in pilot.
        "rating": None,
        "can_rate": False,
    }
    # C7.3 parity with the local BookingItem: optional payment read-model,
    # present only when the event stream produced a mirror row (hold
    # signal or a payment.* event) for this appointment.
    from apps.booking.models import PaymentMirror

    mirror = PaymentMirror.all_tenants.filter(
        tenant=proxy.tenant, appointment_id=proxy.appointment_id
    ).first()
    if mirror is not None:
        out["payment"] = {
            "capture_state": mirror.capture_state,
            "amount": (f"{mirror.amount:.2f}" if mirror.amount is not None else None),
        }
    return out


def _bookings_list_ayla(request: HttpRequest, bot_user) -> HttpResponse:
    """List the bot_user's bookings from RemoteBookingProxy (Ayla path).

    Same query contract as the local list: ``status`` (repeatable, with
    the special ``past`` toggle), ``limit`` (default 20, max 50),
    ``before`` cursor on the visit timestamp.
    """
    from django.db.models import Q

    from apps.booking.models import RemoteBookingProxy

    statuses = request.GET.getlist("status")
    is_past_view = "past" in statuses
    if is_past_view:
        statuses = [s for s in statuses if s != "past"]

    try:
        limit = int(request.GET.get("limit", _BOOKINGS_PAGE_DEFAULT))
    except ValueError:
        return _error("bad_request", "limit must be integer", 400)
    if limit <= 0 or limit > _BOOKINGS_PAGE_MAX:
        return _error("bad_request", f"limit must be 1..{_BOOKINGS_PAGE_MAX}", 400)

    before = _parse_iso_datetime(request.GET.get("before"))

    qs = RemoteBookingProxy.all_tenants.filter(
        tenant=bot_user.tenant,
        bot_user=bot_user,
    )
    now = timezone.now()
    if is_past_view:
        qs = qs.filter(Q(start_at__lt=now) | Q(status__in=_AYLA_TERMINAL_STATUSES))
        qs = qs.order_by("-start_at", "-created_at")
    else:
        wanted = statuses or list(_AYLA_UPCOMING_STATUSES)
        qs = qs.filter(status__in=wanted, start_at__gte=now)
        qs = qs.order_by("start_at", "created_at")

    if before is not None:
        qs = qs.filter(start_at__lt=before)

    rows = list(qs[: limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = last.start_at.isoformat() if last.start_at else None

    return JsonResponse(
        {"items": [_proxy_booking_to_dict(p) for p in rows], "next_cursor": next_cursor}
    )


def _booking_detail_ayla(bot_user, booking_id: str) -> HttpResponse:
    """Booking detail from RemoteBookingProxy (Ayla path). 404 on a
    missing row AND on any ownership mismatch (foreign / orphan proxy)."""
    from apps.booking.models import RemoteBookingProxy

    proxy = RemoteBookingProxy.all_tenants.filter(
        tenant=bot_user.tenant,
        appointment_id=booking_id,
        bot_user=bot_user,
    ).first()
    if proxy is None:
        return _error("not_found", "booking not found", 404)
    return JsonResponse({"booking": _proxy_booking_to_dict(proxy)})


def _cancel_via_ayla(bot_user, booking_id: str) -> HttpResponse:
    """Cancel through the Ayla seam (BOOKING_VIA_AYLA_REST ON).

    Ownership is proven against the RemoteBookingProxy mirror (tenant +
    bot_user); the seam call cancels in Ayla and the proxy row flips to
    ``cancelled`` ONLY via the booking.cancelled round-trip event — this
    view never mutates the proxy directly (no dual-write). Cancel is
    immediate: there is no two-step confirm/undo on the Ayla path.
    """
    import hashlib

    from apps.booking.models import RemoteBookingProxy
    from apps.integrations.ayla.booking_client import (
        BookingAPIError,
        BookingBadRequestError,
        BookingUnavailableError,
        get_ayla_booking_client,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    proxy = RemoteBookingProxy.all_tenants.filter(
        tenant=bot_user.tenant,
        appointment_id=booking_id,
        bot_user=bot_user,
    ).first()
    if proxy is None:
        # Covers foreign and orphan proxies alike — no existence leak.
        return _error("not_found", "booking not found", 404)

    seed = "|".join([external_user_id_for(bot_user), "cancel", str(booking_id)])
    idempotency_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]

    try:
        get_ayla_booking_client().cancel_appointment(
            external_user_id=external_user_id_for(bot_user),
            appointment_id=str(booking_id),
            idempotency_key=idempotency_key,
        )
    except BookingBadRequestError as exc:
        if exc.status_code == 404 or (exc.code or "").upper() == "NOT_FOUND":
            return _error("not_found", "booking not found", 404)
        logger.info("miniapp_api.cancel_booking.ayla_bad_request err=%s", exc)
        return _error(
            "invalid_state",
            "booking cannot be cancelled in its current state",
            409,
        )
    except BookingUnavailableError:
        logger.warning("miniapp_api.cancel_booking.ayla_unavailable")
        return _error(
            "upstream_unavailable",
            "booking upstream is temporarily unavailable",
            502,
        )
    except BookingAPIError:
        logger.exception("miniapp_api.cancel_booking.ayla_error")
        return _error(
            "upstream_unavailable",
            "booking upstream is temporarily unavailable",
            502,
        )

    # The proxy stays untouched: the booking.cancelled round-trip event
    # flips the status. The response mirrors the current row verbatim.
    return JsonResponse({"booking": _proxy_booking_to_dict(proxy)})


@require_http_methods(["GET"])
@require_init_data
@with_request_tenant
def booking_detail(request: HttpRequest, booking_id: str) -> HttpResponse:
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return _booking_detail_ayla(bot_user, booking_id)
    booking = _get_booking_owned(bot_user, booking_id)
    if booking is None:
        return _error("not_found", "booking not found", 404)
    return JsonResponse({"booking": _booking_to_dict(booking)})


_TRANSITION_SLUG_TO_STATUS = {
    "invalid_state": 409,
    "forbidden": 403,
    "undo_window_elapsed": 409,
    "master_not_found": 404,
    "master_archived": 409,
    "master_not_bookable": 409,
    "service_not_found": 404,
    "service_unbookable": 409,
    "service_not_offered": 404,
    "slot_unavailable": 409,
}


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
def booking_cancel_request(request: HttpRequest, booking_id: str) -> HttpResponse:
    """POST /bookings/{id}/cancel — request cancel, returns immediately.

    Body (optional)::

        {"reason_class": "timing" | "plans_changed" | "not_needed" | "other",
         "reason_text": "..."}
    """

    from apps.booking.services.transitions import (
        InvalidBookingTransition,
        request_cancel,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        return _cancel_via_ayla(bot_user, booking_id)
    booking = _get_booking_owned(bot_user, booking_id)
    if booking is None:
        return _error("not_found", "booking not found", 404)

    import json

    try:
        body = json.loads(request.body or b"{}") if request.body else {}
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    reason_class = body.get("reason_class") or None
    reason_text = body.get("reason_text") or None

    try:
        row = request_cancel(
            booking,
            actor=bot_user,
            reason_class=reason_class,
            reason_text=reason_text,
        )
    except InvalidBookingTransition as exc:
        return _error(exc.slug, exc.detail, _TRANSITION_SLUG_TO_STATUS.get(exc.slug, 409))

    return JsonResponse({"booking": _booking_to_dict(row)})


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
def booking_cancel_confirm(request: HttpRequest, booking_id: str) -> HttpResponse:
    from apps.booking.services.transitions import (
        InvalidBookingTransition,
        commit_cancel,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        # Ayla-path cancel is immediate — no two-step confirm/undo.
        return _error(
            "invalid_state",
            "cancel is immediate on the Ayla path — no two-step confirm",
            409,
        )
    booking = _get_booking_owned(bot_user, booking_id)
    if booking is None:
        return _error("not_found", "booking not found", 404)
    try:
        row = commit_cancel(booking, actor=bot_user)
    except InvalidBookingTransition as exc:
        return _error(exc.slug, exc.detail, _TRANSITION_SLUG_TO_STATUS.get(exc.slug, 409))

    # BILLING_REFUND_STUB_LOG: refund evaluation (spec §4) is engineering
    # / billing scope. Stub the call so the audit trail captures the
    # intent; no payment provider integration here.
    if booking.visit_at and booking.billable:
        minutes_before = (booking.visit_at - timezone.now()).total_seconds() / 60
        if minutes_before < 60:
            logger.info(
                "BILLING_REFUND_STUB_LOG: refund_eligible=True amount=100 "
                "reason=late_cancel_auto booking_id=%s",
                booking.id,
            )

    return JsonResponse({"booking": _booking_to_dict(row)})


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
def booking_cancel_undo(request: HttpRequest, booking_id: str) -> HttpResponse:
    from apps.booking.services.transitions import (
        InvalidBookingTransition,
        undo_cancel,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    if getattr(settings, "BOOKING_VIA_AYLA_REST", False):
        # Ayla-path cancel is immediate — no two-step confirm/undo.
        return _error(
            "invalid_state",
            "cancel is immediate on the Ayla path — no undo window",
            409,
        )
    booking = _get_booking_owned(bot_user, booking_id)
    if booking is None:
        return _error("not_found", "booking not found", 404)
    try:
        row = undo_cancel(booking, actor=bot_user)
    except InvalidBookingTransition as exc:
        return _error(exc.slug, exc.detail, _TRANSITION_SLUG_TO_STATUS.get(exc.slug, 409))
    return JsonResponse({"booking": _booking_to_dict(row)})


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
def booking_reschedule_request(request: HttpRequest, booking_id: str) -> HttpResponse:
    """POST /bookings/{id}/reschedule — stash a candidate slot.

    Body::

        {"new_master_id": "...", "new_service_id": "...",
         "new_visit_at": "ISO 8601"}

    The candidate is validated lightly here (parse + future check)
    and re-validated under-lock in
    :func:`commit_reschedule`. Slot collision is caught there too.
    """

    from apps.booking.services.transitions import (
        InvalidBookingTransition,
        request_reschedule,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    booking = _get_booking_owned(bot_user, booking_id)
    if booking is None:
        return _error("not_found", "booking not found", 404)

    import json

    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    new_master_id = body.get("new_master_id") or ""
    new_service_id = body.get("new_service_id") or ""
    new_visit_at = _parse_iso_datetime(body.get("new_visit_at"))

    if not new_master_id or not new_service_id or new_visit_at is None:
        return _error(
            "bad_request",
            "new_master_id, new_service_id, new_visit_at (ISO 8601) are required",
            400,
        )
    if new_visit_at <= timezone.now():
        return _error("visit_in_past", "new_visit_at must be in the future", 400)

    try:
        row = request_reschedule(
            booking,
            actor=bot_user,
            new_master_id=new_master_id,
            new_service_id=new_service_id,
            new_visit_at=new_visit_at,
        )
    except InvalidBookingTransition as exc:
        return _error(exc.slug, exc.detail, _TRANSITION_SLUG_TO_STATUS.get(exc.slug, 409))
    return JsonResponse({"booking": _booking_to_dict(row)})


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
def booking_reschedule_confirm(request: HttpRequest, booking_id: str) -> HttpResponse:
    """POST /bookings/{id}/reschedule/confirm — actually rotate to new booking.

    On slot collision (409 inside commit_reschedule): roll the old
    row back to CONFIRMED so the customer can pick again.
    """

    from apps.booking.services.transitions import (
        InvalidBookingTransition,
        abandon_reschedule,
        commit_reschedule,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    booking = _get_booking_owned(bot_user, booking_id)
    if booking is None:
        return _error("not_found", "booking not found", 404)
    try:
        old_row, new_row = commit_reschedule(booking, actor=bot_user)
    except InvalidBookingTransition as exc:
        # On slot collision, roll back the old row to CONFIRMED so
        # the customer's original visit is still on the books.
        if exc.slug == "slot_unavailable":
            try:
                abandon_reschedule(booking, actor=bot_user)
            except InvalidBookingTransition:
                pass
        return _error(exc.slug, exc.detail, _TRANSITION_SLUG_TO_STATUS.get(exc.slug, 409))

    return JsonResponse(
        {
            "old_booking": _booking_to_dict(old_row),
            "new_booking": _booking_to_dict(new_row),
        }
    )


# --- /me — profile read / update / delete (Phase 3 / F4) -------------------


@csrf_exempt
@require_http_methods(["GET", "PATCH"])
@require_init_data
@with_request_tenant
def me(request: HttpRequest) -> HttpResponse:
    """Read or partially update the current customer's profile."""
    import json

    from apps.identity.services.profile import (
        ProfileUpdateError,
        get_profile,
        update_profile,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    if request.method == "GET":
        snap = get_profile(bot_user)
        return JsonResponse(_profile_to_dict(snap))

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
@with_request_tenant
def delete_me(request: HttpRequest) -> HttpResponse:
    """Soft-delete the current customer (scrub PII + drop prefs)."""
    import json

    from apps.identity.services.profile import (
        DELETE_CONFIRMATION_TOKEN,
        DeletionConfirmationMismatch,
        soft_delete_user,
    )

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


# --- C5 personal-data export/delete (152-ФЗ, pilot 2026-08-15) --------------
#
# Frozen contract PILOT_CONTRACTS_2026-08-15 §6. The aggregation/cascade
# lives in apps.identity.services.privacy; these are thin HTTP shells.


@require_http_methods(["GET"])
@require_init_data
@with_request_tenant
def personal_data_export(request: HttpRequest) -> HttpResponse:
    """C5.1 — aggregate Ayla export + bot memory + consents into one JSON.

    Delivered as an attachment (per contract). 502 when the Ayla leg
    fails — a silently-incomplete export is a compliance lie.
    """
    from apps.identity.services.privacy import (
        PrivacyIdentityConflictError,
        PrivacyUpstreamError,
        export_personal_data,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    try:
        payload = export_personal_data(bot_user)
    except PrivacyUpstreamError:
        return _error(
            "upstream_unavailable",
            "personal-data export is temporarily unavailable, try again later",
            502,
        )
    except PrivacyIdentityConflictError:
        return _error(
            "identity_conflict",
            "person identity conflict: cannot determine canonical subject",
            502,
        )
    response = JsonResponse(payload)
    response["Content-Disposition"] = 'attachment; filename="personal-data-export.json"'
    return response


@csrf_exempt
@require_http_methods(["DELETE"])
@require_init_data
@with_request_tenant
def personal_data_delete(request: HttpRequest) -> HttpResponse:
    """C5.2 — delete cascade: Ayla delete + memory erasure + consent withdraw.

    Requires an explicit destructive confirmation in the body — the same
    ``DELETE_CONFIRMATION_TOKEN`` primitive the sibling ``POST /me/delete``
    already uses (DRF-956 / T-05 owner ruling). A client-side sheet alone is
    not a confirmation: before this, any single authenticated DELETE — a
    network retry, a bad deep link, a router bug — ran the full cascade with
    no evidence of intent.

    Idempotent per contract: a repeat confirmed request returns the same 200.
    A failed or skipped mandatory step yields an honest 502 + failed_steps.
    """
    import json

    from apps.identity.services.privacy import delete_personal_data
    from apps.identity.services.profile import DELETE_CONFIRMATION_TOKEN

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    if body.get("confirmation", "") != DELETE_CONFIRMATION_TOKEN:
        # Nothing has been touched at this point — the cascade is below.
        return _error(
            "confirmation_mismatch",
            f"body.confirmation must equal {DELETE_CONFIRMATION_TOKEN!r}",
            400,
        )

    result = delete_personal_data(bot_user)
    if result.all_ok:
        return JsonResponse({"status": "deleted"}, status=200)
    # ``failed_details`` distinguishes a transient failure (retry helps) from
    # a structural one like ``not_linked`` (retry can never help). Without it
    # the sheet invites an infinite "попробуй ещё раз" loop. Slugs only —
    # never values.
    return JsonResponse(
        {
            "status": "partial",
            "failed_steps": result.failed_steps,
            "failed_details": {s.step: s.detail for s in result.steps if not s.ok and s.detail},
        },
        status=502,
    )


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
@with_request_tenant
def submit_feedback(request: HttpRequest, booking_id) -> HttpResponse:  # type: ignore[no-untyped-def]
    """Persist a 1-5 rating for a past visit; rating ≤ 3 escalates."""
    import json

    from apps.booking.services.feedback import (
        AlreadyRated,
        FeedbackError,
        InvalidRating,
        NotCompletedYet,
        submit_feedback as service_submit_feedback,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    # Use the shared owner+tenant loader (explicit tenant predicate) for
    # parity with the transition endpoints, and a generic not-found message
    # so the response can't act as an existence oracle (#1005).
    booking = _get_booking_owned(bot_user, booking_id)
    if booking is None:
        return _error("not_found", "booking not found", 404)

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)

    rating_raw = body.get("rating")
    comment_raw = body.get("comment", "")
    rating: int = rating_raw  # type: ignore[assignment]
    comment: str = comment_raw  # type: ignore[assignment]

    try:
        result = service_submit_feedback(booking, actor=bot_user, rating=rating, comment=comment)
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


# --- /customer/recommendations — Ayla catalog proxy ------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
def customer_recommendations(request: HttpRequest) -> HttpResponse:
    """Proxy recommendations call onto Ayla per identity-bridging contract.

    The Mini App calls ``POST /api/v1/customer/recommendations`` with a
    JSON body describing what to score (``lat`` / ``lon`` / ``goal`` /
    ``tenant_history``). This view translates the call onto Ayla's
    ``POST /internal/me/catalog/recommendations/`` endpoint using the
    service-to-service identity bridge:

    * ``Authorization: Bearer {AYLA_SERVICE_TOKEN}`` — bot-platform's
      service credential. The customer's initData HMAC stays here; we
      never forward it to Ayla.
    * ``X-External-User-ID: bot:{channel}:{channel_user_id}`` — Ayla
      resolves this to its ProxyUser via the user_proxy mapping.

    The Ayla response body is passed through verbatim. The Mini App
    side owns the rendering contract, so adding a translation layer
    here only creates a release-lockstep tax.

    Failure mapping:

    * 400 — body not valid JSON object, OR Ayla returned 4xx
      (Ayla's response body forwarded under ``ayla_error``).
    * 502 — Ayla timeout / 5xx / malformed JSON.
    * 503 — bot-platform misconfigured (missing service token / base URL).
    """
    import json

    from apps.integrations.ayla import external_user_id_for
    from apps.integrations.ayla.recommendations_client import (
        RecommendationsBadRequest,
        RecommendationsConfigError,
        RecommendationsUnavailable,
        fetch_recommendations,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    # Match the /auth/verify pattern: only parse JSON when the caller
    # explicitly declares `Content-Type: application/json`. Empty/
    # multipart bodies are treated as «no scoring hints» — Ayla receives
    # `{}` and returns its default ranking.
    body: dict = {}
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type == "application/json" and request.body:
        try:
            parsed = json.loads(request.body)
        except ValueError:
            return _error("malformed", "body is not valid JSON", 400)
        if not isinstance(parsed, dict):
            return _error("malformed", "body must be a JSON object", 400)
        body = parsed

    try:
        ayla_body = fetch_recommendations(
            external_user_id=external_user_id_for(bot_user),
            payload=body,
        )
    except RecommendationsConfigError as exc:
        logger.error("customer_recommendations.config_error: %s", exc)
        return _error("not_configured", "ayla recommendations not configured", 503)
    except RecommendationsBadRequest as exc:
        return JsonResponse(
            {
                "error": "ayla_bad_request",
                "detail": f"ayla returned HTTP {exc.status_code}",
                "ayla_error": exc.body,
            },
            status=400,
        )
    except RecommendationsUnavailable as exc:
        logger.warning("customer_recommendations.unavailable: %s", exc)
        return _error("ayla_unavailable", "ayla recommendations unavailable", 502)

    return JsonResponse(ayla_body)


# --- /customer/wellness/today — nutrition composition ----------------------

# Standard glass = 250 ml. The Ayla water endpoint reports millilitres;
# the Mini App dashboard (Tau §6 Block 5) renders glasses. Conversion
# lives here so the frontend stays unit-agnostic.
_WATER_GLASS_ML = 250
# Cold-start default when the customer skipped the nutrition anketa and
# Ayla reports norm_ml=0. Matches the frontend stub default (Tau §11.1).
_WATER_GLASSES_TARGET_DEFAULT = 8


def _ml_to_glasses(ml: float) -> int:
    """Round millilitres to whole glasses (250 ml each). Never negative."""
    if ml <= 0:
        return 0
    return round(ml / _WATER_GLASS_ML)


@require_http_methods(["GET"])
@require_init_data
def customer_wellness_today(request: HttpRequest) -> HttpResponse:
    """Compose the customer's today-snapshot for the Wellness dashboard.

    Wraps two Ayla nutrition reads — ``daily_summary`` (calories + PFC)
    and ``get_water_today`` (hydration) — into the ``WellnessToday``
    shape the Mini App expects (see
    ``apps/miniapp/src/lib/customer-wellness.ts``).

    Identity bridging: the NutritionClient sends
    ``X-External-User-ID: bot:{channel}:{channel_user_id}`` +
    ``X-Service-Token``; the customer's initData HMAC never leaves
    bot-platform.

    ## Graceful degradation

    The two Ayla calls run concurrently and degrade INDEPENDENTLY: if
    `daily_summary` fails, calories/PFC zero out but hydration still
    renders, and vice-versa. The endpoint returns 200 with whatever
    succeeded — a dashboard that renders partial data beats a blank
    error screen. Zeros are a valid «no logs today» state per the
    frontend contract, so a degraded response is indistinguishable from
    a genuinely empty day; that's an accepted trade for resilience.

    ## Fields without an Ayla source (documented gaps)

    * ``active_goals`` — the Layer-2 Goals system has no REST endpoint
      yet; returned as ``[]`` so the frontend shows the «Выбери цель»
      CTA (Tau §11.2). Wire when the goals endpoint ships.
    * ``pfc.protein_target_g`` + ``day_pattern_hint`` — omitted (no
      clean source). Frontend treats both as optional.
    """
    import asyncio

    from apps.integrations.ayla import external_user_id_for, get_nutrition_client
    from apps.integrations.ayla.nutrition_client import (
        NutritionAPIError,
        NutritionUnavailableError,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    external_id = external_user_id_for(bot_user)

    async def _fetch() -> tuple[Any, Any]:
        client = get_nutrition_client()
        return await asyncio.gather(
            client.daily_summary(external_user_id=external_id),
            client.get_water_today(external_user_id=external_id),
            return_exceptions=True,
        )

    summary_res, water_res = asyncio.run(_fetch())

    nutrition_errors = (NutritionUnavailableError, NutritionAPIError)

    # ── calories + PFC (from daily_summary) ─────────────────────────────
    calories_eaten = 0
    calories_target = 0
    pfc: dict[str, Any] | None = None
    if isinstance(summary_res, nutrition_errors):
        logger.warning("wellness_today.summary_unavailable ext=%s err=%s", external_id, summary_res)
    elif isinstance(summary_res, Exception):
        # Unexpected exception type — log + degrade, never 500 the dashboard.
        logger.warning(
            "wellness_today.summary_unexpected ext=%s err=%s",
            external_id,
            type(summary_res).__name__,
        )
    else:
        calories_eaten = round(summary_res.calories_total)
        calories_target = int(summary_res.calories_goal)
        pfc = {
            "protein_g": round(summary_res.protein_g),
            "fat_g": round(summary_res.fat_g),
            "carbs_g": round(summary_res.carbs_g),
        }

    # ── hydration (from get_water_today) ────────────────────────────────
    water_glasses_eaten = 0
    water_glasses_target = _WATER_GLASSES_TARGET_DEFAULT
    if isinstance(water_res, nutrition_errors):
        logger.warning("wellness_today.water_unavailable ext=%s err=%s", external_id, water_res)
    elif isinstance(water_res, Exception):
        logger.warning(
            "wellness_today.water_unexpected ext=%s err=%s",
            external_id,
            type(water_res).__name__,
        )
    else:
        water_glasses_eaten = _ml_to_glasses(water_res.total_ml)
        target = _ml_to_glasses(water_res.norm_ml)
        water_glasses_target = target or _WATER_GLASSES_TARGET_DEFAULT

    payload: dict[str, Any] = {
        "calories_eaten": calories_eaten,
        "calories_target": calories_target,
        "water_glasses_eaten": water_glasses_eaten,
        "water_glasses_target": water_glasses_target,
        # No Goals-system endpoint yet — empty array drives the «Выбери
        # цель» CTA. See docstring.
        "active_goals": [],
        "display_name": bot_user.client_name or bot_user.display_name or "",
    }
    if pfc is not None:
        payload["pfc"] = pfc

    return JsonResponse(payload)


# --- /customer/recent-activity — dashboard rollup --------------------------

# Russian short weekday names (Mon=0 … Sun=6) for the date_human label.
_RU_WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
# Russian month names in genitive case («3 июня») for non-relative dates.
_RU_MONTH_GENITIVE = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def _format_visit_human(visit_at, tz: ZoneInfo, *, now=None) -> str:
    """Render a booking's visit time as «Завтра · пт · 16:00» (customer TZ).

    Relative-day prefix: «Сегодня» / «Завтра» for delta 0/1, else the
    «{day} {month_genitive}» date. Always followed by the short weekday
    + 24h time. Server-rendered so the frontend prints verbatim.
    """
    local = visit_at.astimezone(tz)
    moment = (now or timezone.now()).astimezone(tz)
    day_delta = (local.date() - moment.date()).days

    if day_delta == 0:
        prefix = "Сегодня"
    elif day_delta == 1:
        prefix = "Завтра"
    else:
        prefix = f"{local.day} {_RU_MONTH_GENITIVE[local.month]}"

    weekday = _RU_WEEKDAY_SHORT[local.weekday()]
    return f"{prefix} · {weekday} · {local:%H:%M}"


@require_http_methods(["GET"])
@require_init_data
@with_request_tenant
def customer_recent_activity(request: HttpRequest) -> HttpResponse:
    """Dashboard rollup — next booking + this-week count (bookings-only).

    Backs the Mini App Wellness dashboard Block 5/6 (see
    ``apps/miniapp/src/lib/customer-wellness.ts:RecentActivity``).

    ## Scope: bookings-only pilot

    Per tech-lead verdict 2026-05-29 + memory `project_pilot_scope_discipline`:
    Ayla has no meals-list / timeline endpoint (only single-day
    `daily_summary` + aggregate `weekly_deficits`), so the nutrition
    rollup is deferred to a «meals layer» Phase-1 expansion that wires
    when Alpha ships a meals-list endpoint. `weekly_progress` therefore
    returns zeros — Block 6 (Прогресс недели) is gated on
    `active_days_count >= 3` (Tau §11.4 cold-start) so it stays hidden
    gracefully rather than showing misleading data.

    ## Data source

    `next_booking` + `this_week_booking_count` read the local
    `BookingRequest` mirror (populated by the Ayla booking-event
    consumer per ADR-0009 — a read of cached canonical state, not
    ownership). No Ayla round-trip on this path.

    ## Fields without a source (documented gaps)

    * `next_booking.address` — bot-platform's `Tenant` has no address
      field; returned as `""`. Frontend renders empty until the address
      lands (Ayla salon profile OR a tenant config field).
    """
    from datetime import timedelta

    from apps.booking.models import BookingRequest

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    tenant = bot_user.tenant
    try:
        tz = ZoneInfo(tenant.timezone or "Europe/Moscow")
    except Exception:  # noqa: BLE001 — bad tz config must not 500 the dashboard
        tz = ZoneInfo("Europe/Moscow")

    now = timezone.now()

    # ── next upcoming CONFIRMED booking ─────────────────────────────────
    next_qs = BookingRequest.all_tenants.filter(
        tenant=tenant,
        bot_user=bot_user,
        status=BookingRequest.Status.CONFIRMED,
        visit_at__gte=now,
    ).order_by("visit_at")
    next_row = next_qs.first()

    next_booking: dict[str, Any] | None = None
    if next_row is not None and next_row.visit_at is not None:
        next_booking = {
            "date_human": _format_visit_human(next_row.visit_at, tz, now=now),
            "service_name": next_row.service_name,
            "duration_min": next_row.duration_min or 0,
            "master_name": next_row.master_name,
            "salon_name": tenant.name,
            # No address field on Tenant — graceful empty per docstring.
            "address": "",
            "booking_id": str(next_row.id),
        }

    # ── this-week CONFIRMED count (Mon 00:00 … next Mon, customer TZ) ────
    local_now = now.astimezone(tz)
    week_start_local = (local_now - timedelta(days=local_now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end_local = week_start_local + timedelta(days=7)
    this_week_count = BookingRequest.all_tenants.filter(
        tenant=tenant,
        bot_user=bot_user,
        status=BookingRequest.Status.CONFIRMED,
        visit_at__gte=week_start_local,
        visit_at__lt=week_end_local,
    ).count()

    payload: dict[str, Any] = {
        "this_week_booking_count": this_week_count,
        # Nutrition rollup deferred — see docstring. Zeros keep Block 6
        # hidden (gated on active_days_count >= 3).
        "weekly_progress": {
            "water_days_logged": 0,
            "food_days_logged": 0,
            "active_days_count": 0,
        },
    }
    if next_booking is not None:
        payload["next_booking"] = next_booking

    return JsonResponse(payload)


# --- C7 client payments passthrough (PILOT_CONTRACTS §7.5) ------------------
#
# Verified customer binding (C7.6): every endpoint resolves the Ayla user
# from the SESSION BotUser (identity linkage), never from client input.
# A client-supplied ayla_user_id that doesn't match the session is 403.
# Amounts never come from the client (C7.1 — Ayla prices from the Booking
# snapshot). Fields pass through verbatim.


def _resolve_c7_ayla_user(request: HttpRequest, body: dict | None = None) -> Any:
    """C7.6 verified customer binding.

    Returns the session-resolved ``ayla_user_id`` (str) on success, or a
    ``JsonResponse`` error to return immediately:

    * 403 ``identity_not_linked`` — the BotUser has no Ayla link yet.
    * 403 ``forbidden`` — the client supplied an ``ayla_user_id`` that
      does not match the session identity (arbitrary id never trusted).
    """
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    ayla_user_id = getattr(bot_user, "ayla_user_id", None)
    if not ayla_user_id:
        return _error(
            "identity_not_linked",
            "user is not linked to Ayla yet — payments unavailable",
            403,
        )
    supplied = request.GET.get("ayla_user_id") or (
        body.get("ayla_user_id") if isinstance(body, dict) else None
    )
    if supplied and str(supplied) != str(ayla_user_id):
        logger.warning(
            "miniapp_api.c7.foreign_user_id bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        return _error("forbidden", "ayla_user_id does not match the session identity", 403)
    return str(ayla_user_id)


def _c7_json_body(request: HttpRequest) -> dict | JsonResponse:
    import json

    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("malformed", "body must be a JSON object", 400)
    return body


def _c7_upstream_error(exc: Exception, *, not_found_slug: str = "not_found") -> JsonResponse:
    """Map a C7 client failure onto an HTTP response (neutral slugs)."""

    if isinstance(exc, ClientPaymentsConflictError):
        if (exc.code or "").lower() == "subscription_past_due":
            # C1: neutral surface — no debt semantics to the client.
            return _error(
                "unavailable",
                "Запись к этому специалисту сейчас недоступна",
                409,
            )
        return _error("conflict", "operation conflicts with current state", 409)
    if isinstance(exc, ClientPaymentsNotFoundError):
        return _error(not_found_slug, "resource not found", 404)
    logger.exception("miniapp_api.c7.upstream_error")
    return _error("upstream_unavailable", "payments upstream is temporarily unavailable", 502)


def _customer_owns_appointment(*, bot_user, tenant, appointment_id: str) -> bool:
    """Ownership check (C7.6): the appointment must belong to THIS user —
    via the Ayla-path proxy mirror or a local BookingRequest link."""
    from apps.booking.models import RemoteBookingProxy

    return RemoteBookingProxy.all_tenants.filter(
        tenant=tenant, appointment_id=appointment_id, bot_user=bot_user
    ).exists()


def _c7_return_url(body: dict) -> str:
    """YooKassa ``return_url`` for the confirmation flows.

    The miniapp may send one explicitly (master-side precedent); otherwise
    fall back to the configured ``AYLA_CLIENT_PAYMENTS_RETURN_URL``. Empty
    string when neither is set — the caller turns that into a local 400
    rather than an upstream one.
    """
    supplied = str(body.get("return_url") or "").strip()
    if supplied:
        return supplied
    return getattr(settings, "AYLA_CLIENT_PAYMENTS_RETURN_URL", "") or ""


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
def create_payment(request: HttpRequest) -> HttpResponse:
    """C7.1 — create a two-stage payment for an owned appointment.

    Body: ``{"appointment_id": "<uuid>"}`` (+ optional ``return_url`` —
    falls back to ``AYLA_CLIENT_PAYMENTS_RETURN_URL``; + optional
    ayla_user_id that MUST match the session, C7.6). No amount accepted —
    Ayla prices from the Booking snapshot. Response: the Ayla ``data``
    verbatim (``payment_id``, ``confirmation_url``, ``amount``,
    ``capture_state``, ``currency``).
    """

    body = _c7_json_body(request)
    if isinstance(body, JsonResponse):
        return body
    binding = _resolve_c7_ayla_user(request, body)
    if isinstance(binding, JsonResponse):
        return binding

    appointment_id = str(body.get("appointment_id") or "").strip()
    if not appointment_id:
        return _error("bad_request", "appointment_id is required", 400)
    try:
        uuid.UUID(appointment_id)
    except (ValueError, AttributeError):
        return _error("bad_request", "appointment_id must be a UUID", 400)

    if not _customer_owns_appointment(
        bot_user=request.bot_user,  # type: ignore[attr-defined]
        tenant=request.tenant,  # type: ignore[attr-defined]
        appointment_id=appointment_id,
    ):
        # 404, not 403 — do not leak that the appointment exists at all.
        return _error("appointment_not_found", "appointment not found", 404)

    return_url = _c7_return_url(body)
    if not return_url:
        return _error("bad_request", "return_url is required", 400)

    try:
        with AylaClientPaymentsClient() as client:
            data = client.create_payment(
                appointment_id=appointment_id,
                # IsBotServiceWithVerifiedClient: Bearer + resolved actor
                # (X-External-User-ID); body client_id is the C7.6
                # cross-check against that actor.
                external_user_id=external_user_id_for(request.bot_user),  # type: ignore[attr-defined]
                client_id=binding,
                return_url=return_url,
            )
    except ClientPaymentsError as exc:
        return _c7_upstream_error(exc, not_found_slug="appointment_not_found")
    return JsonResponse(data)


@csrf_exempt
@require_http_methods(["POST"])
@require_init_data
@with_request_tenant
def cards_setup(request: HttpRequest) -> HttpResponse:
    """C7.2 — start card binding (separate voluntary action). Body carries
    the consent boundary: ``consent_version`` (required; ``consented_at``
    accepted for the audit trail, not forwarded upstream) + optional
    ``return_url``. Response: ``{confirmation_url}`` verbatim."""

    body = _c7_json_body(request)
    if isinstance(body, JsonResponse):
        return body
    binding = _resolve_c7_ayla_user(request, body)
    if isinstance(binding, JsonResponse):
        return binding
    consent_version = str(body.get("consent_version") or "").strip()
    if not consent_version:
        return _error("bad_request", "consent_version is required", 400)
    return_url = _c7_return_url(body)
    if not return_url:
        return _error("bad_request", "return_url is required", 400)
    try:
        with AylaClientPaymentsClient() as client:
            data = client.cards_setup(
                ayla_user_id=binding,
                external_user_id=external_user_id_for(request.bot_user),  # type: ignore[attr-defined]
                consent_version=consent_version,
                return_url=return_url,
            )
    except ClientPaymentsError as exc:
        return _c7_upstream_error(exc)
    return JsonResponse(data)


@require_http_methods(["GET"])
@require_init_data
@with_request_tenant
def cards_list(request: HttpRequest) -> HttpResponse:
    """C7.2 — list the customer's saved cards (verbatim upstream payload)."""

    binding = _resolve_c7_ayla_user(request)
    if isinstance(binding, JsonResponse):
        return binding
    try:
        with AylaClientPaymentsClient() as client:
            data = client.list_cards(
                ayla_user_id=binding,
                external_user_id=external_user_id_for(request.bot_user),  # type: ignore[attr-defined]
            )
    except ClientPaymentsError as exc:
        return _c7_upstream_error(exc)
    if isinstance(data, list):
        return JsonResponse({"cards": data})
    return JsonResponse(data)


@csrf_exempt
@require_http_methods(["DELETE"])
@require_init_data
@with_request_tenant
def card_delete(request: HttpRequest, card_id) -> HttpResponse:
    """C7.2 — revoke a saved card. Idempotent: upstream 404 (already
    gone) counts as deleted (repeat → 204)."""

    binding = _resolve_c7_ayla_user(request)
    if isinstance(binding, JsonResponse):
        return binding
    try:
        with AylaClientPaymentsClient() as client:
            client.delete_card(
                ayla_user_id=binding,
                card_id=str(card_id),
                external_user_id=external_user_id_for(request.bot_user),  # type: ignore[attr-defined]
            )
    except ClientPaymentsNotFoundError:
        pass  # already gone — idempotent success
    except ClientPaymentsError as exc:
        return _c7_upstream_error(exc)
    return HttpResponse(status=204)
