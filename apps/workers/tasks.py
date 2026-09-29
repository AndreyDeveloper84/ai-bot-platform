"""Celery tasks for ``apps.workers`` (issue #499 — PEL reaper).

Currently a single periodic task:

* ``apps.workers.tasks.reap_pel`` — wraps
  :func:`apps.workers.reaper.reap_pel_streams` in a Celery shared_task
  so the beat schedule entry in ``config/settings/base.py`` can call it.
  Returns the number of reaped entries (for Celery result-backend
  visibility when one is configured).
"""

from __future__ import annotations

import logging

from celery import shared_task  # type: ignore[import-untyped]

from apps.ingress.streams import trim_expired
from apps.workers.reaper import reap_pel_streams

logger = logging.getLogger(__name__)


@shared_task(name="apps.workers.tasks.reap_pel")
def reap_pel() -> int:
    """Periodic PEL reaper — see :mod:`apps.workers.reaper` for semantics.

    Returns:
      Total entries reaped across all registered ingress streams this tick.
    """

    reaped = reap_pel_streams()
    if reaped:
        logger.info("workers.tasks.reap_pel reaped=%d", reaped)
    return reaped


@shared_task(name="apps.workers.tasks.trim_ingress_streams")
def trim_ingress_streams() -> int:
    """Hourly retention trim of raw webhook bodies (DRF-2220).

    Returns:
      Total entries removed across every ingress stream and its DLQ.
    """

    trimmed = trim_expired()
    total = sum(trimmed.values())
    if total:
        logger.info("workers.tasks.trim_ingress_streams removed=%d", total)
    return total
