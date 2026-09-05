"""`last_catalog_sync_ok_at` answers "did it run" (DRF-1494).

`last_catalog_sync_at` cannot: it holds `max(external_updated_at)` across
the rows the pull returned, so a salon nobody has edited for three weeks
and a salon whose sync died three weeks ago carry the identical value.
That ambiguity is why the pilot's twelve-day freeze looked exactly like a
quiet salon. These tests pin the two columns apart.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from django.core.cache import cache
from django.utils import timezone as dj_timezone

from apps.catalog.services.http_client import CatalogSalonServiceDTO, CatalogTransportError
from apps.catalog.services.sync import CatalogSyncService
from apps.catalog.services.tests.test_sync import FakeHttpClient
from apps.tenancy.models import Tenant

STALE_CONTENT = datetime(2026, 8, 23, 9, 21, 27, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="formula-tela", name="Formula Tela")


def _salon() -> CatalogSalonServiceDTO:
    return CatalogSalonServiceDTO(
        ayla_service_id=str(uuid.uuid4()),
        external_updated_at=STALE_CONTENT,
        name="Маникюр классический",
    )


class TestTheTwoColumnsAreDifferentQuestions:
    def test_a_run_over_an_unedited_catalog_still_advances_the_clock(self, tenant: Tenant) -> None:
        # This is the exact shape of the pilot's confusion: upstream content
        # dated 2026-08-23, fetched successfully today. The content watermark
        # is honestly old; the run clock must be now, or health is unreadable.
        http = FakeHttpClient(services=[_salon()])
        before = dj_timezone.now()

        CatalogSyncService(http_client=http).run(tenant)

        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at == STALE_CONTENT
        assert tenant.last_catalog_sync_ok_at is not None
        assert tenant.last_catalog_sync_ok_at >= before

    def test_an_empty_catalog_still_advances_the_clock(self, tenant: Tenant) -> None:
        # No rows means no watermark to write — but the fetch succeeded, so
        # the sync is healthy and must not age into the alarm. Gating the
        # clock on `new_cursor is not None` would page on every empty salon.
        http = FakeHttpClient(services=[])

        CatalogSyncService(http_client=http).run(tenant)

        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at is None
        assert tenant.last_catalog_sync_ok_at is not None


class TestFailureLeavesTheClockToAge:
    def test_fetch_failure_does_not_stamp_the_clock(self, tenant: Tenant) -> None:
        stamped_yesterday = dj_timezone.now() - timedelta(days=1)
        tenant.last_catalog_sync_ok_at = stamped_yesterday
        tenant.save(update_fields=["last_catalog_sync_ok_at"])

        result = CatalogSyncService(
            http_client=FakeHttpClient(raise_on_fetch=CatalogTransportError)
        ).run(tenant)

        # Presence: the run really was attempted and really did fail, so the
        # untouched clock below is the failure path and not a no-op fixture.
        assert result.ran is True
        assert result.error != ""

        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_ok_at == stamped_yesterday

    def test_a_successful_run_on_the_same_tenant_does_stamp_it(self, tenant: Tenant) -> None:
        # The paired positive. Without it, "the clock did not move" would be
        # equally true of a column nothing ever writes.
        stamped_yesterday = dj_timezone.now() - timedelta(days=1)
        tenant.last_catalog_sync_ok_at = stamped_yesterday
        tenant.save(update_fields=["last_catalog_sync_ok_at"])

        result = CatalogSyncService(http_client=FakeHttpClient(services=[_salon()])).run(tenant)

        assert result.ran is True
        assert result.error == ""

        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_ok_at is not None
        assert tenant.last_catalog_sync_ok_at > stamped_yesterday
