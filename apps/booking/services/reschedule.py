"""Customer-initiated reschedule of an existing booking.

Atomic operation per customer-handoff §11 + Q12-α:

1. Lock the OLD booking row (select_for_update).
2. Verify ownership: ``tenant == caller.tenant`` AND ``bot_user ==
   caller.bot_user``.
3. Verify status == CONFIRMED (only confirmed bookings reschedule;
   cancelled/rescheduled rows are terminal).
4. Pull ``service`` + ``master`` from old row — reschedule keeps the
   same service & master, only the time changes (per §11 line 626 "Mini
   App opens at date picker pre-filled with same master+service").
5. Inside transaction.atomic:
   - Old booking → ``status=RESCHEDULED``
   - New booking created via ``create_customer_booking`` with
     ``attribution_metadata.created_by='execute_reschedule'``
   - ``compute_billable`` recognises ``created_by=execute_reschedule``
     → ``billable=False`` (Q12-α retention rule)
6. Emit ``booking.rescheduled`` event linking old → new ids.

The new booking inherits the same partial unique constraint
``(master, visit_at) WHERE status=confirmed`` — race protection
applies identically.
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.db import transaction

from apps.booking.models import BookingRequest
from apps.booking.services.create import (
    BookingCreateError,
    CreateBookingInput,
    create_customer_booking,
)
from apps.events.services import emit
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


def reschedule_customer_booking(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    old_booking_id: str,
    new_visit_at: datetime,
    correlation_id: str | None = None,
) -> BookingRequest:
    """Atomic reschedule. Returns the NEW :class:`BookingRequest`."""

    with tenant_scope(tenant), transaction.atomic():
        try:
            old = (
                BookingRequest.all_tenants.select_for_update()
                .select_related("service", "master")
                .get(id=old_booking_id, tenant_id=tenant.id)
            )
        except BookingRequest.DoesNotExist:
            raise BookingCreateError("not_found", "booking not found")

        if old.bot_user_id != bot_user.id:
            raise BookingCreateError("forbidden", "booking belongs to another customer")
        if old.status != BookingRequest.Status.CONFIRMED:
            raise BookingCreateError(
                "not_reschedulable",
                f"booking status={old.status} (only confirmed bookings can reschedule)",
            )
        if old.service_id is None or old.master_id is None:
            # Legacy rows lack FKs; cannot reschedule reliably.
            raise BookingCreateError(
                "legacy_row",
                "this booking is missing structured data and cannot be rescheduled",
            )

        # Create new booking with execute_reschedule attribution.
        new_booking = create_customer_booking(
            inp=CreateBookingInput(
                tenant=tenant,
                bot_user=bot_user,
                service_id=str(old.service_id),
                master_id=str(old.master_id),
                visit_at=new_visit_at,
                created_by="execute_reschedule",
            ),
            correlation_id=correlation_id,
        )

        # Mark old as RESCHEDULED (terminal).
        BookingRequest.all_tenants.filter(pk=old.pk).update(
            status=BookingRequest.Status.RESCHEDULED,
        )

    emit(
        "booking.rescheduled",
        properties={
            "old_booking_id": str(old.id),
            "new_booking_id": str(new_booking.id),
            "correlation_id": correlation_id or "",
        },
    )
    logger.info(
        "booking.rescheduled old=%s new=%s",
        old.id,
        new_booking.id,
    )
    return new_booking


__all__ = ["reschedule_customer_booking"]
