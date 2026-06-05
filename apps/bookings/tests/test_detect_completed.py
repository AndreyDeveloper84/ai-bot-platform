"""booking.completed producer (Phase 2.3).

Tests the Celery task ``apps.bookings.tasks.detect_completed_bookings``:
- emits ``booking.completed`` for confirmed bookings past visit_at + duration + grace
- idempotent: second invocation finds nothing
- skips cancelled / rescheduled / already-completed rows
- respects the grace window
- emit failure rolls back the completed_at stamp (next tick retries)
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingRequest
from apps.bookings.tasks import (
    detect_completed_bookings,
)
from apps.eventbus.models import DomainEvent
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="comp-t", name="CompletedTenant")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="comp-bot")


def _make_booking(
    tenant,
    customer,
    *,
    visit_offset_minutes: int,
    duration_min: int = 60,
    status: str = BookingRequest.Status.CONFIRMED,
    completed_at: dt.datetime | None = None,
) -> BookingRequest:
    """Create a booking whose visit_at is (now + offset) minutes."""
    visit_at = timezone.now() + dt.timedelta(minutes=visit_offset_minutes)
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=customer,
        service_name="Маникюр",
        client_name="Customer",
        client_phone="snapshot",
        visit_at=visit_at,
        duration_min=duration_min,
        status=status,
        completed_at=completed_at,
        source="bot",
        booking_source="external",
    )


class TestEmits:
    def test_emits_for_past_visit_with_grace_elapsed(self, tenant, customer):
        # visit ended 2h ago + 60min duration + 30min grace = 3.5h ago
        # Visit_at = -210 min (3.5h ago); duration 60; → ended -150 min ago.
        # Threshold = visit_at + duration + grace = -210 + 60 + 30 = -120 min.
        # -120 < 0 → eligible.
        _make_booking(tenant, customer, visit_offset_minutes=-210, duration_min=60)

        result = detect_completed_bookings()

        assert result["emitted"] == 1
        assert DomainEvent.objects.filter(event_name="booking.completed").count() == 1

    def test_payload_matches_taxonomy_3_1(self, tenant, customer):
        booking = _make_booking(tenant, customer, visit_offset_minutes=-180, duration_min=60)

        detect_completed_bookings()

        ev = DomainEvent.objects.get(event_name="booking.completed")
        assert ev.data["booking_id"] == str(booking.pk)
        assert ev.data["completed_at"]
        assert ev.data["marked_by"] == "system"
        assert ev.tenant_id == tenant.id


class TestSkipsAndGrace:
    def test_visit_still_in_progress_skipped(self, tenant, customer):
        # Visit ended 10 min ago, duration 60, grace 30. Threshold = visit_at
        # + 60 + 30 = -10 + 60 + 30 = +80 — still future. Skip.
        # Actually: visit_at offset = -10 → visit ENDED at -10 + 60 = +50 min.
        # We need visit_at + duration + grace ≤ now → +50 + 30 = +80 > 0 → skip.
        _make_booking(tenant, customer, visit_offset_minutes=-10, duration_min=60)

        result = detect_completed_bookings()

        assert result["emitted"] == 0
        assert not DomainEvent.objects.filter(event_name="booking.completed").exists()

    def test_visit_just_ended_but_grace_not_elapsed_skipped(self, tenant, customer):
        # Visit ended 5 min ago (60 min visit, started 65 min ago).
        # visit_at offset = -65 → visit ended at -65 + 60 = -5 min.
        # Need: visit_end + grace ≤ now → -5 + 30 = +25 > 0 → skip.
        _make_booking(tenant, customer, visit_offset_minutes=-65, duration_min=60)

        result = detect_completed_bookings()

        assert result["emitted"] == 0

    def test_cancelled_booking_skipped(self, tenant, customer):
        _make_booking(
            tenant,
            customer,
            visit_offset_minutes=-300,
            status=BookingRequest.Status.CANCELLED,
        )

        result = detect_completed_bookings()

        assert result["emitted"] == 0
        # No completed_at stamped either.
        booking = BookingRequest.all_tenants.get()
        assert booking.completed_at is None

    def test_already_completed_booking_skipped(self, tenant, customer):
        _make_booking(
            tenant,
            customer,
            visit_offset_minutes=-300,
            completed_at=timezone.now() - dt.timedelta(hours=1),
        )

        result = detect_completed_bookings()

        assert result["emitted"] == 0

    def test_rescheduled_booking_skipped(self, tenant, customer):
        _make_booking(
            tenant,
            customer,
            visit_offset_minutes=-300,
            status=BookingRequest.Status.RESCHEDULED,
        )

        result = detect_completed_bookings()

        assert result["emitted"] == 0


class TestIdempotency:
    def test_second_invocation_emits_nothing(self, tenant, customer):
        _make_booking(tenant, customer, visit_offset_minutes=-300)

        first = detect_completed_bookings()
        second = detect_completed_bookings()

        assert first["emitted"] == 1
        assert second["emitted"] == 0
        assert DomainEvent.objects.filter(event_name="booking.completed").count() == 1

    def test_completed_at_stamped(self, tenant, customer):
        booking = _make_booking(tenant, customer, visit_offset_minutes=-300)
        before = timezone.now()

        detect_completed_bookings()

        booking.refresh_from_db()
        assert booking.completed_at is not None
        assert booking.completed_at >= before


class TestEmitFailureRollback:
    def test_emit_failure_unstamps_completed_at(self, tenant, customer):
        # If eventbus emit fails, the row should NOT carry a stale
        # completed_at — the next tick must retry. Otherwise we'd lose
        # the booking.completed signal forever (and Loyalty would never
        # credit points).
        _make_booking(tenant, customer, visit_offset_minutes=-300)

        with patch(
            "apps.eventbus.services.emit",
            side_effect=RuntimeError("simulated bus outage"),
        ):
            result = detect_completed_bookings()

        assert result["emitted"] == 0
        booking = BookingRequest.all_tenants.get()
        assert booking.completed_at is None  # rolled back

        # Phase 2.2 cleanup #4: failure increments emit_failed and
        # leaves scanned untouched (scanned == emitted + raced + emit_failed).
        assert result["emit_failed"] == 1
        assert result["scanned"] == result["emitted"] + result["raced"] + result["emit_failed"]

        # And the next tick (without the patched failure) does emit.
        result = detect_completed_bookings()
        assert result["emitted"] == 1
        assert result["emit_failed"] == 0


class TestGraceTunable:
    def test_zero_grace_emits_immediately_after_visit_end(self, tenant, customer, monkeypatch):
        # Drop grace to 0 — visit ended 1 min ago should now qualify.
        monkeypatch.setattr("apps.bookings.tasks.COMPLETED_GRACE_MINUTES", 0)
        _make_booking(tenant, customer, visit_offset_minutes=-65, duration_min=60)

        result = detect_completed_bookings()

        # Visit ended at offset -5 min (65 - 60). Without grace, eligible.
        assert result["emitted"] == 1
