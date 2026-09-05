"""The catalog sync is actually on the schedule (DRF-1494).

`catalog_sync_every_15min` has been in `CELERY_BEAT_SCHEDULE` since
2026-05-13 and nothing pinned it. Retention, booking reconciliation,
profile recompute and the shadow delta all have beat-entry tests; the one
job whose silence costs a salon its bookable catalog had none, so it could
have been renamed or dropped by an unrelated edit and every test in the
repository would still have been green.

These tests are cheap and they answer the ticket's own question — "была ли
синхронизация вообще периодической" — in the place the answer belongs:
the schedule, asserted, rather than a claim in a docstring.
"""

from __future__ import annotations

from django.conf import settings


class TestSyncEntry:
    def test_sync_is_scheduled(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["catalog_sync_every_15min"]
        assert entry["task"] == "apps.catalog.tasks.sync_catalog_for_all_tenants"

    def test_cadence_is_every_15_minutes(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["catalog_sync_every_15min"]
        assert entry["schedule"].minute == {0, 15, 30, 45}

    def test_cadence_stays_under_the_advisory_lock_ttl(self) -> None:
        # The lock TTL must remain >= 1.5x the cadence or two beats can race
        # on one tenant. Pinning the relationship, not the two numbers apart,
        # keeps a future cadence change from silently breaking the invariant
        # the sync module documents.
        assert settings.CATALOG_SYNC_LOCK_TTL_SECONDS >= int(15 * 60 * 1.5)


class TestStalenessEntry:
    def test_watchdog_is_scheduled(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["catalog_sync_staleness_hourly"]
        assert entry["task"] == "apps.catalog.tasks.alert_stale_catalog_sync"

    def test_runs_hourly_offset_past_the_sync_ticks(self) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE["catalog_sync_staleness_hourly"]
        # :07 — after the :00 sync has had its chance to advance the clock
        # this task reads. Sharing a minute with the sync would race the
        # watchdog against the thing it watches.
        assert entry["schedule"].minute == {7}
        assert entry["schedule"].hour == set(range(24))

    def test_threshold_is_outside_what_a_healthy_contour_can_produce(self) -> None:
        # Cadence 15m + lock TTL 25m bounds a legal skip pair at ~30m, and
        # the run behind it is capped at 12m soft. The threshold has to sit
        # above that envelope or the alarm pages on healthy behaviour and
        # gets muted, which is the state this ticket exists to prevent.
        assert settings.CATALOG_SYNC_STALE_AFTER_SECONDS > settings.CATALOG_SYNC_LOCK_TTL_SECONDS
