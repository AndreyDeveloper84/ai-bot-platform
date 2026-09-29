"""Periodic Celery task: chase handoff tasks nobody picked up.

DRF-1488. Runs every 5 minutes via Celery beat (see
``CELERY_BEAT_SCHEDULE`` in ``config/settings/base.py``). All logic lives
in :mod:`apps.handoff.escalation` — this module is only the Celery handle,
matching the thin-task convention of the other beat entries.

The sweep is idempotent by construction (``pickup_escalated_at`` is
stamped with a conditional UPDATE), so a retried message or an
overlapping tick cannot double-notify. It never touches the client's
dialog: what to do about the client while they wait is an owner decision.
"""

from __future__ import annotations

from celery import shared_task  # type: ignore[import-untyped]

from apps.handoff.escalation import sweep_unclaimed_tasks


@shared_task(
    name="handoff.sweep_unclaimed_tasks",
    # The pilot produces a handful of tasks a month, so the query is
    # trivially small; 60 s of slack still keeps a pathological run from
    # overlapping the next 5-minute tick.
    soft_time_limit=60,
)
def sweep_unclaimed_handoff_tasks() -> dict[str, int]:
    return {"escalated": sweep_unclaimed_tasks()}
