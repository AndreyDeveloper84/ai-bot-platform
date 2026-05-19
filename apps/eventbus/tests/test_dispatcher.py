"""Outbox dispatcher — claim, mark, retry, dead-letter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.eventbus import dispatcher, services, vocabulary as V
from apps.eventbus.dispatcher import MAX_ATTEMPTS, dispatch_pending_events
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


class TestDispatcherHappyPath:
    def test_marks_pending_rows_dispatched(self):
        _emit_three()
        assert DomainEvent.objects.filter(is_dispatched=False).count() == 3

        counters = dispatch_pending_events()

        assert counters["claimed"] == 3
        assert counters["dispatched"] == 3
        assert counters["failed"] == 0
        assert DomainEvent.objects.filter(is_dispatched=True).count() == 3
        for row in DomainEvent.objects.all():
            assert row.dispatched_at is not None

    def test_second_run_finds_nothing(self):
        _emit_three()
        dispatch_pending_events()
        counters = dispatch_pending_events()
        assert counters["claimed"] == 0


class TestDispatcherFailure:
    def test_subscriber_error_increments_attempts(self):
        _emit_three()

        class BoomSubscriber:
            def handle(self, envelope: Envelope) -> None:
                raise RuntimeError("boom")

        with patch.object(dispatcher, "_subscribers", return_value=[BoomSubscriber()]):
            counters = dispatch_pending_events()

        assert counters["dispatched"] == 0
        assert counters["failed"] == 3
        for row in DomainEvent.objects.all():
            assert row.is_dispatched is False
            assert row.dispatch_attempts == 1
            assert "boom" in row.last_error

    def test_dead_letter_after_max_attempts(self):
        _emit_three()

        class BoomSubscriber:
            def handle(self, envelope: Envelope) -> None:
                raise RuntimeError("boom")

        with patch.object(dispatcher, "_subscribers", return_value=[BoomSubscriber()]):
            for _ in range(MAX_ATTEMPTS):
                dispatch_pending_events()

        # After MAX_ATTEMPTS attempts, dispatcher stops re-claiming them.
        for row in DomainEvent.objects.all():
            assert row.dispatch_attempts == MAX_ATTEMPTS
            assert row.is_dispatched is False

        # Next run claims nothing — dead-letter rows are excluded.
        counters = dispatch_pending_events()
        assert counters["claimed"] == 0
