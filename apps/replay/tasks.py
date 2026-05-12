"""Replay retention task (DRF-500 / Sprint 5 / A4).

Daily Celery beat job that hard-deletes :class:`ReplayTrace` rows past
their ``expires_at`` deadline. Mirrors the Sprint 1 audit-log retention
pattern (`apps.audit.tasks.cleanup_old_audit_logs`) but evicts on a
per-row timestamp rather than a global retention window — recorder
stamps `expires_at` at capture time as `now + REPLAY_RETENTION_DAYS`.

### Why per-row expires_at, not a global cutoff

Future Phase-1 work may want per-tenant retention overrides (regulated
tenants → 90d; others → 30d). Stamping on the row keeps the cleanup
query trivial (`WHERE expires_at < now()`) regardless of policy
permutations.

### Scheduling

Wired in `config.settings.base.CELERY_BEAT_SCHEDULE` daily 04:00 UTC.
The hour offset from audit cleanup (03:00) + idempotency cleanup
(:15 hourly) spreads load on the shared Celery worker pool.

### Audit row

Writes `replay.cleanup_completed` with the deleted count so a future
operator can grep audit history for retention-eviction trends.
"""

from __future__ import annotations

import logging

from celery import shared_task  # type: ignore[import-untyped]
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="apps.replay.tasks.cleanup_expired_traces")
def cleanup_expired_traces() -> int:
    """Hard-delete ReplayTrace rows whose `expires_at` is in the past.

    Returns:
      Number of rows deleted. 0 if nothing to delete.

    Side effects:
      Writes a forensic audit row with the count. Errors are not
      swallowed — Celery surfaces them to Sentry; the next scheduled
      run retries.
    """

    from apps.audit.services import write_audit
    from apps.replay.models import ReplayTrace

    now = timezone.now()
    # `all_tenants` because this is a system-level job — runs outside
    # any tenant scope. The filter on `expires_at` keeps it cheap via
    # the (expires_at,) index on the model.
    deleted_count, _ = ReplayTrace.all_tenants.filter(expires_at__lt=now).delete()
    logger.info(
        "replay.cleanup_completed count=%d cutoff=%s",
        deleted_count,
        now.isoformat(),
    )
    # System-level audit row — no tenant scope.
    write_audit(
        "replay.cleanup_completed",
        target="ReplayTrace",
        payload={"deleted_count": deleted_count, "cutoff": now.isoformat()},
    )
    return deleted_count
