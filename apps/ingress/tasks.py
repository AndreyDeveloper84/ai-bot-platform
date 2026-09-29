"""Celery tasks for the ingress app (DRF-2242)."""

from __future__ import annotations

import logging

from celery import shared_task  # type: ignore[import-untyped]

from apps.ingress.retention import sweep_expired

logger = logging.getLogger(__name__)


@shared_task(name="apps.ingress.tasks.sweep_webhook_journal")
def sweep_webhook_journal() -> dict[str, int | str]:
    """Hourly WebhookJournal retention, rolling edge only (DRF-2242).

    Bodies past ``INGRESS_RAW_RETENTION_HOURS`` → ``{}``; rows past
    ``WEBHOOK_JOURNAL_ROW_RETENTION_DAYS`` → deleted. Anything older than the
    edge is the ``purge_webhook_journal`` command's, on the owner's word.

    The result carries the COVERAGE of those counts, not the counts alone
    (DRF-2426): what lay in the basket before the sweep acted, what is left
    beyond its window, how many rows the journal holds at all, and the one-word
    ``verdict``. A bare ``{"payloads": 0, "rows": 0}`` was true about the edge
    and read as a clean journal while 581 over-term bodies sat outside the
    window; a zero now arrives with the scope that makes it mean something.

    Returns:
      counts and their coverage — never payload values.
    """

    result = sweep_expired()
    return {
        "payloads": result.payloads,
        "rows": result.rows,
        "in_window_payloads": result.in_window_payloads,
        "in_window_rows": result.in_window_rows,
        "beyond_window_payloads": result.beyond_window_payloads,
        "beyond_window_rows": result.beyond_window_rows,
        "scanned": result.scanned,
        "verdict": result.verdict,
    }
