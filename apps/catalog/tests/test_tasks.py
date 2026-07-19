"""Catalog Celery task tests — fan-out over tenants (S3B / #1044).

PR-1 syncs only ``CatalogService``, so the fan-out accumulator sums the
``services`` mirror counters. Each test resets the ``Tenant`` table first so
the seeded global bot tenant (identity migration ``0014``) doesn't perturb
the exact-count assertions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.catalog.services.sync import MirrorCounts, SyncResult
from apps.catalog.tasks import sync_catalog_for_all_tenants
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tenants(db) -> list[Tenant]:
    # Reset so the seeded global bot tenant doesn't skew exact counts.
    Tenant.objects.all().delete()
    return [
        Tenant.objects.create(slug="t-a", name="A"),
        Tenant.objects.create(slug="t-b", name="B"),
        Tenant.objects.create(slug="t-c", name="C"),
    ]


def _result(
    *,
    ran: bool = True,
    skipped: bool = False,
    created: int = 0,
    updated: int = 0,
    error: str = "",
) -> SyncResult:
    return SyncResult(
        ran=ran,
        skipped=skipped,
        services=MirrorCounts(created=created, updated=updated),
        cursor_advanced_to=datetime(2026, 5, 13, tzinfo=timezone.utc) if ran else None,
        error=error,
    )


class TestFanOut:
    def test_runs_every_active_tenant(self, tenants: list[Tenant]) -> None:
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.return_value = _result(created=1)
            counters = sync_catalog_for_all_tenants()
        assert instance.run.call_count == len(tenants)
        assert counters["tenants_run"] == len(tenants)
        assert counters["total_created"] == len(tenants)

    def test_per_tenant_failure_does_not_block_others(self, tenants: list[Tenant]) -> None:
        side_effects = [_result(created=1), RuntimeError("boom"), _result(created=2)]
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.side_effect = side_effects
            counters = sync_catalog_for_all_tenants()
        assert counters["tenants_run"] == 2
        assert counters["tenants_failed"] == 1
        assert counters["total_created"] == 3

    def test_skipped_tenant_counted_separately(self, tenants: list[Tenant]) -> None:
        side_effects = [
            _result(created=1),
            SyncResult(ran=False, skipped=True),
            _result(created=2),
        ]
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.side_effect = side_effects
            counters = sync_catalog_for_all_tenants()
        assert counters["tenants_run"] == 2
        assert counters["tenants_skipped"] == 1
        assert counters["tenants_failed"] == 0

    def test_error_field_counts_as_failure(self, tenants: list[Tenant]) -> None:
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.return_value = _result(ran=True, error="boom")
            counters = sync_catalog_for_all_tenants()
        assert counters["tenants_failed"] == len(tenants)
        assert counters["tenants_run"] == 0


class TestEmptyDatabase:
    def test_no_tenants_returns_zeros(self, db) -> None:
        Tenant.objects.all().delete()
        counters = sync_catalog_for_all_tenants()
        assert counters["tenants_run"] == 0
        assert counters["tenants_failed"] == 0


class TestAccumulator:
    def test_counts_summed_across_tenants(self, tenants: list[Tenant]) -> None:
        result = SyncResult(
            ran=True,
            services=MirrorCounts(created=1, updated=2, skipped=5),
            cursor_advanced_to=datetime(2026, 5, 13, tzinfo=timezone.utc),
        )
        with patch("apps.catalog.tasks.CatalogSyncService") as MockService:
            instance = MockService.return_value
            instance.run.return_value = result
            counters = sync_catalog_for_all_tenants()
        assert counters["total_created"] == 1 * len(tenants)
        assert counters["total_updated"] == 2 * len(tenants)
        assert counters["total_skipped"] == 5 * len(tenants)
