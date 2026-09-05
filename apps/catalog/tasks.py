"""Catalog Celery tasks (DRF-579 / Sprint 7 / C5).

One periodic beat target — :func:`sync_catalog_for_all_tenants` —
that fans :class:`apps.catalog.services.sync.CatalogSyncService`
out across every active tenant.

### Why fan-out at the task layer (not inside the service)

The service is single-tenant by design (one Redis lock, one cursor,
one audit row per run). Sprint 8 multi-tenant ramp wants each tenant
to fail independently — one slow / broken tenant must not stall the
others. Doing the fan-out here gives us per-tenant try/except and
per-tenant scheduling granularity for Sprint 9+ when we may want to
stagger tenants by SLA tier.

### Beat schedule

Two entries in ``config.settings.base::CELERY_BEAT_SCHEDULE``:

* ``catalog_sync_every_15min`` → :func:`sync_catalog_for_all_tenants`.
  15-minute cadence, deliberately matched to the lock TTL ÷ 1.5 in
  :mod:`apps.catalog.services.sync` so a slow run can't race itself.
  (The key name here read ``catalog-sync-every-15min`` until DRF-1494;
  the real key uses underscores. A reader checking whether the sync was
  scheduled at all would have grepped the hyphenated name and found
  nothing.)
* ``catalog_sync_staleness_hourly`` → :func:`alert_stale_catalog_sync`.
  Hourly, offset to :07 so it reads a clock the :00/:15/:30/:45 sync has
  just had a chance to advance.

``apps/catalog/tests/test_beat_schedule.py`` pins both. Before DRF-1494
neither was covered: the sync could have been dropped from the schedule
by an unrelated edit and every test in the repository would still be
green.

### Soft time limit

Task carries a ``soft_time_limit=720`` (12 min) — under the 15-min
beat cadence so an overrun fires :class:`SoftTimeLimitExceeded`
in time for the next tick to start fresh. The service's Redis lock
TTL still guards against the worker silently hanging beyond that
window.
"""

from __future__ import annotations

import logging

from celery import shared_task  # type: ignore[import-untyped]

from apps.catalog.services.sync import CatalogSyncService, SyncResult
from apps.catalog.staleness import sync_ages
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.observability.alerting import page
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.catalog.tasks.sync_catalog_for_all_tenants",
    soft_time_limit=720,
    time_limit=780,
)
def sync_catalog_for_all_tenants() -> dict[str, int]:
    """Beat target — sync every active tenant's catalog mirror.

    Returns:
      Counter dict ``{tenants_run, tenants_skipped, tenants_failed,
      total_created, total_updated, total_skipped, total_removed}``
      aggregated across the fan-out. The return value goes to the Celery
      result backend and nowhere else.

    Nothing watches this task's outcome from in here. Until DRF-1494 this
    docstring said the counters were "surfaced to ``check_agents`` health
    for 'last sync OK across N/M tenants'"; ``check_agents`` does not exist
    in this repository and never did. A sentence describing unbuilt
    monitoring is worse than no sentence, because it answers "are we
    watching this?" with a yes — and for twelve pilot days the answer was
    no. The watching is now done by :func:`alert_stale_catalog_sync` below,
    which reads the clock this fan-out advances instead of trusting a
    counter nobody collects.
    """
    counters = {
        "tenants_run": 0,
        "tenants_skipped": 0,
        "tenants_failed": 0,
        "total_created": 0,
        "total_updated": 0,
        "total_skipped": 0,
        "total_removed": 0,
    }

    service = CatalogSyncService()
    for tenant in Tenant.objects.all().iterator():
        # Skip the global_bot sentinel (#1019): it owns tenant-less global
        # BotUsers + discovery, NOT a salon catalog — the Ayla fetch with
        # its UUID returns 400 every cycle (perpetual tenants_failed=1 +
        # monitoring noise). Marker: GLOBAL_BOT_TENANT_SLUG from
        # apps.identity.constants — the same single source of truth the
        # identity resolver uses, so the sentinel can't drift past the
        # exclusion. The row itself is NOT touched (it must stay alive).
        if tenant.slug == GLOBAL_BOT_TENANT_SLUG:
            continue
        try:
            result = service.run(tenant)
        except Exception:
            counters["tenants_failed"] += 1
            logger.exception(
                "catalog.sync.beat_tenant_failed tenant_id=%s",
                tenant.id,
            )
            continue
        _accumulate(counters, result)

    logger.info(
        "catalog.sync.beat_completed run=%s skipped=%s failed=%s "
        "created=%s updated=%s skipped_rows=%s removed=%s",
        counters["tenants_run"],
        counters["tenants_skipped"],
        counters["tenants_failed"],
        counters["total_created"],
        counters["total_updated"],
        counters["total_skipped"],
        # Removal is the one destructive action a beat can take — it must not
        # be the only counter missing from the line an operator reads.
        counters["total_removed"],
    )
    return counters


