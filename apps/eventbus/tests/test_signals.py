"""Signal wireup — booking.created fires on BookingRequest.create()."""

from __future__ import annotations

import pytest

from apps.booking.models import BookingRequest
from apps.eventbus import vocabulary as V
from apps.eventbus.models import DomainEvent
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class TestBookingCreatedSignal:
    def test_new_booking_emits_event(self):
        t = Tenant.objects.create(slug="evb-sig-a", name="A")
        with tenant_scope(t):
            booking = BookingRequest.objects.create(
                tenant=t,
                service_name="hair",
                client_name="Anonymous",
                client_phone="snapshot",  # snapshot field — not PII in event payload
                source="bot",
            )

        rows = DomainEvent.objects.filter(event_name=V.BOOKING_CREATED)
        assert rows.count() == 1
        row = rows.first()
        assert row.data["booking_id"] == str(booking.id)
        assert row.data["booking_source"] == "bot"
        assert row.tenant_id == t.id

    def test_update_does_not_re_emit(self):
        t = Tenant.objects.create(slug="evb-sig-b", name="B")
        with tenant_scope(t):
            booking = BookingRequest.objects.create(
                tenant=t,
                service_name="hair",
                client_name="Anonymous",
                client_phone="snapshot",
                source="bot",
            )
            # Mutate + save — must NOT produce a second event.
            booking.is_processed = True
            booking.save()

        assert DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).count() == 1
