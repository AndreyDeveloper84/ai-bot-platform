"""Conversations app Celery tasks — retention sweeps.

Currently hosts one task:

* :func:`purge_old_ai_drafts` — Blocker #5 Layer 2 of the PR #535 (M6
  AI drafts) follow-up. Hard-deletes terminal :class:`AiDraft` rows
  older than :data:`AI_DRAFT_RETENTION_DAYS`. Layer 1 (content
  clearing at status-flip time) lives in
  :mod:`apps.master_api.services.ai_drafts`; Layer 2 sweeps the
  metadata-only stubs after the finance reconciliation window closes.

### Why two layers?

* **Layer 1 (immediate)** clears the customer-PII-quoting ``content``
  column the moment a draft transitions to a terminal status. This
  closes the at-rest PII exposure window per ADR-0011 §3.4 (red-zone
  data needs TTL or at-rest redaction).
* **Layer 2 (eventual)** deletes the whole row including the
  tokens / cost / model metadata after :data:`AI_DRAFT_RETENTION_DAYS`
  (default 30). Finance reconciliation windows are typically 30 days;
  past that there's no operational need to retain even the metadata.

Together they bound PII exposure to «status flip → end of HTTP
request» and bound metadata retention to a known window.

### Why not ACTIVE rows?

The ACTIVE status is, by construction, the only writable state. An
ACTIVE row with ``updated_at`` 60 days in the past would mean the
master generated a draft 60 days ago and never sent / released /
regenerated — pathological but possible (master stopped using the
mini app for 2 months). We deliberately don't delete ACTIVE rows in
this sweep — they still have a live UI surface. A separate decay
policy would be needed if we wanted to retire stale-ACTIVE rows; that's
out of scope for the immediate follow-up.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


# Configurable via settings.AI_DRAFT_RETENTION_DAYS for staging tuning.
# Default 30 days — matches the longest typical finance reconciliation
# window for the salons we onboard for the 2026-07-15 Penza pilot.
AI_DRAFT_RETENTION_DAYS = 30


def _retention_days() -> int:
    return int(getattr(settings, "AI_DRAFT_RETENTION_DAYS", AI_DRAFT_RETENTION_DAYS))


@shared_task(name="apps.conversations.tasks.purge_old_ai_drafts")
def purge_old_ai_drafts() -> int:
    """Hard-delete terminal AiDraft rows older than the retention cutoff.

    Filter:
      * ``status != ACTIVE`` — preserves the currently-shown draft for
        every conversation regardless of age (ACTIVE means «live UI»).
      * ``updated_at < cutoff`` — terminal drafts whose finance window
        has closed.

    Returns the number of rows deleted (0 if nothing matched).

    Idempotent: re-runs over the same data delete only newly-aged rows.

    Reads:
      ``settings.AI_DRAFT_RETENTION_DAYS`` (default 30).
    """

    # Local import — the model lives in this same app but importing at
    # module load time forces Django app loading before the Celery
    # worker is ready in some test paths. Local import sidesteps that.
    from apps.conversations.models import AiDraft

    days = _retention_days()
    if days <= 0:
        logger.info("ai_drafts.purge.disabled days=%d", days)
        return 0

    cutoff = timezone.now() - timedelta(days=days)
    qs = AiDraft.all_tenants.filter(
        updated_at__lt=cutoff,
    ).exclude(status=AiDraft.Status.ACTIVE)

    # Cap the per-run batch so a deferred backlog doesn't blow worker
    # memory. 5000 is conservative — typical daily volume is well below
    # this even for a busy salon.
    batch_pks = list(qs.values_list("pk", flat=True)[:5000])
    if not batch_pks:
        logger.info("ai_drafts.purge.nothing_to_delete cutoff=%s", cutoff.isoformat())
        return 0

    deleted_count, _per_model = AiDraft.all_tenants.filter(pk__in=batch_pks).delete()
    logger.info(
        "ai_drafts.purge.deleted count=%d cutoff=%s retention_days=%d",
        deleted_count,
        cutoff.isoformat(),
        days,
    )
    return int(deleted_count)


__all__ = ["AI_DRAFT_RETENTION_DAYS", "purge_old_ai_drafts"]
