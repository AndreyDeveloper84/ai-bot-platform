"""Audit retention task (DRF-426 / B1, review revision 6A-split;
extended for soft-delete + audit-row in DRF-851 / Phase 1 / PI1).

Deletes audit rows older than ``AUDIT_LOG_RETENTION_DAYS`` (default 90).
Separate from idempotency-key retention (B2 / DRF-427, default 7d) per
the explicit user decision in the eng review: AuditLog and
IdempotencyKey have different lifecycles and must never share a
schedule.

### Mode (DRF-851)

``AUDIT_LOG_RETENTION_MODE`` (default ``"hard"``) controls the sweep:

* ``"hard"`` — issue DELETE. Original behaviour; backwards compatible.
* ``"soft"`` — UPDATE rows to ``is_archived=True`` + ``archived_at=now``.
  Recommended for prod going forward; lets a second (future) task
  hard-delete archived rows past a longer cutoff. NOT built here.

The cleanup run itself writes an audit row (``audit.retention.cleanup``)
including the mode, deleted count, cutoff, and retention days. That
row is itself subject to retention — intentional: you get N days of
cleanup-run history, but the table never grows boundlessly.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


AUDIT_CLEANUP_ACTION = "audit.retention.cleanup"


@shared_task(name="apps.audit.tasks.cleanup_old_audit_logs")
def cleanup_old_audit_logs() -> int:
    """Delete (or soft-archive) AuditLog rows older than the retention cutoff.

    Returns:
      Number of rows affected (deleted in ``"hard"`` mode, archived in
      ``"soft"`` mode). 0 if nothing matched.

    Reads:
      settings.AUDIT_LOG_RETENTION_DAYS (default 90).
      settings.AUDIT_LOG_RETENTION_MODE  (default "hard"; "soft" enables
        flip-instead-of-delete).

    Side effects:
      Writes an ``audit.retention.cleanup`` row via
      :func:`apps.audit.services.write_audit` capturing mode + deleted
      count + cutoff + retention days. Cross-tenant: written with
      ``tenant=None`` (cleanup is a system action).
    """

    from apps.audit.models import AuditLog
    from apps.audit.services import write_audit

    days = int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 90))
    mode = str(getattr(settings, "AUDIT_LOG_RETENTION_MODE", "hard")).lower()
    if mode not in ("hard", "soft"):
        logger.warning(
            "audit.retention.invalid_mode mode=%r — falling back to 'hard'",
            mode,
        )
        mode = "hard"

    cutoff = timezone.now() - timedelta(days=days)

    if mode == "soft":
        # Only flip rows that aren't already archived — re-running the
        # task must be idempotent.
        affected = AuditLog.all_tenants.filter(
            created_at__lt=cutoff,
            is_archived=False,
        ).update(is_archived=True, archived_at=timezone.now())
    else:
        # hard: original behaviour, full DELETE.
        deleted, _per_model = AuditLog.all_tenants.filter(created_at__lt=cutoff).delete()
        affected = deleted

    logger.info(
        "audit.retention.cleanup affected=%d cutoff=%s days=%d mode=%s",
        affected,
        cutoff.isoformat(),
        days,
        mode,
    )

    # System-wide cleanup — no tenant context. write_audit() reads
    # current_tenant() (None here) so the row lands with tenant=None,
    # mirroring the cross-tenant catalog webhook pattern.
    write_audit(
        action=AUDIT_CLEANUP_ACTION,
        target="audit.AuditLog",
        payload={
            "deleted": affected,
            "cutoff": cutoff.isoformat(),
            "retention_days": days,
            "mode": mode,
        },
    )
    return affected
