"""Tests for the ingest cleanup tasks (PR #507 adversarial A8).

Pins the retention windows: DLQ 90d (replayed 30d) per §6.4, dedupe
120d per §5.3. Backdates the time-stamped fields via
``Model.objects.filter(pk=...).update(field=...)`` because
``auto_now_add`` blocks direct insertion of past dates.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.eventbus.cleanup_tasks import (
    DEDUPE_RETENTION_DAYS,
    DLQ_RETENTION_DAYS,
    DLQ_REPLAYED_RETENTION_DAYS,
    cleanup_ingest_dedupe,
    cleanup_ingest_dlq,
)
from apps.eventbus.models import IngestDedupe, IngestDLQ


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
