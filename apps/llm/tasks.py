"""Beat-scheduled LLM availability probe / connection warm-up.

DRF-1054 + DRF-1056. All logic lives in :mod:`apps.llm.health`; this is
the Celery shell so ``CELERY_BEAT_SCHEDULE`` has something to name.

``apps.llm`` is deliberately not a Django app (no models — see the
package docstring), so ``autodiscover_tasks()`` cannot find this module.
It is registered explicitly via ``CELERY_IMPORTS`` in
``config/settings/base.py``, the same escape hatch
``apps.integrations.yclients.tasks`` already uses.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]

from apps.llm.health import check_llm_availability

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.llm.tasks.probe_llm_availability",
    # The probe's own outer ceiling is LLM_HEALTH_PROBE_TIMEOUT_S (60 s
    # by default). These limits sit above it so the task is killed only
    # if that ceiling itself somehow fails to bite — a beat task must
    # never wedge a worker slot indefinitely.
    soft_time_limit=120,
    time_limit=150,
    # No Celery-level retry: the next beat tick IS the retry, and a
    # retrying task would desynchronise the consecutive-failure counter
    # that the alert threshold is built on.
    max_retries=0,
)
def probe_llm_availability() -> dict[str, Any]:
    """One tick: probe the LLM path, warm the connection, alert on change.

    Returns :func:`apps.llm.health.check_llm_availability`'s summary
    dict. Never raises — a monitor that can crash the scheduler is worse
    than no monitor.
    """

    try:
        return check_llm_availability()
    except Exception:  # noqa: BLE001 — hard containment
        logger.exception("llm.tasks.probe_llm_availability.unexpected")
        return {"skipped": "error"}
