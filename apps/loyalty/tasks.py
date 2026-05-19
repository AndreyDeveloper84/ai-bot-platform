"""Celery tasks for Loyalty (Volna 4).

Phase 2.c — daily inactivity-downgrade beat.

### Tunables

``INACTIVITY_HARD_DOWNGRADE_DAYS`` + ``INACTIVITY_DOWNGRADE_BATCH_LIMIT``
live on ``apps.loyalty.services`` so service-layer tests can override
without poking at the task wrapper.

### Why a thin task wrapper

The service-layer function does all the work. The Celery decorator is
the only platform-specific glue. Keeping the wrapper thin means:
- Unit tests exercise the service directly (no Celery machinery).
- The beat schedule entry references a single canonical task name.
"""

from __future__ import annotations

import logging

from celery import shared_task  # type: ignore[import-untyped]

from apps.loyalty import services as loyalty_services

logger = logging.getLogger(__name__)


@shared_task(name="loyalty.apply_inactivity_downgrades")
def apply_inactivity_downgrades() -> dict[str, int]:
    """Daily beat task — hard-downgrade accounts ≥ 365 days inactive.

    Returns the counters dict from
    :func:`apps.loyalty.services.apply_inactivity_downgrades` for Celery
    result-backend visibility.
    """

    counters = loyalty_services.apply_inactivity_downgrades()
    return counters
