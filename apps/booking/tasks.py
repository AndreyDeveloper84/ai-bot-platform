"""Periodic Celery task: mirror ↔ canon reconciliation sweep.

DRF-1111 + DRF-1161. Runs hourly via Celery beat (see
``CELERY_BEAT_SCHEDULE`` in ``config/settings/base.py``). All logic lives
in :mod:`apps.booking.mirror_reconcile` — this module is only the Celery
handle, matching the thin-task convention of the other beat entries.

Read-only against both sides: the sweep detects and alerts, it never
repairs. The result returned to the result backend drops the per-tenant
``reports`` mapping (dataclasses are not JSON-serialisable); the counts
and slug lists stay.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task  # type: ignore[import-untyped]

from apps.booking.mirror_reconcile import run_mirror_reconciliation


@shared_task(
    name="apps.booking.tasks.reconcile_ayla_mirror",
    # 45 day-requests per swept tenant at ~100 ms each lands well under a
    # minute; 10 min of slack covers a slow Ayla without overlapping the
    # next hourly tick.
    soft_time_limit=600,
)
def reconcile_ayla_mirror() -> dict[str, Any]:
    summary = run_mirror_reconciliation()
    summary.pop("reports", None)
    return summary
