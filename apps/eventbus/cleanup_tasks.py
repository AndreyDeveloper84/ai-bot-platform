"""Celery beat cleanup tasks for the cross-service ingest tables.

PR #507 adversarial pass A8. The :class:`IngestDLQ` table stores
full envelope ``data`` payloads indefinitely without cleanup; per
ADR-0011 §3.4 + 152-ФЗ a publisher redaction bug or v2 event with
new (un-redacted) fields = weeks of accumulating PII in the DLQ.

This module ships the retention sweeps as ``@shared_task`` functions
+ their beat schedule entries (added to
``config/settings/base.py::CELERY_BEAT_SCHEDULE``).

| Task | Source | Retention | Cadence |
|---|---|---|---|
| ``cleanup_ingest_dlq`` | ``IngestDLQ`` | 90 days (§6.4); replayed rows cleaned at 30d | Daily 04:45 UTC |
| ``cleanup_ingest_dedupe`` | ``IngestDedupe`` | 120 days (§5.3) | Daily 04:50 UTC |
| ``cleanup_ingest_secondary_ledgers`` | ``PaymentTerminalDedupe``, ``ReviewProcessedDedupe``, ``NotificationDispatchDedupe``, ``HandlerFailureTracker`` | 120 days (§5.3) | Daily 04:55 UTC |

### Secondary ledgers (#1056)

``cleanup_ingest_dlq`` + ``cleanup_ingest_dedupe`` bound the two
canonical ingest tables. The consumer-side second-layer guards
(``PaymentTerminalDedupe`` / ``ReviewProcessedDedupe`` /
``NotificationDispatchDedupe`` per §3, and ``HandlerFailureTracker``
per the #433 retry counter) each carry their own «Cleanup is a Celery
beat (follow-up)» retention note and would otherwise grow unbounded.
``cleanup_ingest_secondary_ledgers`` sweeps all four under the shared
120d window (§5.3) with a **chunked** delete (see
:func:`_chunked_delete_older_than`) so a large backlog never runs in one
long transaction.

Note: the primary ``cleanup_ingest_dlq`` / ``cleanup_ingest_dedupe``
sweeps still issue a single unbounded ``.delete()`` — they predate the
chunked helper. IngestDedupe is the largest table, so migrating those to
the helper is a worthwhile FOLLOW_UP; left out of #1056 to keep this diff
to the previously-uncovered ledgers.

### Why 90d for DLQ + 120d for dedupe

`event-contract.md` §5.3 explicitly: «Dedupe rows kept for 120 days.
Retention MUST be ≥ max(DLQ retention, deprecation window) + 30-day
safety margin. With current DLQ retention 90d and deprecation
window 30d, 120 days satisfies the inequality.»

### Why split into two tasks vs one

The dedupe and DLQ tables have different access patterns:

- DLQ is small (events that fail dispatch are rare) and is the PII-
  sensitive surface — operator triage may touch it within the 90d
  window, after that it's pure noise.
- Dedupe is large (one row per successful ingest) and is the
  hot-read surface for retry dedupe — the 120d horizon protects
  against replay-from-old-DLQ scenarios per §5.3.

Splitting lets ops scale them independently (DLQ stays small with
manual cleanup if needed; dedupe is a bulk archive sweep).

### Idempotency

Both tasks are idempotent — running twice in a row deletes nothing
on the second run (the cutoff window means same query, same set,
already gone).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Final

from celery import shared_task  # type: ignore[import-untyped]
from django.core.cache import cache
from django.db import models
from django.utils import timezone

from apps.eventbus.models import (
    HandlerFailureTracker,
    IngestDedupe,
    IngestDLQ,
    NotificationDispatchDedupe,
    PaymentTerminalDedupe,
    ReviewProcessedDedupe,
)


# Round-2 AS10 — beat-ran-recently marker cache keys.
# Set at task end; checked by ``health.check_cleanup_beat_healthy``.
# Cache is the right store: ephemeral, no migration, and the
# 25h staleness window aligns with the daily cadence regardless.
_DLQ_BEAT_LAST_RUN_CACHE_KEY = "eventbus:ingest:cleanup_dlq_last_ran_at"
_DEDUPE_BEAT_LAST_RUN_CACHE_KEY = "eventbus:ingest:cleanup_dedupe_last_ran_at"
_SECONDARY_BEAT_LAST_RUN_CACHE_KEY = "eventbus:ingest:cleanup_secondary_last_ran_at"
# Cache TTL > 25h so the absence of a recent run reads as "stale"
# (cache miss) rather than "not configured".
_BEAT_HEARTBEAT_TTL_S = 30 * 24 * 3600  # 30 days


logger = logging.getLogger(__name__)


# `event-contract.md` §6.4 — DLQ retention 90 days.
DLQ_RETENTION_DAYS: Final[int] = 90

# Replayed DLQ rows: the operator has acknowledged them. Shorter
# retention since the forensic value is captured in the replay's
# downstream effects (dedupe row + audit). Not in the spec; chosen
# to bound PII window without losing audit trail.
DLQ_REPLAYED_RETENTION_DAYS: Final[int] = 30

# `event-contract.md` §5.3 — dedupe retention 120 days.
DEDUPE_RETENTION_DAYS: Final[int] = 120

# #1056 — the consumer-side second-layer dedupe guards + the #433
# handler-failure counter all share IngestDedupe's 120d window (§5.3);
# their model docstrings say as much. Each is swept by its own
# «processed / last-active» timestamp so a row that's still being
# retried (recent last_attempt_at) is never dropped early.
SECONDARY_LEDGER_RETENTION_DAYS: Final[int] = 120

# Chunk size for the batch delete. A single unbounded
# ``DELETE ... WHERE ts < cutoff`` over a large backlog runs in one long
# transaction: it takes a ``RowExclusiveLock`` (which does NOT block
# other DML) but the long-running txn drives dead-tuple bloat, holds an
# open snapshot, and inflates replication lag until it commits. Deleting
# in bounded batches — each its own autocommitted statement — keeps every
# transaction short and lets autovacuum keep up.
_CLEANUP_CHUNK_SIZE: Final[int] = 1000

# (model, timestamp field to age on). The field is the model's own
# «when this row became final»: processed_at for the dedupe ledgers,
# dispatched_at for the notification ledger, last_attempt_at for the
# failure tracker (so still-retrying rows are retained until quiet).
_SECONDARY_LEDGERS: Final[tuple[tuple[type[models.Model], str], ...]] = (
    (PaymentTerminalDedupe, "processed_at"),
    (ReviewProcessedDedupe, "processed_at"),
    (NotificationDispatchDedupe, "dispatched_at"),
    (HandlerFailureTracker, "last_attempt_at"),
)


@shared_task(name="apps.eventbus.cleanup_ingest_dlq")
def cleanup_ingest_dlq() -> dict[str, int]:
    """Sweep IngestDLQ rows past their retention horizon.

    Two-pass:

    1. Replayed rows (``replayed_at IS NOT NULL`` AND
       ``replayed_at < now - 30d``) — operator already handled.
    2. Unreplayed rows past the 90d hard limit
       (``dead_lettered_at < now - 90d``).

    Returns counters for observability:
    ``{"deleted_replayed": int, "deleted_aged": int}``.
    """
    now = timezone.now()
    replayed_cutoff = now - timedelta(days=DLQ_REPLAYED_RETENTION_DAYS)
    aged_cutoff = now - timedelta(days=DLQ_RETENTION_DAYS)

    deleted_replayed, _ = IngestDLQ.objects.filter(
        replayed_at__lt=replayed_cutoff,
    ).delete()

    deleted_aged, _ = IngestDLQ.objects.filter(
        replayed_at__isnull=True,
        dead_lettered_at__lt=aged_cutoff,
    ).delete()

    # Round-2 AS10 — stamp the «beat ran» marker so the health check
    # can assert recency. We set EVEN ON ZERO-DELETION runs because
    # the marker is about beat aliveness, not deletion volume.
    cache.set(
        _DLQ_BEAT_LAST_RUN_CACHE_KEY, timezone.now().isoformat(), timeout=_BEAT_HEARTBEAT_TTL_S
    )

    logger.info(
        "eventbus.ingest.cleanup_dlq deleted_replayed=%d deleted_aged=%d",
        deleted_replayed,
        deleted_aged,
    )
    return {"deleted_replayed": deleted_replayed, "deleted_aged": deleted_aged}


@shared_task(name="apps.eventbus.cleanup_ingest_dedupe")
def cleanup_ingest_dedupe() -> dict[str, int]:
    """Sweep IngestDedupe rows past the 120d retention horizon.

    Returns ``{"deleted": int}`` for observability.
    """
    cutoff = timezone.now() - timedelta(days=DEDUPE_RETENTION_DAYS)
    deleted, _ = IngestDedupe.objects.filter(received_at__lt=cutoff).delete()

    # Round-2 AS10 — beat-ran marker (see cleanup_ingest_dlq above).
    cache.set(
        _DEDUPE_BEAT_LAST_RUN_CACHE_KEY,
        timezone.now().isoformat(),
        timeout=_BEAT_HEARTBEAT_TTL_S,
    )

    logger.info(
        "eventbus.ingest.cleanup_dedupe deleted=%d cutoff_days=%d",
        deleted,
        DEDUPE_RETENTION_DAYS,
    )
    return {"deleted": deleted}


def _chunked_delete_older_than(
    model: type[models.Model],
    ts_field: str,
    cutoff: datetime,
    *,
    chunk_size: int = _CLEANUP_CHUNK_SIZE,
) -> int:
    """Delete rows of ``model`` whose ``ts_field`` is < ``cutoff`` in
    batches of ``chunk_size``.

    Selects a page of PKs, deletes exactly those, repeats until a
    short/empty page. Each batch is its own autocommitted statement, so
    no single long transaction accumulates (bounds txn duration, snapshot
    age, and replication lag rather than table-level blocking). Returns
    the total number of model rows deleted.

    The DELETE re-applies the ``ts_field < cutoff`` predicate on top of
    ``pk__in`` so a row whose timestamp was refreshed between the SELECT
    and the DELETE — e.g. ``HandlerFailureTracker.last_attempt_at`` bumped
    by a concurrent retry — is NOT dropped (it no longer qualifies). For
    the ``auto_now_add`` ledgers this is a no-op; for the failure tracker
    it closes the select→delete race.

    Idempotent: a second run over the same window selects nothing.
    """
    filter_kwargs = {f"{ts_field}__lt": cutoff}
    total = 0
    while True:
        pk_page = list(
            model._base_manager.filter(**filter_kwargs).values_list("pk", flat=True)[:chunk_size]
        )
        if not pk_page:
            break
        deleted, _ = model._base_manager.filter(pk__in=pk_page, **filter_kwargs).delete()
        total += deleted
        if len(pk_page) < chunk_size:
            # Last (partial) page — no point issuing one more query that
            # would return empty. (Break on page size, not `deleted`, so
            # a row that escaped via the re-applied predicate still ends
            # the loop; it now sorts after the cutoff and won't reappear.)
            break
    return total


@shared_task(name="apps.eventbus.cleanup_ingest_secondary_ledgers")
def cleanup_ingest_secondary_ledgers() -> dict[str, int]:
    """Sweep the secondary dedupe/failure ledgers past their 120d horizon.

    Covers the ledgers the ``cleanup_ingest_dedupe`` / ``cleanup_ingest_dlq``
    sweeps don't (#1056): ``PaymentTerminalDedupe``,
    ``ReviewProcessedDedupe``, ``NotificationDispatchDedupe`` (§3 consumer
    second-layer guards) and ``HandlerFailureTracker`` (#433 retry
    counter). All share the 120d window (§5.3). Chunked delete bounds the
    per-statement row lock (see :func:`_chunked_delete_older_than`).

    Returns per-model deletion counters keyed by class name, e.g.
    ``{"PaymentTerminalDedupe": int, ...}``. A model whose sweep raised
    is recorded as ``-1`` (sentinel) rather than aborting the whole task —
    one poisoned ledger must not starve the others. The beat heartbeat
    marker is WITHHELD if any model failed, so the health check flags the
    miss instead of reporting a healthy run.
    """
    cutoff = timezone.now() - timedelta(days=SECONDARY_LEDGER_RETENTION_DAYS)
    result: dict[str, int] = {}
    failures: list[str] = []
    for model, ts_field in _SECONDARY_LEDGERS:
        try:
            result[model.__name__] = _chunked_delete_older_than(model, ts_field, cutoff)
        except Exception:  # noqa: BLE001 — isolate one ledger's failure from the rest
            failures.append(model.__name__)
            result[model.__name__] = -1
            logger.exception(
                "eventbus.ingest.cleanup_secondary.model_failed model=%s",
                model.__name__,
            )

    # Beat-ran marker (see cleanup_ingest_dlq / _dedupe above + AS10
    # health check). Set on every fully-successful run, including
    # zero-deletion runs — the marker asserts beat aliveness, not
    # deletion volume. Withheld when any ledger failed so the health
    # check surfaces the partial failure rather than masking it.
    if not failures:
        cache.set(
            _SECONDARY_BEAT_LAST_RUN_CACHE_KEY,
            timezone.now().isoformat(),
            timeout=_BEAT_HEARTBEAT_TTL_S,
        )

    logger.info(
        "eventbus.ingest.cleanup_secondary %r cutoff_days=%d failures=%r",
        result,
        SECONDARY_LEDGER_RETENTION_DAYS,
        failures,
    )
    return result


# ─── AS10 health check ────────────────────────────────────────────────────


def cleanup_beat_health() -> dict[str, Any]:
    """Snapshot of beat liveness for the three cleanup tasks.

    Returns a dict with per-task status: ``{healthy: bool, last_ran_at: str|None, ...}``.

    Health rule per AS10 acceptance: each task MUST have run within
    the last 25h (daily cadence + 1h grace). A stale or missing
    marker means the beat is dead OR mis-configured; either way the
    retention guarantee is just a comment in code. Covers the DLQ,
    dedupe, and (#1056) secondary-ledger sweeps.

    The result shape is suitable for direct JSON serialisation in a
    /health endpoint or a Celery-scheduled status emit.
    """
    import datetime as _dt

    now = timezone.now()
    cutoff = now - _dt.timedelta(hours=25)

    def _check_marker(key: str) -> dict[str, Any]:
        raw = cache.get(key)
        if not raw:
            return {"healthy": False, "last_ran_at": None, "reason": "no_marker"}
        try:
            last = _dt.datetime.fromisoformat(raw)
        except ValueError:
            return {"healthy": False, "last_ran_at": raw, "reason": "bad_marker_format"}
        if last < cutoff:
            return {
                "healthy": False,
                "last_ran_at": raw,
                "reason": "stale",
                "max_age_h": 25,
            }
        return {"healthy": True, "last_ran_at": raw}

    return {
        "cleanup_ingest_dlq": _check_marker(_DLQ_BEAT_LAST_RUN_CACHE_KEY),
        "cleanup_ingest_dedupe": _check_marker(_DEDUPE_BEAT_LAST_RUN_CACHE_KEY),
        "cleanup_ingest_secondary_ledgers": _check_marker(_SECONDARY_BEAT_LAST_RUN_CACHE_KEY),
    }
