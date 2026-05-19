"""booking.attribution.assigned auto-wire (Phase 2.2 PR-D)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.booking.models import BookingRequest
from apps.eventbus import vocabulary as V
from apps.eventbus.models import DomainEvent
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_FUTURE = dt.datetime(2027, 6, 15, 10, 0, 0, tzinfo=dt.UTC)


def _attribution_metadata():
    return {
        "actor_type": "customer",
        "booking_created_at": "2026-05-19T10:00:00Z",
        "test_mode": False,
    }


class TestAttributionAssignedFires:
    def test_new_booking_emits_both_events(self):
        t = Tenant.objects.create(slug="att-a", name="A")
        with tenant_scope(t):
            BookingRequest.objects.create(
                tenant=t,
                service_name="hair",
                client_name="Customer",
                client_phone="snapshot",
                source="bot",
                booking_source="ai_direct",
                ai_assist_score=Decimal("0.85"),
                billable=True,
                billing_reason="ai_direct + customer actor",
                visit_at=_FUTURE,
                attribution_metadata=_attribution_metadata(),
            )

        created_events = DomainEvent.objects.filter(event_name=V.BOOKING_CREATED)
        attribution_events = DomainEvent.objects.filter(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert created_events.count() == 1
        assert attribution_events.count() == 1

    def test_correlation_id_shared_between_pair(self):
        t = Tenant.objects.create(slug="att-b", name="B")
        with tenant_scope(t):
            BookingRequest.objects.create(
                tenant=t,
                service_name="hair",
                client_name="Customer",
                client_phone="snapshot",
                source="bot",
                booking_source="ai_direct",
                ai_assist_score=Decimal("0.85"),
                billable=True,
                billing_reason="ai_direct",
                visit_at=_FUTURE,
                attribution_metadata=_attribution_metadata(),
            )

        created = DomainEvent.objects.get(event_name=V.BOOKING_CREATED)
        attribution = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert created.correlation_id
        assert created.correlation_id == attribution.correlation_id

    def test_attribution_payload_carries_fields(self):
        t = Tenant.objects.create(slug="att-c", name="C")
        with tenant_scope(t):
            BookingRequest.objects.create(
                tenant=t,
                service_name="hair",
                client_name="Customer",
                client_phone="snapshot",
                source="bot",
                booking_source="ai_direct",
                ai_assist_score=Decimal("0.85"),
                billable=True,
                billing_reason="ai_direct + customer actor",
                visit_at=_FUTURE,
                attribution_metadata=_attribution_metadata(),
            )

        ev = DomainEvent.objects.get(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED)
        assert ev.data["booking_source"] == "ai_direct"
        assert ev.data["ai_assist_score"] == pytest.approx(0.85)
        assert ev.data["billable"] is True
        assert ev.data["billing_reason"] == "ai_direct + customer actor"
        assert ev.data["attribution_metadata"]["actor_type"] == "customer"


class TestAttributionAssignedSkipPaths:
    def test_external_booking_with_empty_booking_source_skips_attribution(self):
        # `external` row written by YC webhook (pre-Q-ATT-IMPL7 port)
        # has `booking_source='external'` but attribution_metadata={}.
        # The validator allows {} for external rows. We DO still emit
        # attribution_assigned because booking_source is non-empty —
        # the writer DID classify the row. attribution_metadata being
        # empty is policy-sanctioned (§15.1 deviation), not absence.
        t = Tenant.objects.create(slug="att-d", name="D")
        with tenant_scope(t):
            BookingRequest.objects.create(
                tenant=t,
                service_name="hair",
                client_name="Customer",
                client_phone="snapshot",
                source="yclients_admin",
                booking_source="external",
                ai_assist_score=Decimal("0.00"),
                billable=False,
                billing_reason="",
                attribution_metadata={},
            )

        # Both fire — booking_source classified means attribution decided.
        assert DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).count() == 1
        assert DomainEvent.objects.filter(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED).count() == 1

    def test_update_does_not_re_emit_attribution(self):
        t = Tenant.objects.create(slug="att-e", name="E")
        with tenant_scope(t):
            booking = BookingRequest.objects.create(
                tenant=t,
                service_name="hair",
                client_name="Customer",
                client_phone="snapshot",
                source="bot",
                booking_source="ai_direct",
                ai_assist_score=Decimal("0.85"),
                billable=True,
                billing_reason="ai_direct",
                visit_at=_FUTURE,
                attribution_metadata=_attribution_metadata(),
            )
            booking.is_processed = True
            booking.save()

        # Only one of each — second save did not re-emit.
        assert DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).count() == 1
        assert DomainEvent.objects.filter(event_name=V.BOOKING_ATTRIBUTION_ASSIGNED).count() == 1
