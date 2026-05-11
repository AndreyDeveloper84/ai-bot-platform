"""Sprint 2 / E3 — Celery beat schedule sanity tests (DRF-447).

We don't run the beat scheduler in unit tests; instead we verify the
schedule registration is sane:
- Both retention tasks have entries.
- Each entry's `task` name resolves to a callable in the app's
  registered Celery tasks.
- Each entry has a `crontab` schedule with the expected cadence.

This catches typos in task paths and accidental schedule drift
without needing to spin up a Celery worker.
"""

from __future__ import annotations

from celery.schedules import crontab  # type: ignore[import-untyped]
from django.conf import settings


def test_audit_log_cleanup_scheduled_daily_3am_utc():
    entry = settings.CELERY_BEAT_SCHEDULE["cleanup_old_audit_logs"]
    assert entry["task"] == "apps.audit.tasks.cleanup_old_audit_logs"
    sched = entry["schedule"]
    assert isinstance(sched, crontab)
    # crontab stores normalised sets of valid values.
    assert 3 in sched.hour
    assert 0 in sched.minute


def test_idempotency_cleanup_scheduled_hourly_15min():
    entry = settings.CELERY_BEAT_SCHEDULE["cleanup_old_idempotency_keys"]
    assert entry["task"] == "apps.tools.tasks.cleanup_old_idempotency_keys"
    sched = entry["schedule"]
    assert isinstance(sched, crontab)
    assert 15 in sched.minute


def test_retention_tasks_are_registered_celery_tasks():
    """Smoke: the @shared_task wrappers exist and are importable as
    Celery tasks. A typo in the task path string above would surface
    here as an import error.
    """

    from apps.audit.tasks import cleanup_old_audit_logs
    from apps.tools.tasks import cleanup_old_idempotency_keys

    # Both should be Celery task instances (have a `.name` attribute
    # set by @shared_task).
    assert cleanup_old_audit_logs.name == "apps.audit.tasks.cleanup_old_audit_logs"
    assert cleanup_old_idempotency_keys.name == "apps.tools.tasks.cleanup_old_idempotency_keys"
