"""Signal-based wire-up of Phase 1 domain events.

Phase 2.1 scope: ``booking.created`` only.
Phase 2.2 PR-D adds: ``booking.attribution.assigned`` on the same
post_save signal — attribution fields are populated synchronously
with BookingRequest creation (per 4a `create_customer_booking`),
so both events fire from the same hook with the same correlation id.

Why both events share a hook:
  - taxonomy §3.1 distinguishes them by trigger: `created` = «row
    inserted», `attribution.assigned` = «4a writes booking_source /
    billable». In current code these happen in the same atomic
    transaction, so we co-emit. If attribution later becomes
    re-computable (e.g. nightly back-fill for `external` rows after
    YC webhook port, Q-ATT-IMPL7), that future writer should emit
    `booking.attribution.assigned` independently from the new
    call-site, NOT through this signal — model `pre_save` diff
    detection here would be lossy.

Out of scope (still need per-domain PRs):
  - booking.cancelled / booking.rescheduled — service-layer diff
    (PR-E targets these via apps/bookings/callbacks.py)
  - booking.completed / booking.no_show — depend on YClients webhook
    port (Q-ATT-IMPL7)
  - customer.* / master.* — owning modules' lifecycle code

Bulk-insert caveat (Phase 2.2 cleanup, retro review #7):
  ``post_save`` does NOT fire on ``Model.objects.bulk_create(...)`` —
  Django's documented behaviour. Any future code path creating
  BookingRequests via bulk_create (large imports, backfills, fixture
  seeders) will silently skip both ``booking.created`` and
  ``booking.attribution.assigned``. No current callers do this, but if
  one lands, emit explicitly via :mod:`apps.eventbus.services` after the
  bulk_create completes. Same caveat applies to bulk_update / raw SQL
  inserts.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.booking.models import BookingRequest
from apps.eventbus import services

logger = logging.getLogger(__name__)


@receiver(post_save, sender=BookingRequest, dispatch_uid="eventbus.booking_created")
def _emit_booking_lifecycle_events(
    sender: type[BookingRequest],
    instance: BookingRequest,
    created: bool,
    **kwargs: Any,
) -> None:
    """On new-row creation: emit `booking.created` and (if attribution
    is populated) `booking.attribution.assigned`.

    Both events share a correlation_id so subscribers (audit, future
    billing) can join the lifecycle trail.

    Updates do not re-emit either event; status transitions
    (cancelled / rescheduled) get explicit emits at the service layer.
    """

    if not created:
        return

    correlation_id = services.new_correlation_id()

    _safe_emit_booking_created(instance, correlation_id=correlation_id)
    _safe_emit_attribution_assigned(instance, correlation_id=correlation_id)


def _safe_emit_booking_created(instance: BookingRequest, *, correlation_id: str) -> None:
    try:
        services.emit_booking_created(
            booking_id=str(instance.id),
            customer_id=str(instance.bot_user_id) if instance.bot_user_id else "",
            service_id=str(instance.service_id)
            if _has(instance, "service_id")
            else instance.service_name,
            slot_start=_slot_start_iso(instance),
            booking_source=instance.booking_source
            if _has(instance, "booking_source")
            else instance.source,
            master_id=str(instance.master_id)
            if _has(instance, "master_id")
            else instance.master_name or "",
            tenant=instance.tenant,
            correlation_id=correlation_id,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        logger.exception("eventbus.signal.booking_created_failed booking=%s", instance.pk)


def _safe_emit_attribution_assigned(instance: BookingRequest, *, correlation_id: str) -> None:
    """Emit `booking.attribution.assigned` iff attribution fields are
    populated on the new row.

    Legacy `external` rows (YC webhook, pre-4a) may have empty
    `booking_source` and/or no `attribution_metadata` — skip those to
    avoid forging events for rows the attribution backend hasn't
    actually written to yet.
    """

    if not _has(instance, "booking_source"):
        return  # model didn't have the 4a fields at all
    if not instance.booking_source:
        return  # populated-but-empty (defensive)

    try:
        services.emit_booking_attribution_assigned(
            booking_id=str(instance.id),
            booking_source=instance.booking_source,
            ai_assist_score=float(instance.ai_assist_score or 0),
            billable=bool(instance.billable),
            billing_reason=instance.billing_reason or "",
            attribution_metadata=instance.attribution_metadata or {},
            correlation_id=correlation_id,
            tenant=instance.tenant,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        logger.exception("eventbus.signal.attribution_assigned_failed booking=%s", instance.pk)


def _has(obj: Any, attr: str) -> bool:
    """True iff ``obj`` has the attribute AND its value is not None.

    Tolerates the legacy BookingRequest schema where 4a attribution
    fields didn't exist — protects unit tests building bare instances
    that only set the snapshot fields.
    """

    if not hasattr(obj, attr):
        return False
    value = getattr(obj, attr)
    return value is not None and value != ""


def _slot_start_iso(instance: BookingRequest) -> str:
    """Prefer the real `visit_at` slot when present; fall back to
    `created_at` for legacy rows so the field is never empty.
    """

    visit_at = getattr(instance, "visit_at", None)
    if visit_at:
        return visit_at.isoformat()
    return instance.created_at.isoformat()


# Imported for use by tests
__all__ = ["_emit_booking_lifecycle_events"]


# Type hint compatibility — unused at runtime
_ = uuid  # silence unused-import warning if we don't use UUID directly here
