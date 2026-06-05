"""Celery beat cleanup tasks for the cross-service ingest tables.

PR #507 adversarial pass A8. The :class:`IngestDLQ` table stores
full envelope ``data`` payloads indefinitely without cleanup; per
ADR-0011 §3.4 + 152-ФЗ a publisher redaction bug or v2 event with
new (un-redacted) fields = weeks of accumulating PII in the DLQ.

This module ships two retention sweeps as ``@shared_task`` functions
+ their beat schedule entries (added to
``config/settings/base.py::CELERY_BEAT_SCHEDULE``).

| Task | Source | Retention | Cadence |
|---|---|---|---|
| ``cleanup_ingest_dlq`` | ``IngestDLQ`` | 90 days (§6.4); replayed rows cleaned at 30d | Daily 04:30 UTC |
| ``cleanup_ingest_dedupe`` | ``IngestDedupe`` | 120 days (§5.3) | Daily 04:45 UTC |

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
from datetime import timedelta
from typing import Any, Final

from celery import shared_task  # type: ignore[import-untyped]
from django.core.cache import cache
from django.utils import timezone

from apps.eventbus.models import IngestDedupe, IngestDLQ


# Round-2 AS10 — beat-ran-recently marker cache keys.
# Set at task end; checked by ``health.check_cleanup_beat_healthy``.
# Cache is the right store: ephemeral, no migration, and the
# 25h staleness window aligns with the daily cadence regardless.
_DLQ_BEAT_LAST_RUN_CACHE_KEY = "eventbus:ingest:cleanup_dlq_last_ran_at"
_DEDUPE_BEAT_LAST_RUN_CACHE_KEY = "eventbus:ingest:cleanup_dedupe_last_ran_at"
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


# ─── AS10 health check ────────────────────────────────────────────────────


def cleanup_beat_health() -> dict[str, Any]:
    """Snapshot of beat liveness for the two cleanup tasks.

    Returns a dict with per-task status: ``{healthy: bool, last_ran_at: str|None, ...}``.

    Health rule per AS10 acceptance: each task MUST have run within
    the last 25h (daily cadence + 1h grace). A stale or missing
    marker means the beat is dead OR mis-configured; either way the
    DLQ retention guarantee is a comment in code.

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
    }
