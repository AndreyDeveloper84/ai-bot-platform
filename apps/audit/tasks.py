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
from django.db import transaction
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

    # Audit retro Y4: chunked sweep instead of single-statement
    # UPDATE / DELETE. Pre-fix one UPDATE / DELETE over potentially
    # millions of rows held long row locks, lagged replication, and
    # risked statement-timeout. Chunked iteration with short
    # transactions per chunk lets the sweep coexist with hot
    # production traffic.
    affected = (
        _soft_archive_chunked(AuditLog, cutoff)
        if mode == "soft"
        else _hard_delete_chunked(AuditLog, cutoff)
    )

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


# Audit retro Y4: chunk size matches the loyalty audit-sweep pattern
# (~5k rows / iteration). Tuned for production tenants — bumps
# replication caught-up between iterations + keeps row locks
# short-lived enough that hot writes don't starve.
_SWEEP_CHUNK_SIZE = 5_000


def _soft_archive_chunked(model_cls, cutoff) -> int:  # noqa: ANN001
    """Chunked UPDATE: flip ``is_archived=True`` on archive-eligible rows.

    Returns the total number of rows affected across all chunks. Each
    chunk runs inside an explicit ``transaction.atomic()`` so the
    «short transaction per chunk» contract is load-bearing — a future
    maintainer adding a second statement inside the loop can't
    accidentally span chunks (closes reviewer Y3).
    """

    total = 0
    while True:
        pks = list(
            model_cls.all_tenants.filter(
                created_at__lt=cutoff,
                is_archived=False,
            )
            .order_by("pk")
            .values_list("pk", flat=True)[:_SWEEP_CHUNK_SIZE]
        )
        if not pks:
            break
        with transaction.atomic():
            affected = model_cls.all_tenants.filter(pk__in=pks).update(
                is_archived=True,
                archived_at=timezone.now(),
            )
        total += affected
        if len(pks) < _SWEEP_CHUNK_SIZE:
            break
    return total


def _hard_delete_chunked(model_cls, cutoff) -> int:  # noqa: ANN001
    """Chunked DELETE: drop rows past the retention cutoff.

    See :func:`_soft_archive_chunked` for the per-chunk atomic
    rationale.
    """

    total = 0
    while True:
        pks = list(
            model_cls.all_tenants.filter(created_at__lt=cutoff)
            .order_by("pk")
            .values_list("pk", flat=True)[:_SWEEP_CHUNK_SIZE]
        )
        if not pks:
            break
        with transaction.atomic():
            deleted, _per_model = model_cls.all_tenants.filter(pk__in=pks).delete()
        total += deleted
        if len(pks) < _SWEEP_CHUNK_SIZE:
            break
    return total
