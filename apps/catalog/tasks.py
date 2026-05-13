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

Wired in ``config.settings.base::CELERY_BEAT_SCHEDULE`` at the
``catalog-sync-every-15min`` key (default 15-minute cadence,
deliberately matched to the lock TTL ÷ 1.5 in
:mod:`apps.catalog.services.sync` so a slow run can't race itself).

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
      total_created, total_updated, total_skipped}`` aggregated
      across the fan-out. Surfaced to ``check_agents`` health (Sprint
      8) for "last sync OK across N/M tenants".
    """
    counters = {
        "tenants_run": 0,
        "tenants_skipped": 0,
        "tenants_failed": 0,
        "total_created": 0,
        "total_updated": 0,
        "total_skipped": 0,
    }

    service = CatalogSyncService()
    for tenant in Tenant.objects.all().iterator():
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
        "created=%s updated=%s skipped_rows=%s",
        counters["tenants_run"],
        counters["tenants_skipped"],
        counters["tenants_failed"],
        counters["total_created"],
        counters["total_updated"],
        counters["total_skipped"],
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
    for mirror in (
        result.services,
        result.masters,
        result.faqs,
        result.help_articles,
    ):
        counters["total_created"] += mirror.created
        counters["total_updated"] += mirror.updated
        counters["total_skipped"] += mirror.skipped
