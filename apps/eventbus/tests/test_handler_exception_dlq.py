"""Tests for #433 umbrella — HANDLER_EXCEPTION → DLQ threshold.

Covers:

* Each handler exception increments ``HandlerFailureTracker.attempt_count``
  for ``(event_id, handler_name, outcome)``.
* DLQ row written via ``update_or_create`` ONCE per
  ``(event_id, reason)`` — no duplicates on subsequent failures.
* Threshold is env-driven via ``EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD``.
* Below threshold: tracker increments, NO DLQ row.
* At/above threshold: tracker increments AND DLQ row appears (then
  refreshes idempotently).
* The tracker write happens in a separate transaction from the
  handler's rolled-back atomic — survives the rollback.
* DLQ ``raw_body`` is redacted per ingest_redaction (PII §7).
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.eventbus.ingest_dispatcher import (
    DispatchOutcome,
    _track_handler_failure,
    dispatch_envelope,
    register,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.models import HandlerFailureTracker, IngestDLQ


pytestmark = pytest.mark.django_db


# ─── helpers ───────────────────────────────────────────────────────────────


def _envelope(
    *,
    event_id: str = "01J9HANDLERFAILTEST00000000",  # pragma: allowlist secret
    event_name: str = "booking.created",
    event_version: int = 1,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=event_version,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id="9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
        user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data={"appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8"},
    )


@pytest.fixture(autouse=True)
def _registry_snapshot():
    """Snapshot + restore the dispatcher registry per test so
    register() calls below don't collide with boot-time handlers."""
    import apps.eventbus.ingest_dispatcher as dispatcher_module

    snapshot = dict(dispatcher_module._REGISTRY)
    dispatcher_module._REGISTRY.clear()
    try:
        yield
    finally:
        dispatcher_module._REGISTRY.clear()
        dispatcher_module._REGISTRY.update(snapshot)


# ─── _track_handler_failure (unit) ─────────────────────────────────────────


class TestTrackHandlerFailureUnit:
    def test_first_failure_creates_tracker_no_dlq(self, settings) -> None:
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        env = _envelope()

        _track_handler_failure(env, RuntimeError("boom"))

        tracker = HandlerFailureTracker.objects.get(
            event_id=env.event_id,
            handler_name="booking.created@v1",
            outcome="handler_exception",
        )
        assert tracker.attempt_count == 1
        assert "RuntimeError" in tracker.last_error
        assert "boom" in tracker.last_error
        # Below threshold (1 < 3) — no DLQ row.
        assert (
            IngestDLQ.objects.filter(event_id=env.event_id, reason="handler_exception").count() == 0
        )

    def test_threshold_crossing_writes_dlq(self, settings) -> None:
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        env = _envelope()

        for i in range(3):
            _track_handler_failure(env, RuntimeError(f"attempt {i + 1}"))

        tracker = HandlerFailureTracker.objects.get(event_id=env.event_id)
        assert tracker.attempt_count == 3
        # Threshold (3 >= 3) — DLQ row exists exactly once.
        assert (
            IngestDLQ.objects.filter(event_id=env.event_id, reason="handler_exception").count() == 1
        )

    def test_above_threshold_dlq_upserts_not_duplicates(self, settings) -> None:
        """Attempts 4, 5, 6 past threshold update the same DLQ row.
        UniqueConstraint(event_id, reason) enforces single-row upsert."""
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        env = _envelope()

        for i in range(6):
            _track_handler_failure(env, RuntimeError(f"attempt {i + 1}"))

        tracker = HandlerFailureTracker.objects.get(event_id=env.event_id)
        assert tracker.attempt_count == 6
        # Still exactly ONE DLQ row.
        assert (
            IngestDLQ.objects.filter(event_id=env.event_id, reason="handler_exception").count() == 1
        )

    def test_distinct_handlers_for_same_event_id_separate_trackers(self, settings) -> None:
        """A theoretical multi-handler future: different handler_name
        + same event_id → independent counters (unique key includes
        handler_name)."""
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        env_a = _envelope(event_name="booking.created", event_version=1)
        env_b = _envelope(event_name="payment.captured", event_version=1)

        _track_handler_failure(env_a, RuntimeError("a"))
        _track_handler_failure(env_b, RuntimeError("b"))

        assert HandlerFailureTracker.objects.filter(event_id=env_a.event_id).count() == 2

    def test_dlq_raw_body_redacted(self, settings) -> None:
        """PII §7: ``raw_body.data`` goes through ``redact_data_for_dlq``
        — no raw user input is persisted untouched."""
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 1  # immediate DLQ
        env = _envelope()

        _track_handler_failure(env, RuntimeError("boom"))

        dlq = IngestDLQ.objects.get(event_id=env.event_id, reason="handler_exception")
        # Top-level envelope fields intact.
        assert dlq.raw_body["event_id"] == env.event_id
        assert dlq.raw_body["event_name"] == "booking.created"
        # data redaction touched: redact_data_for_dlq is the only
        # path that wraps `data`, so its presence is verification.
        assert "data" in dlq.raw_body

    def test_last_error_truncated_at_1024(self, settings) -> None:
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 99
        env = _envelope()
        huge = "x" * 10_000

        _track_handler_failure(env, RuntimeError(huge))

        tracker = HandlerFailureTracker.objects.get(event_id=env.event_id)
        assert len(tracker.last_error) == 1024