def _accumulate(counters: dict[str, int], result: SyncResult) -> None:
    """Roll a per-tenant :class:`SyncResult` into the fan-out totals."""
    if result.skipped:
        counters["tenants_skipped"] += 1
        return
    if not result.ran or result.error:
        counters["tenants_failed"] += 1
        return
    counters["tenants_run"] += 1
    # All three mirrors count toward the beat totals (DRF-945). The masters
    # mirror landed on dev without being wired in here; folding it and the new
    # bookable-edge mirror in together keeps "last sync OK across N/M tenants"
    # honest — otherwise a mirror could fail every cycle and the beat counters
    # would still look clean.
    for mirror in (result.services, result.masters, result.master_services):
        counters["total_created"] += mirror.created
        counters["total_updated"] += mirror.updated
        counters["total_skipped"] += mirror.skipped
        counters["total_removed"] += mirror.removed


@shared_task(
    name="apps.catalog.tasks.alert_stale_catalog_sync",
    soft_time_limit=60,
    time_limit=90,
)
def alert_stale_catalog_sync() -> dict[str, int]:
    """Page the on-call channel when a tenant's catalog stopped refreshing.

    Reads ``Tenant.last_catalog_sync_ok_at`` — the wall-clock
    :class:`~apps.catalog.services.sync.CatalogSyncService` stamps on every
    run that got past the Ayla fetch — and pages once per hour per stale
    tenant through :func:`apps.observability.alerting.page`, which fans out
    to the operators' Telegram channel and to Sentry.

    ### Severity, and why ``error`` rather than ``warning``

    ``warning`` is delivered muted (see the severity matrix in
    ``apps.observability.alerting``). A muted channel is the correct place
    for capacity headroom and is the wrong place for this: while the mirror
    is stale the bot is not degraded, it is *confidently wrong* — it tells
    clients that services the salon actively sells do not exist. That is a
    revenue-losing answer given in the salon's own voice, so it gets the
    unmuted line.

    ### Cadence, and the noise trade

    Hourly, not per-sync-cycle. ``page()`` dedups on a five-minute TTL, so
    a check running on the 15-minute sync cadence would put four lines an
    hour per stale salon into the channel — and an operator who mutes the
    channel is the state this whole ticket exists to prevent. Hourly costs
    up to an extra hour of detection latency on top of the one-hour
    threshold. Two hours against the twelve days this replaces is the right
    side of that trade.

    Returns:
      ``{"checked": N, "stale": M, "paged": P}``. ``paged`` can be lower
      than ``stale`` when :func:`page` dedups or every sink is unreachable;
      it is the honest count of alerts that left the process, not of alerts
      we would have liked to send.
    """
    ages = sync_ages()
    stale = [age for age in ages if age.is_stale]
    paged = 0

    for age in stale:
        sent = page(
            "error",
            f"Каталог салона {age.slug} не синхронизировался {age.age_human}",
            (
                f"tenant={age.slug} (id={age.tenant_id})\n"
                f"last successful catalog sync: "
                f"{age.last_ok_at.isoformat() if age.last_ok_at else 'never'}\n"
                f"age: {age.age_human} (threshold "
                f"{age.threshold_seconds // 60}m)\n\n"
                "Пока это длится, бот отвечает клиентам по устаревшему каталогу и "
                "говорит «такого у наших мастеров нет» про услуги, которые салон "
                "продаёт.\n"
                "Причину искать в логах воркера по catalog.sync.fetch_failed / "
                "catalog.http.row_unparseable для этого tenant_id.\n"
                "Разовый прогон: manage.py sync_catalog --tenant " + age.slug
            ),
            dedup_key=f"catalog_sync_stale:{age.slug}",
        )
        if sent:
            paged += 1

    logger.info(
        "catalog.sync.staleness_checked checked=%d stale=%d paged=%d",
        len(ages),
        len(stale),
        paged,
    )
    return {"checked": len(ages), "stale": len(stale), "paged": paged}
