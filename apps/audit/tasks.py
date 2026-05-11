"""Audit retention task (DRF-426 / B1, review revision 6A-split).

Deletes audit rows older than ``AUDIT_LOG_RETENTION_DAYS`` (default 90).
Separate from idempotency-key retention (B2 / DRF-427, default 7d) per
the explicit user decision in the eng review: AuditLog and
IdempotencyKey have different lifecycles and must never share a
schedule.

Sprint 1 ships the task. Wiring into Celery beat is a Sprint 2 follow-up
captured in TODOS.md.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="apps.audit.tasks.cleanup_old_audit_logs")
def cleanup_old_audit_logs() -> int:
    """Delete AuditLog rows older than the retention cutoff.

    Returns:
      Number of rows deleted. 0 if nothing to delete.

    Reads:
      settings.AUDIT_LOG_RETENTION_DAYS (default 90).

    Side effects:
      Logs the count + cutoff for observability. Errors are not
      swallowed here (this is a Celery task — Celery surfaces the
      exception to Sentry and the next scheduled run retries).
    """

    from apps.audit.models import AuditLog

    days = int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90))
    cutoff = timezone.now() - timedelta(days=days)

    deleted, _per_model = AuditLog.all_tenants.filter(created_at__lt=cutoff).delete()
    logger.info(
        "audit.retention.cleanup deleted=%d cutoff=%s days=%d",
        deleted,
        cutoff.isoformat(),
        days,
    )
    return deleted
