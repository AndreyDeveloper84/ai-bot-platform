"""Outbox dispatcher — claim, mark, retry, dead-letter, replay, alert."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import (
    MAX_ATTEMPTS,
    dispatch_pending_events,
    replay_dead_letter,
)
from apps.eventbus.envelope import Envelope
from apps.eventbus.models import DomainEvent

pytestmark = pytest.mark.django_db(transaction=True)


def _emit_three():
    for i in range(3):
        services.emit(
            V.BOOKING_CREATED,
            {
                "booking_id": f"b{i}",
                "customer_id": f"c{i}",
                "service_id": "s",
                "slot_start": "2026-05-19T10:00:00Z",
                "booking_source": "ai_direct",
            },
            actor_type="system",
        )


class _BoomSubscriber:
    def handle(self, envelope: Envelope) -> None:
        raise RuntimeError("boom")


class TestDispatcherHappyPath:
    def test_marks_pending_rows_dispatched(self):
        _emit_three()
        assert DomainEvent.objects.filter(is_dispatched=False).count() == 3

        counters = dispatch_pending_events()

        assert counters["claimed"] == 3
        assert counters["dispatched"] == 3
        assert counters["failed"] == 0
        assert DomainEvent.objects.filter(is_dispatched=True).count() == 3
        for row in DomainEvent.objects.filter(event_name=V.BOOKING_CREATED):
            assert row.dispatched_at is not None
            assert row.dead_lettered_at is None

    def test_second_run_finds_nothing(self):
        _emit_three()
        dispatch_pending_events()
        # Filter out the health-degraded event the alerter may have emitted.
        counters = dispatch_pending_events()
        assert counters["claimed"] == 0


class TestDispatcherFailure:
    def test_subscriber_error_increments_attempts(self):
        _emit_three()
        with patch.object(dispatcher, "_subscribers", return_value=[_BoomSubscriber()]):
            counters = dispatch_pending_events()

        assert counters["dispatched"] == 0
        assert counters["failed"] == 3
        assert counters["dead_letter"] == 0
        for row in DomainEvent.objects.filter(event_name=V.BOOKING_CREATED):
            assert row.is_dispatched is False
            assert row.dispatch_attempts == 1
            assert "boom" in row.last_error
            assert row.dead_lettered_at is None


class TestDeadLetter:
    def test_dead_lettered_at_set_on_max_attempts(self):
        _emit_three()
        with patch.object(dispatcher, "_subscribers", return_value=[_BoomSubscriber()]):
            for _ in range(MAX_ATTEMPTS):
                dispatch_pending_events()

        for row in DomainEvent.objects.filter(event_name=V.BOOKING_CREATED):
            assert row.dispatch_attempts == MAX_ATTEMPTS
            assert row.dead_lettered_at is not None
            assert row.is_dead_letter is True

    def test_dlq_rows_excluded_from_claim(self):
        _emit_three()
        with patch.object(dispatcher, "_subscribers", return_value=[_BoomSubscriber()]):
            for _ in range(MAX_ATTEMPTS):
                dispatch_pending_events()

        # Booking rows are dead-lettered → MUST NOT be re-claimed.
        # (Alert events from the threshold-crossing also exist as
        # pending rows; those are unrelated to the DLQ-exclusion check.)
        booking_still_claimable = DomainEvent.objects.filter(
            event_name=V.BOOKING_CREATED,
            is_dispatched=False,
            dead_lettered_at__isnull=True,
        ).count()
        assert booking_still_claimable == 0

    def test_dlq_count_in_run_reports_threshold_crossings_only(self):
        _emit_three()
        with patch.object(dispatcher, "_subscribers", return_value=[_BoomSubscriber()]):
            # Attempts 1 and 2 — no DLQ crossings.
            c1 = dispatch_pending_events()
            c2 = dispatch_pending_events()
            assert c1["dead_letter"] == 0
            assert c2["dead_letter"] == 0
            # Attempt 3 — all 3 cross threshold this run.
            c3 = dispatch_pending_events()
            assert c3["dead_letter"] == 3


class TestReplay:
    def _drive_to_dlq(self):
        _emit_three()
        with patch.object(dispatcher, "_subscribers", return_value=[_BoomSubscriber()]):
            for _ in range(MAX_ATTEMPTS):
                dispatch_pending_events()

    def test_replay_resets_state(self):
        self._drive_to_dlq()
        ids = list(
            DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).values_list(
                "event_id", flat=True
            )
        )

        reset_count = replay_dead_letter(ids)

        assert reset_count == 3
        for row in DomainEvent.objects.filter(event_name=V.BOOKING_CREATED):
            assert row.dead_lettered_at is None
            assert row.dispatch_attempts == 0
            assert row.last_error == ""

    def test_replay_only_touches_dlq_rows(self):
        # Healthy row — replay must skip it (it's not in DLQ).
        services.emit(
            V.BOOKING_CREATED,
            {
                "booking_id": "healthy",
                "customer_id": "c",
                "service_id": "s",
                "slot_start": "2026-05-19T10:00:00Z",
                "booking_source": "ai_direct",
            },
            actor_type="system",
        )
        healthy_id = DomainEvent.objects.get(data__booking_id="healthy").event_id

        reset_count = replay_dead_letter([healthy_id])

        assert reset_count == 0

    def test_replay_re_claimed_on_next_tick(self):
        self._drive_to_dlq()
        ids = list(
            DomainEvent.objects.filter(event_name=V.BOOKING_CREATED).values_list(
                "event_id", flat=True
            )
        )
        replay_dead_letter(ids)

        # Default Noop subscriber succeeds; replayed rows dispatch cleanly.
        # The alert events from the earlier DLQ crossing also sit pending
        # and will dispatch on this tick — we assert on the booking rows
        # specifically.
        dispatch_pending_events()

        booking_dispatched = DomainEvent.objects.filter(
            event_name=V.BOOKING_CREATED, is_dispatched=True
        ).count()
        assert booking_dispatched == 3


class TestDlqAlert:
    def test_threshold_crossing_emits_health_degraded(self):
        _emit_three()
        with patch.object(dispatcher, "_subscribers", return_value=[_BoomSubscriber()]):
            for _ in range(MAX_ATTEMPTS):
                dispatch_pending_events()

        # The threshold-crossing run was the third one. It should have
        # emitted a system.module.health.degraded event AFTER quarantine.
        alerts = DomainEvent.objects.filter(event_name=V.SYSTEM_MODULE_HEALTH_DEGRADED)
        assert alerts.exists()
        alert = alerts.latest("event_id")
        assert alert.data["module_name"] == "eventbus.dispatcher"
        assert alert.data["severity"] == "warning"
        assert "dlq_count_in_run=3" in alert.data["metric"]
        # tenant-less per taxonomy §8 system.* exception
        assert alert.tenant_id is None

    def test_no_alert_on_clean_run(self):
        _emit_three()
        dispatch_pending_events()  # Noop subscriber — no failures
        # No health-degraded event should have been emitted.
        assert not DomainEvent.objects.filter(event_name=V.SYSTEM_MODULE_HEALTH_DEGRADED).exists()
