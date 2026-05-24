"""Atomic, race-safe booking creation for customer-initiated flows.

Extracted from the Mini App view (``apps.miniapp_api.views.create_booking``)
per 4a hardening review:

* ``transaction.atomic`` envelope so the validation + write are a single
  unit.
* ``select_for_update`` on the master row locks the master while the
  resolver re-runs inside the transaction — concurrent customers
  racing for the same slot can no longer both succeed.
* Partial unique index on ``(master_id, visit_at) WHERE
  status='confirmed'`` is the DB-level backstop; this service is the
  application-level first-pass + structured error envelope.
* Emits ``booking.created`` event with ``correlation_id`` for analytics
  and event-sourcing consumers.

### Error envelope

Failures raise :class:`BookingCreateError` with a stable ``slug`` plus
human-readable ``detail``. View layer maps ``slug`` to HTTP status:

* ``service_not_found`` → 404
* ``service_unbookable`` → 409
* ``master_not_bookable`` → 404 (covers archive + invite_status filter)
* ``service_not_offered`` → 404
* ``visit_in_past`` → 400
* ``slot_unavailable`` → 409
* ``master_archived`` → 409 (race: deactivated after we resolved id)
* ``tenant_mismatch`` → 403

### Why a separate exception class instead of ValidationError

ValidationError is Django's generic — it doesn't carry a stable error
slug. The Mini App API uses ``{"error": <slug>, "detail": <text>}``;
mapping ValidationError to that shape loses structure. Custom
exception preserves the slug→HTTP mapping at the view boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from apps.booking.models import BookingRequest
from apps.booking.services.attribution import (
    build_customer_attribution_metadata,
    build_live_commercial_identity,
    compute_assist_score,
    compute_billable,
)
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.events.services import emit
from apps.identity.models import BotUser
from apps.scheduling.services.resolver import (
    collect_time_block_intervals,
    compute_free_slots,
    get_slot_config,
    resolve_working_blocks,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


class BookingCreateError(Exception):
    """Structured error raised by :func:`create_customer_booking`.

    Carries a stable ``slug`` for the API error envelope plus a
    human-readable ``detail`` for logs / dispute UI.
    """

    def __init__(self, slug: str, detail: str):
        super().__init__(f"{slug}: {detail}")
        self.slug = slug
        self.detail = detail


@dataclass(frozen=True)
class CreateBookingInput:
    """Validated input to :func:`create_customer_booking`.

    Callers (e.g. the Mini App view) parse + validate types, then pass
    this. The service trusts the types but re-checks business invariants.
    """

    tenant: Tenant
    bot_user: BotUser
    service_id: str
    master_id: str
    visit_at: datetime  # timezone-aware, in the future
    created_by: str = "execute_confirm"
    """Attribution tag — 'execute_confirm' (default) or 'execute_reschedule'.

    The reschedule service (apps.booking.services.reschedule) passes
    'execute_reschedule' so compute_billable can apply Q12-α
    continuation logic.
    """
    # Q12-α continuation chain (issue #478, founder ACK 2026-05-22).
    # Caller computes these via ``compute_reschedule_continuation``
    # BEFORE invoking this service. For execute_confirm (fresh sale)
    # leave at defaults. For execute_reschedule, the reschedule
    # service is responsible for populating these.
    is_reschedule_continuation: bool = False
    chain_break_reason: str | None = None
    original_booking_event_id: object | None = None  # UUID or None
    # Q12-α #541 commercial_identity_snapshot is INTENTIONALLY NOT a
    # field on this dataclass — see #619 + adversarial-pass A6 fix
    # (2026-05-24). Moved to a private kwarg on
    # :func:`create_customer_booking` so any future REST/webhook/LLM
    # binding of CreateBookingInput as a request schema CANNOT forge a
    # snapshot. Only direct in-process callers (services/reschedule.py
    # and skills/booking/tools.py) can supply the snapshot.


def _occupied_intervals(*, tenant, master, on_date, tz):
    """Same occupancy union used by the slot endpoint — kept inline
    here so the lock window stays short.
    """

    # Match the slot-API filter — CONFIRMED + interim reversible
    # states still occupy the slot (customer-cancellation-reschedule
    # spec §2 / 5-sec undo window).
    qs = BookingRequest.all_tenants.filter(
        tenant_id=tenant.id,
        master_id=master.id,
        status__in=(
            BookingRequest.Status.CONFIRMED,
            BookingRequest.Status.CANCEL_REQUESTED,
            BookingRequest.Status.RESCHEDULE_REQUESTED,
        ),
        visit_at__isnull=False,
        visit_at__date=on_date,
    ).values_list("visit_at", "duration_min")
    intervals: list[tuple[datetime, datetime]] = []
    from datetime import timedelta

    for visit_at, duration_min in qs:
        minutes = duration_min if duration_min else 60
        intervals.append((visit_at, visit_at + timedelta(minutes=minutes)))
    intervals += collect_time_block_intervals(
        tenant=tenant,
        master=master,
        date_from=on_date,
        date_to=on_date,
        tz=tz,
    )
    return intervals


def create_customer_booking(
    *,
    inp: CreateBookingInput,
    correlation_id: str | None = None,
    _commercial_identity_snapshot: dict | None = None,
) -> BookingRequest:
    """Atomic customer booking creation. Returns the created
    :class:`BookingRequest` or raises :class:`BookingCreateError`.

    Caller MUST already be inside the tenant's ``tenant_scope`` OR the
    function will re-enter it from ``inp.tenant``.

    **INTERNAL CONTRACT — Q12-α #619/A6 trust boundary** (2026-05-24).

    ``_commercial_identity_snapshot`` is billing-affecting (controls
    the comparator behaviour in ``compute_reschedule_continuation``).
    The leading underscore signals «internal only» — this kwarg MUST
    ONLY be populated by trusted server-side flows that themselves
    derive the dict via :func:`build_live_commercial_identity` from a
    DB-loaded ``CatalogService`` row OR by carrying forward a root
    row's already-persisted ``commercial_identity_snapshot``.

    Authorised producers as of 2026-05-24:

    * ``apps.booking.services.reschedule.reschedule_customer_booking``
      (REST reschedule path — carries root.snapshot or live build)
    * ``apps.skills.booking.tools.execute_reschedule`` (LLM reschedule
      path — writes via ``BookingRequest.create()`` directly, NOT
      through this function — included for completeness)

    **NEVER** wire this kwarg to a REST request body, webhook payload,
    or LLM tool argument. A forged snapshot whose 4 compared fields
    don't match the live service could bypass the chain-break check
    and silently mark a fresh sale as a ``reschedule_continuation``
    (``billable=False``) → revenue leak. Moving the parameter off the
    dataclass + leading-underscore name + this docstring are the
    layered defence; if a future external boundary needs to influence
    the snapshot, add a server-side integrity check BEFORE forwarding
    it here.

    Race ordering inside the transaction
    ------------------------------------
    1. Lookup service + verify is_active.
    2. ``SELECT … FOR UPDATE`` on the master row — serializes all
       customers booking this master.
    3. Re-check master is_active + invite_status under the lock
       (master may have been archived between view-layer lookup and
       lock acquisition).
    4. Verify ``MasterService`` mapping still exists.
    5. Re-run the slot resolver inside the lock — any other booking
       that snuck in before our lock is visible now.
    6. Insert the BookingRequest. Partial unique index catches the
       (vanishingly rare) case where another transaction holding a
       row-level lock on a DIFFERENT master inserted the same
       ``(master_id, visit_at)``.
    7. Emit ``booking.created`` event.
    """

    tz = ZoneInfo(inp.tenant.timezone)

    if inp.visit_at <= timezone.now():
        raise BookingCreateError("visit_in_past", "visit_at must be in the future")

    with tenant_scope(inp.tenant):
        # Service lookup — no lock needed, the catalog row's mutability
        # doesn't introduce double-booking risk.
        try:
            service = CatalogService.objects.get(id=inp.service_id, is_active=True)
        except CatalogService.DoesNotExist:
            raise BookingCreateError("service_not_found", "service not found")

        if not service.duration_min:
            raise BookingCreateError("service_unbookable", "service has no duration configured")

        # Atomic window: master lock + resolver + insert.
        with transaction.atomic():
            try:
                master = CatalogMaster.all_tenants.select_for_update().get(
                    id=inp.master_id, tenant_id=inp.tenant.id
                )
            except CatalogMaster.DoesNotExist:
                raise BookingCreateError("master_not_bookable", "master not found or not bookable")

            # Under-lock re-check: master may have been archived /
            # invite revoked / tenant mismatched between view lookup
            # and lock acquisition.
            if master.tenant_id != inp.tenant.id:
                raise BookingCreateError("tenant_mismatch", "master belongs to a different tenant")
            if not master.is_active:
                raise BookingCreateError(
                    "master_archived", "master deactivated before booking confirmed"
                )
            if master.invite_status != CatalogMaster.InviteStatus.ACCEPTED:
                raise BookingCreateError(
                    "master_not_bookable",
                    f"master invite_status={master.invite_status}",
                )

            if not MasterService.all_tenants.filter(
                tenant_id=inp.tenant.id,
                master_id=master.id,
                service_id=service.id,
            ).exists():
                raise BookingCreateError(
                    "service_not_offered", "master does not perform this service"
                )

            local_visit = inp.visit_at.astimezone(tz)
            blocks = resolve_working_blocks(
                tenant=inp.tenant, master=master, on_date=local_visit.date()
            )
            if not blocks:
                raise BookingCreateError("slot_unavailable", "master not working at this date")
            occupied = _occupied_intervals(
                tenant=inp.tenant, master=master, on_date=local_visit.date(), tz=tz
            )
            config = get_slot_config(inp.tenant)
            free = compute_free_slots(
                working_blocks=blocks,
                occupied=occupied,
                service_duration_min=int(service.duration_min),
                on_date=local_visit.date(),
                tz=tz,
                config=config,
                now=timezone.now(),
            )
            if not any(slot.start == local_visit for slot in free):
                raise BookingCreateError("slot_unavailable", "slot is no longer available")

            # Q12-α #541 (founder ACK 2026-05-23): snapshot the
            # service's commercial identity at write time. Reschedule
            # callers pass the ROOT chain's snapshot through unchanged
            # via the private ``_commercial_identity_snapshot`` kwarg
            # (#619/A6 trust boundary 2026-05-24) to preserve the
            # original sale's commercial truth across the chain. Fresh
            # sales (None) snapshot from the live ``service`` row.
            # #618 follow-up: shape lives in
            # ``attribution.build_live_commercial_identity`` (single
            # source of truth across all 3 write sites).
            commercial_identity_snapshot = (
                _commercial_identity_snapshot
                if _commercial_identity_snapshot is not None
                else build_live_commercial_identity(service)
            )

            now_iso = timezone.now().isoformat()
            attribution_metadata = build_customer_attribution_metadata(
                booking_created_at=now_iso,
                test_mode=False,
                created_by=inp.created_by,
            )
            billable, billing_reason = compute_billable(
                booking_source="ai_direct",
                status=BookingRequest.Status.CONFIRMED,
                created_by=inp.created_by,
                is_reschedule_continuation=inp.is_reschedule_continuation,
                chain_break_reason=inp.chain_break_reason,
            )
            ai_assist_score = compute_assist_score(booking_source="ai_direct")

            booking = BookingRequest.objects.create(
                tenant=inp.tenant,
                bot_user=inp.bot_user,
                service=service,
                master=master,
                service_name=service.name,
                master_name=master.name,
                client_name=inp.bot_user.client_name or inp.bot_user.display_name or "",
                client_phone=inp.bot_user.phone,
                visit_at=inp.visit_at,
                duration_min=int(service.duration_min),
                status=BookingRequest.Status.CONFIRMED,
                source="bot",
                booking_source="ai_direct",
                ai_assist_score=ai_assist_score,
                billable=billable,
                billing_reason=billing_reason,
                attribution_metadata=attribution_metadata,
                original_booking_event_id=inp.original_booking_event_id,
                commercial_identity_snapshot=commercial_identity_snapshot,
            )

    # Emit AFTER commit so consumers see the row.
    emit(
        "booking.created",
        properties={
            "booking_id": str(booking.id),
            "booking_source": booking.booking_source,
            "billable": booking.billable,
            "visit_at": inp.visit_at.isoformat(),
            "master_id": str(booking.master_id),
            "service_id": str(booking.service_id),
            "correlation_id": correlation_id or "",
        },
    )

    logger.info(
        "booking.created id=%s master=%s visit_at=%s billable=%s",
        booking.id,
        booking.master_id,
        booking.visit_at,
        booking.billable,
    )

    # YC push — async, best-effort. Platform = source of truth; YC is
    # eventual mirror. Task is no-op if tenant has no YClients integration
    # (features.yclients_integration=false) or master/service lacks YC ids.
    # Failure modes documented in apps.integrations.yclients.tasks.
    try:
        from apps.integrations.yclients.tasks import push_booking_to_yclients

        push_booking_to_yclients.delay(booking_id=str(booking.id))
    except Exception as exc:  # noqa: BLE001 — never let YC push break booking flow
        logger.warning(
            "yclients.push.enqueue_failed booking=%s exc=%s",
            booking.id,
            exc,
        )

    return booking


__all__ = [
    "BookingCreateError",
    "CreateBookingInput",
    "create_customer_booking",
]
