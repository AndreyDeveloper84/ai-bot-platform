"""Tests for the ingest cleanup tasks (PR #507 adversarial A8).

Pins the retention windows: DLQ 90d (replayed 30d) per §6.4, dedupe
120d per §5.3. Backdates the time-stamped fields via
``Model.objects.filter(pk=...).update(field=...)`` because
``auto_now_add`` blocks direct insertion of past dates.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import models
from django.utils import timezone

from apps.eventbus.cleanup_tasks import (
    DEDUPE_RETENTION_DAYS,
    DLQ_RETENTION_DAYS,
    DLQ_REPLAYED_RETENTION_DAYS,
    SECONDARY_LEDGER_RETENTION_DAYS,
    _chunked_delete_older_than,
    cleanup_ingest_dedupe,
    cleanup_ingest_dlq,
    cleanup_ingest_secondary_ledgers,
)
from apps.eventbus.models import (
    HandlerFailureTracker,
    IngestDedupe,
    IngestDLQ,
    NotificationDispatchDedupe,
    PaymentTerminalDedupe,
    ReviewProcessedDedupe,
)


pytestmark = pytest.mark.django_db


# ─── helpers ───────────────────────────────────────────────────────────────


def _make_dlq(
    *,
    event_id: str,
    dead_lettered_days_ago: int,
    replayed_days_ago: int | None = None,
) -> IngestDLQ:
    """Create an IngestDLQ row and backdate timestamps via update()."""
    row = IngestDLQ.objects.create(
        event_id=event_id,
        event_name="booking.created",
        event_version=1,
        reason="unknown_event_name",
        raw_body={"event_id": event_id},
    )
    when_dead = timezone.now() - timedelta(days=dead_lettered_days_ago)
    update_kwargs = {"dead_lettered_at": when_dead}
    if replayed_days_ago is not None:
        update_kwargs["replayed_at"] = timezone.now() - timedelta(days=replayed_days_ago)
    IngestDLQ.objects.filter(pk=row.pk).update(**update_kwargs)
    return row


def _make_dedupe(*, event_id: str, received_days_ago: int) -> IngestDedupe:
    row = IngestDedupe.objects.create(
        event_id=event_id,
        event_name="booking.created",
        event_version=1,
        processed_at=timezone.now() - timedelta(days=received_days_ago),
    )
    IngestDedupe.objects.filter(pk=row.pk).update(
        received_at=timezone.now() - timedelta(days=received_days_ago),
    )
    return row


# ─── DLQ cleanup ───────────────────────────────────────────────────────────


class TestCleanupIngestDLQ:
    def test_deletes_unreplayed_rows_past_90_days(self) -> None:
        _make_dlq(event_id="OLD" + "0" * 23, dead_lettered_days_ago=100)
        _make_dlq(event_id="FRESH" + "0" * 21, dead_lettered_days_ago=10)

        result = cleanup_ingest_dlq()

        assert result["deleted_aged"] == 1
        assert IngestDLQ.objects.filter(event_id__startswith="OLD").count() == 0
        assert IngestDLQ.objects.filter(event_id__startswith="FRESH").count() == 1

    def test_deletes_replayed_rows_past_30_days(self) -> None:
        # Replayed long ago: should be deleted as "replayed cleanup".
        _make_dlq(
            event_id="REPLAYED_OLD" + "0" * 14,
            dead_lettered_days_ago=40,
            replayed_days_ago=35,
        )
        # Replayed recently: kept.
        _make_dlq(
            event_id="REPLAYED_NEW" + "0" * 14,
            dead_lettered_days_ago=10,
            replayed_days_ago=5,
        )

        result = cleanup_ingest_dlq()

        assert result["deleted_replayed"] == 1
        assert IngestDLQ.objects.filter(event_id__startswith="REPLAYED_OLD").count() == 0
        assert IngestDLQ.objects.filter(event_id__startswith="REPLAYED_NEW").count() == 1

    def test_retention_boundaries_pin_to_contract(self) -> None:
        """§6.4 = 90 days for unreplayed; replayed = 30 days (this PR's choice)."""
        assert DLQ_RETENTION_DAYS == 90
        assert DLQ_REPLAYED_RETENTION_DAYS == 30

    def test_idempotent_second_run_deletes_nothing(self) -> None:
        """Running the task twice in a row produces the same final state."""
        _make_dlq(event_id="OLD" + "0" * 23, dead_lettered_days_ago=100)

        cleanup_ingest_dlq()
        result_second = cleanup_ingest_dlq()

        assert result_second["deleted_aged"] == 0
        assert result_second["deleted_replayed"] == 0

    def test_empty_table_runs_clean(self) -> None:
        result = cleanup_ingest_dlq()
        assert result == {"deleted_replayed": 0, "deleted_aged": 0}


# ─── Dedupe cleanup ────────────────────────────────────────────────────────


class TestCleanupIngestDedupe:
    def test_deletes_rows_past_120_days(self) -> None:
        _make_dedupe(event_id="OLD" + "0" * 23, received_days_ago=130)
        _make_dedupe(event_id="FRESH" + "0" * 21, received_days_ago=60)

        result = cleanup_ingest_dedupe()

        assert result["deleted"] == 1
        assert IngestDedupe.objects.filter(event_id__startswith="OLD").count() == 0
        assert IngestDedupe.objects.filter(event_id__startswith="FRESH").count() == 1

    def test_retention_boundary_pins_to_contract(self) -> None:
        """§5.3 = 120 days, exceeding max(DLQ 90d, deprecation 30d) + safety margin."""
        assert DEDUPE_RETENTION_DAYS == 120

    def test_idempotent_second_run(self) -> None:
        _make_dedupe(event_id="OLD" + "0" * 23, received_days_ago=130)

        cleanup_ingest_dedupe()
        second = cleanup_ingest_dedupe()

        assert second == {"deleted": 0}

    def test_empty_table_runs_clean(self) -> None:
        assert cleanup_ingest_dedupe() == {"deleted": 0}


# ─── Secondary ledger cleanup (#1056) ────────────────────────────────────────


def _backdate(model: type[models.Model], pk: object, field: str, days_ago: int) -> None:
    """Push a timestamp field into the past via update() (bypasses
    auto_now / auto_now_add which block direct insertion of past dates)."""
    model._base_manager.filter(pk=pk).update(**{field: timezone.now() - timedelta(days=days_ago)})


def _make_payment_terminal(*, processed_days_ago: int) -> PaymentTerminalDedupe:
    row = PaymentTerminalDedupe.objects.create(
        tenant_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        terminal_state=PaymentTerminalDedupe.TerminalState.CAPTURED,
        event_id=str(uuid.uuid4()),
    )
    _backdate(PaymentTerminalDedupe, row.pk, "processed_at", processed_days_ago)
    return row


def _make_review(*, processed_days_ago: int) -> ReviewProcessedDedupe:
    row = ReviewProcessedDedupe.objects.create(
        tenant_id=uuid.uuid4(),
        review_id=uuid.uuid4(),
        event_id=str(uuid.uuid4()),
    )
    _backdate(ReviewProcessedDedupe, row.pk, "processed_at", processed_days_ago)
    return row


def _make_notification(*, dispatched_days_ago: int) -> NotificationDispatchDedupe:
    row = NotificationDispatchDedupe.objects.create(
        tenant_id=uuid.uuid4(),
        event_id=str(uuid.uuid4()),
        recipient_id=uuid.uuid4(),
        channel="max",
        kind="payment_failed",
    )
    _backdate(NotificationDispatchDedupe, row.pk, "dispatched_at", dispatched_days_ago)
    return row


def _make_failure(
    *, last_attempt_days_ago: int, first_attempt_days_ago: int | None = None
) -> HandlerFailureTracker:
    row = HandlerFailureTracker.objects.create(
        event_id=str(uuid.uuid4()),
        handler_name="booking.created@v1",
        attempt_count=1,
    )
    _backdate(HandlerFailureTracker, row.pk, "last_attempt_at", last_attempt_days_ago)
    if first_attempt_days_ago is not None:
        _backdate(HandlerFailureTracker, row.pk, "first_attempt_at", first_attempt_days_ago)
    return row


_ALL_ZERO = {
    "PaymentTerminalDedupe": 0,
    "ReviewProcessedDedupe": 0,
    "NotificationDispatchDedupe": 0,
    "HandlerFailureTracker": 0,
}


class TestCleanupSecondaryLedgers:
    def test_deletes_all_four_ledgers_past_120_days(self) -> None:
        """Old rows (130d) go; fresh (60d) stay — across all four models."""
        _make_payment_terminal(processed_days_ago=130)
        _make_payment_terminal(processed_days_ago=60)
        _make_review(processed_days_ago=130)
        _make_review(processed_days_ago=60)
        _make_notification(dispatched_days_ago=130)
        _make_notification(dispatched_days_ago=60)
        _make_failure(last_attempt_days_ago=130)
        _make_failure(last_attempt_days_ago=60)

        result = cleanup_ingest_secondary_ledgers()

        assert result == {
            "PaymentTerminalDedupe": 1,
            "ReviewProcessedDedupe": 1,
            "NotificationDispatchDedupe": 1,
            "HandlerFailureTracker": 1,
        }
        assert PaymentTerminalDedupe.objects.count() == 1
        assert ReviewProcessedDedupe.objects.count() == 1
        assert NotificationDispatchDedupe.objects.count() == 1
        assert HandlerFailureTracker.objects.count() == 1

    def test_retention_boundary_pins_to_contract(self) -> None:
        """§5.3 = 120 days, same window as IngestDedupe."""
        assert SECONDARY_LEDGER_RETENTION_DAYS == 120

    def test_boundary_just_over_vs_just_under(self) -> None:
        """121d deleted, 119d kept — the cutoff is `now - 120d` exclusive."""
        _make_review(processed_days_ago=121)
        _make_review(processed_days_ago=119)

        result = cleanup_ingest_secondary_ledgers()

        assert result["ReviewProcessedDedupe"] == 1
        assert ReviewProcessedDedupe.objects.count() == 1

    def test_still_retrying_failure_is_retained(self) -> None:
        """HandlerFailureTracker ages on last_attempt_at: a row first seen
        long ago (130d) but retried recently (5d) MUST be kept — otherwise
        an event still inside Ayla's retry budget loses its counter."""
        row = _make_failure(last_attempt_days_ago=5, first_attempt_days_ago=130)

        result = cleanup_ingest_secondary_ledgers()

        assert result["HandlerFailureTracker"] == 0
        assert HandlerFailureTracker.objects.filter(pk=row.pk).exists()

    def test_idempotent_second_run_deletes_nothing(self) -> None:
        _make_review(processed_days_ago=130)

        cleanup_ingest_secondary_ledgers()
        second = cleanup_ingest_secondary_ledgers()

        assert second == _ALL_ZERO

    def test_empty_tables_run_clean(self) -> None:
        assert cleanup_ingest_secondary_ledgers() == _ALL_ZERO

    def test_chunked_delete_loops_over_multiple_pages(self) -> None:
        """The chunked helper deletes the whole backlog even when it
        exceeds one page — proves the loop, not a single bounded DELETE."""
        for _ in range(5):
            _make_review(processed_days_ago=130)
        cutoff = timezone.now() - timedelta(days=SECONDARY_LEDGER_RETENTION_DAYS)

        deleted = _chunked_delete_older_than(
            ReviewProcessedDedupe, "processed_at", cutoff, chunk_size=2
        )

        assert deleted == 5
        assert ReviewProcessedDedupe.objects.count() == 0

    def test_sets_beat_heartbeat_marker(self) -> None:
        from django.core.cache import cache

        from apps.eventbus.cleanup_tasks import _SECONDARY_BEAT_LAST_RUN_CACHE_KEY

        cache.delete(_SECONDARY_BEAT_LAST_RUN_CACHE_KEY)
        cleanup_ingest_secondary_ledgers()
        assert cache.get(_SECONDARY_BEAT_LAST_RUN_CACHE_KEY) is not None

    def test_one_model_failure_isolated_and_marker_withheld(self, monkeypatch) -> None:
        """A poisoned ledger records -1 but MUST NOT starve the others,
        and the beat heartbeat is withheld so the health check flags it."""
        from django.core.cache import cache

        from apps.eventbus import cleanup_tasks as ct

        cache.delete(ct._SECONDARY_BEAT_LAST_RUN_CACHE_KEY)

        def _fake_sweep(model, ts_field, cutoff, **kwargs):  # type: ignore[no-untyped-def]
            if model is PaymentTerminalDedupe:
                raise RuntimeError("simulated ledger sweep failure")
            return 0

        monkeypatch.setattr(ct, "_chunked_delete_older_than", _fake_sweep)

        result = cleanup_ingest_secondary_ledgers()

        assert result["PaymentTerminalDedupe"] == -1  # failed → sentinel
        # The other three still swept despite the first one failing.
        assert result["ReviewProcessedDedupe"] == 0
        assert result["NotificationDispatchDedupe"] == 0
        assert result["HandlerFailureTracker"] == 0
        # Marker withheld → health check reports the miss.
        assert cache.get(ct._SECONDARY_BEAT_LAST_RUN_CACHE_KEY) is None