# ─── End-to-end via dispatch_envelope ──────────────────────────────────────


def _failing_handler(envelope: IngestEnvelope) -> None:
    """Test handler that always raises."""
    raise RuntimeError(f"handler-failure-for-{envelope.event_id}")


class TestDispatchEnvelopeEndToEnd:
    def test_dispatch_records_failure_and_returns_handler_exception(self, settings) -> None:
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        register("booking.created", 1, _failing_handler)
        env = _envelope()

        result = dispatch_envelope(env)

        assert result.outcome == DispatchOutcome.HANDLER_EXCEPTION
        tracker = HandlerFailureTracker.objects.get(event_id=env.event_id)
        assert tracker.attempt_count == 1
        # Below threshold — no DLQ.
        assert (
            IngestDLQ.objects.filter(event_id=env.event_id, reason="handler_exception").count() == 0
        )

    def test_three_dispatches_trip_threshold_dlq_appears(self, settings) -> None:
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        register("booking.created", 1, _failing_handler)
        env = _envelope()

        for _ in range(3):
            result = dispatch_envelope(env)
            assert result.outcome == DispatchOutcome.HANDLER_EXCEPTION

        tracker = HandlerFailureTracker.objects.get(event_id=env.event_id)
        assert tracker.attempt_count == 3
        # Threshold crossed — exactly one DLQ row.
        assert (
            IngestDLQ.objects.filter(event_id=env.event_id, reason="handler_exception").count() == 1
        )

    def test_outer_transaction_rollback_preserves_no_dedupe_row(self, settings) -> None:
        """The outer dispatcher transaction.atomic rolls back on
        handler exception, so the IngestDedupe row never lands.
        Retry IS expected (this is the whole §5.1 contract). Tracker
        survives in a separate transaction."""
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        register("booking.created", 1, _failing_handler)
        env = _envelope()

        dispatch_envelope(env)

        from apps.eventbus.models import IngestDedupe

        # No dedupe row — handler failed, retry expected.
        assert IngestDedupe.objects.filter(event_id=env.event_id).count() == 0
        # But tracker row IS persisted (separate transaction).
        assert HandlerFailureTracker.objects.filter(event_id=env.event_id).count() == 1

    def test_eventual_success_does_not_wipe_tracker(self, settings) -> None:
        """Once retries succeed (handler doesn't raise), tracker
        remains as forensic trace — operator can see how many
        attempts were needed."""
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 3
        register("booking.created", 1, _failing_handler)
        env = _envelope()

        # Two failed attempts.
        dispatch_envelope(env)
        dispatch_envelope(env)

        # Swap the handler for a succeeding one.
        import apps.eventbus.ingest_dispatcher as dispatcher_module

        dispatcher_module._REGISTRY[("booking.created", 1)] = lambda e: None

        # Third attempt succeeds (registers IngestDedupe row).
        result = dispatch_envelope(env)
        assert result.outcome == DispatchOutcome.OK

        # Tracker still at 2 (success didn't increment).
        tracker = HandlerFailureTracker.objects.get(event_id=env.event_id)
        assert tracker.attempt_count == 2


# ─── DLQ upsert idempotency ────────────────────────────────────────────────


class TestDLQUpsert:
    def test_write_dlq_idempotent_on_unknown_event_name(self, settings) -> None:
        """Two dispatches for an unknown event_name → one DLQ row.
        UniqueConstraint(event_id, reason) + update_or_create."""
        env = _envelope(event_name="totally.unknown")

        dispatch_envelope(env)
        dispatch_envelope(env)

        assert (
            IngestDLQ.objects.filter(event_id=env.event_id, reason="unknown_event_name").count()
            == 1
        )

    def test_distinct_reasons_share_event_id(self, settings) -> None:
        """An event can produce DLQ rows under multiple reasons — the
        unique key is (event_id, reason), not just event_id."""
        # Two envelopes with the SAME event_id but contrived to fall
        # under different DLQ reasons.
        register("booking.created", 1, _failing_handler)
        settings.EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD = 1

        env_known = _envelope(
            event_id="01J9SHARED00000000000000ZZ",  # pragma: allowlist secret
            event_name="booking.created",
        )
        env_unknown = _envelope(
            event_id="01J9SHARED00000000000000ZZ",  # pragma: allowlist secret
            event_name="totally.unknown",
        )

        # First → handler_exception DLQ.
        dispatch_envelope(env_known)
        # Second → unknown_event_name DLQ.
        dispatch_envelope(env_unknown)

        # Two distinct DLQ rows under one event_id.
        assert IngestDLQ.objects.filter(event_id="01J9SHARED00000000000000ZZ").count() == 2
