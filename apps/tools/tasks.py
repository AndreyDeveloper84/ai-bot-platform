"""Idempotency retention task (DRF-427 / B2, review revision 6A-split).

Deletes IdempotencyKey rows past their ``expires_at`` AND older than
``IDEMPOTENCY_KEY_RETENTION_DAYS`` (default 7d). Two layered conditions:
- ``expires_at`` is the soft TTL (typically hours).
- The retention window adds a hard cutoff so rows can't accumulate
  indefinitely if a caller passes huge TTLs.

Separate from ``cleanup_old_audit_logs`` (B1, 90d). Different
lifecycles per the explicit 6A-split decision in plan-eng-review.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def cleanup_old_idempotency_keys() -> int:
    """Delete idempotency keys past their TTL or older than retention.

    Returns:
      Number of rows deleted.

    Reads:
      settings.IDEMPOTENCY_KEY_RETENTION_DAYS (default 7).
    """

    from apps.tools.models import IdempotencyKey

    days = int(getattr(settings, "IDEMPOTENCY_KEY_RETENTION_DAYS", 7))
    now = timezone.now()
    retention_cutoff = now - timedelta(days=days)

    # Delete rows that are EITHER past expiry OR past the hard retention cutoff.
    queryset = IdempotencyKey.objects.filter(
        models_q_expires_or_age(now, retention_cutoff),
    )
    deleted, _per_model = queryset.delete()
    logger.info(
        "tools.idempotency.cleanup deleted=%d retention_days=%d",
        deleted,
        days,
    )
    return deleted


def models_q_expires_or_age(now, retention_cutoff):
    """Build the Q object for `expires_at < now OR created_at < retention_cutoff`."""
    from django.db.models import Q

    return Q(expires_at__lt=now) | Q(created_at__lt=retention_cutoff)
