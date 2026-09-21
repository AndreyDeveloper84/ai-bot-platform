"""Celery tasks for the ingress app (DRF-2242)."""

from __future__ import annotations

import logging

from celery import shared_task  # type: ignore[import-untyped]

from apps.ingress.retention import sweep_expired

logger = logging.getLogger(__name__)


@shared_task(name="apps.ingress.tasks.sweep_webhook_journal")
def sweep_webhook_journal() -> dict[str, int]:
    """Hourly WebhookJournal retention, rolling edge only (DRF-2242).

    Bodies past ``INGRESS_RAW_RETENTION_HOURS`` → ``{}``; rows past
    ``WEBHOOK_JOURNAL_ROW_RETENTION_DAYS`` → deleted. Anything older than the
    edge is the ``purge_webhook_journal`` command's, on the owner's word.

    Returns:
      ``{"payloads": n, "rows": m}`` — counts, never values.
    """

    result = sweep_expired()
    return {"payloads": result.payloads, "rows": result.rows}
