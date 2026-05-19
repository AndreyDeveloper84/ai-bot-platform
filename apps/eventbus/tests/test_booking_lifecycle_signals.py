"""booking.cancelled / booking.rescheduled emit helpers (Phase 2.2 PR-E).

Tool-flow integration is covered by apps/skills/booking tool tests;
this file pins the typed emit helpers in isolation so a regression on
payload shape is caught without spinning up YClients mocks.
"""

from __future__ import annotations

import pytest

from apps.eventbus import services, vocabulary as V
from apps.eventbus.models import DomainEvent
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class TestEmitBookingCancelled:
    def test_payload_matches_taxonomy_3_1(self):
        t = Tenant.objects.create(slug="cancel-a", name="A")
        with tenant_scope(t):
            services.emit_booking_cancelled(
                booking_id="bk-1",
                cancelled_by="customer",
                cancellation_reason="changed my mind",
                cancelled_at="2026-05-19T10:00:00Z",
                actor_type="human",
                actor_role="customer",
                actor_id="bot-user-123",
                tenant=t,
            )

        row = DomainEvent.objects.get(event_name=V.BOOKING_CANCELLED)
        assert row.data["booking_id"] == "bk-1"
        assert row.data["cancelled_by"] == "customer"
        assert row.data["cancellation_reason"] == "changed my mind"
        assert row.data["cancelled_at"] == "2026-05-19T10:00:00Z"
        assert row.actor == {"type": "human", "id": "bot-user-123", "role": "customer"}
        assert row.tenant_id == t.id

    def test_system_initiated_cancel_no_actor_role(self):
        # Reschedule partial-failure path emits cancelled with actor_type=system
        # and no role. Payload still valid per taxonomy §3.1.
        services.emit_booking_cancelled(
            booking_id="bk-2",
            cancelled_by="system",
            cancellation_reason="reschedule_partial_failure",
            cancelled_at="2026-05-19T10:00:00Z",
            actor_type="system",
        )

        row = DomainEvent.objects.get(data__booking_id="bk-2")
        assert row.event_name == V.BOOKING_CANCELLED
        assert row.actor["type"] == "system"
        assert row.actor["role"] is None


class TestEmitBookingRescheduled:
    def test_payload_matches_taxonomy_3_1(self):
        t = Tenant.objects.create(slug="resch-a", name="A")
        with tenant_scope(t):
            services.emit_booking_rescheduled(
                booking_id="bk-3",
                old_slot_start="2026-05-20T10:00:00Z",
                new_slot_start="2026-05-21T14:00:00Z",
                rescheduled_by="customer",
                actor_type="human",
                actor_role="customer",
                actor_id="bot-user-456",
                tenant=t,
            )

        row = DomainEvent.objects.get(event_name=V.BOOKING_RESCHEDULED)
        assert row.data["booking_id"] == "bk-3"
        assert row.data["old_slot_start"] == "2026-05-20T10:00:00Z"
        assert row.data["new_slot_start"] == "2026-05-21T14:00:00Z"
        assert row.data["rescheduled_by"] == "customer"


class TestPartialFailureCorrelation:
    def test_rescheduled_then_cancelled_share_correlation_id(self):
        """execute_reschedule partial-failure path emits BOTH events
        (rescheduled = old-row terminal mark, cancelled = no replacement
        booked) with the SAME correlation_id so subscribers recognise
        the composite outcome.
        """
        corr = services.new_correlation_id()
        services.emit_booking_rescheduled(
            booking_id="bk-4",
            old_slot_start="2026-05-20T10:00:00Z",
            new_slot_start="2026-05-21T14:00:00Z",
            rescheduled_by="customer",
            actor_type="human",
            actor_role="customer",
            correlation_id=corr,
        )
        services.emit_booking_cancelled(
            booking_id="bk-4",
            cancelled_by="system",
            cancellation_reason="reschedule_partial_failure",
            cancelled_at="2026-05-19T10:00:00Z",
            actor_type="system",
            correlation_id=corr,
        )

        rescheduled = DomainEvent.objects.get(event_name=V.BOOKING_RESCHEDULED)
        cancelled = DomainEvent.objects.get(event_name=V.BOOKING_CANCELLED)
        assert rescheduled.correlation_id == cancelled.correlation_id == corr
