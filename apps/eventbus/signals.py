"""Signal-based wire-up of Phase 1 domain events.

Phase 1 scope: emit ``booking.created`` on BookingRequest post_save
(new row only). Other Phase 1 events (cancelled/completed/etc. plus
all customer.* and master.*) are exposed as typed emit helpers in
:mod:`apps.eventbus.services` — call-site wire-up lives in the
domain modules that own the lifecycle, added in follow-up PRs.

Why not auto-wire everything here:
  - booking.cancelled needs the status-change diff (was=confirmed,
    now=cancelled) — that's a service-layer concern in apps/booking,
    not a model-level signal.
  - master.* events need Master models that don't exist yet.
  - customer.* needs identity + consent integration that's still
    being designed.

This file should stay thin. New auto-wires belong in the domain app's
own signals.py when the lifecycle code is added.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.booking.models import BookingRequest
from apps.eventbus import services

logger = logging.getLogger(__name__)


@receiver(post_save, sender=BookingRequest, dispatch_uid="eventbus.booking_created")
def _emit_booking_created(
    sender: type[BookingRequest],
    instance: BookingRequest,
    created: bool,
    **kwargs: Any,
) -> None:
    """Emit ``booking.created`` exactly once per new BookingRequest row.

    Updates do not re-emit; status transitions (cancelled / rescheduled)
    will be handled by a service-layer call that emits the appropriate
    booking.* event explicitly. Auto-emitting on every save would
    spam the bus.
    """

    if not created:
        return
    try:
        services.emit_booking_created(
            booking_id=str(instance.id),
            customer_id=str(instance.bot_user_id) if instance.bot_user_id else "",
            service_id=instance.service_name,  # snapshot field; catalog id link TBD
            slot_start=instance.created_at.isoformat(),  # placeholder until slot fields land
            booking_source=instance.source,
            master_id=instance.master_name or "",
            tenant=instance.tenant,
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the request
        logger.exception("eventbus.signal.booking_created_failed booking=%s", instance.pk)
