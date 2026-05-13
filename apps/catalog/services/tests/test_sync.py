"""CatalogSyncService tests (DRF-575 / Sprint 7 / C4)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.core.cache import cache

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogService
from apps.catalog.services.http_client import (
    CatalogFaqDTO,
    CatalogHelpArticleDTO,
    CatalogMasterDTO,
    CatalogServiceDTO,
)
from apps.catalog.services.sync import (
    EVENT_CATALOG_SYNCED,
    CatalogSyncService,
    SyncResult,
)
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _cache_clear():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cat-sync", name="Cat Sync")


def _ts(hour: int = 10) -> datetime:
    return datetime(2026, 5, 13, hour, 0, tzinfo=timezone.utc)


class FakeHttpClient:
    """Mocks CatalogHttpClient — captures calls + returns canned DTOs."""

    def __init__(
        self,
        *,
        services: list[CatalogServiceDTO] | None = None,
        masters: list[CatalogMasterDTO] | None = None,
        faqs: list[CatalogFaqDTO] | None = None,
        help_articles: list[CatalogHelpArticleDTO] | None = None,
        raise_on_fetch: type[Exception] | None = None,
    ) -> None:
        self._services = services or []
        self._masters = masters or []
        self._faqs = faqs or []
        self._help = help_articles or []
        self._raise = raise_on_fetch
        self.since_seen: list[datetime | None] = []

    def fetch_services(self, *, since: datetime | None = None) -> list[CatalogServiceDTO]:
        if self._raise is not None:
            raise self._raise("synthetic")
        self.since_seen.append(since)
        return self._services

    def fetch_masters(self, *, since: datetime | None = None) -> list[CatalogMasterDTO]:
        return self._masters

    def fetch_faqs(self, *, since: datetime | None = None) -> list[CatalogFaqDTO]:
        return self._faqs

    def fetch_help_articles(self, *, since: datetime | None = None) -> list[CatalogHelpArticleDTO]:
        return self._help

    def close(self) -> None: ...

    def __enter__(self) -> FakeHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class TestHappyPath:
    def test_first_run_pulls_and_creates(self, tenant: Tenant) -> None:
        http = FakeHttpClient(
            services=[
                CatalogServiceDTO(
                    external_id=1,
                    external_updated_at=_ts(),
                    slug="s1",
                    name="Service 1",
                )
            ],
            masters=[
                CatalogMasterDTO(
                    external_id=10,
                    external_updated_at=_ts(),
                    name="Анна",
                )
            ],
        )
        result = CatalogSyncService(http_client=http).run(tenant)
        assert isinstance(result, SyncResult)
        assert result.ran is True
        assert result.skipped is False
        assert result.services.created == 1
        assert result.masters.created == 1
        assert result.cursor_advanced_to == _ts()
        # Cursor persisted.
        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at == _ts()

    def test_subsequent_run_passes_since(self, tenant: Tenant) -> None:
        tenant.last_catalog_sync_at = _ts(10)
        tenant.save()
        http = FakeHttpClient()
        CatalogSyncService(http_client=http).run(tenant)
        # The first since= seen by fetch_services should be the cursor.
        assert http.since_seen == [_ts(10)]

    def test_cursor_unchanged_on_empty_pull(self, tenant: Tenant) -> None:
        http = FakeHttpClient()  # all empty lists
        before = tenant.last_catalog_sync_at
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is True
        assert result.cursor_advanced_to is None
        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at == before


class TestLock:
    def test_lock_held_returns_skipped(self, tenant: Tenant) -> None:
        # Pre-acquire the lock to simulate another beat already running.
        cache.add(f"catalog_sync_lock:{tenant.id}", "1", timeout=60)
        http = FakeHttpClient()
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is False
        assert result.skipped is True
        # No fetcher work happened.
        assert http.since_seen == []

    def test_lock_released_on_completion(self, tenant: Tenant) -> None:
        http = FakeHttpClient()
        CatalogSyncService(http_client=http).run(tenant)
        # Second run on same tenant should NOT be skipped — lock got
        # released in the `finally` block.
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is True

    def test_lock_released_on_fetch_error(self, tenant: Tenant) -> None:
        http = FakeHttpClient(raise_on_fetch=RuntimeError)
        CatalogSyncService(http_client=http).run(tenant)
        # Lock released even though fetch threw — next run can proceed.
        http2 = FakeHttpClient()  # OK this time
        result = CatalogSyncService(http_client=http2).run(tenant)
        assert result.ran is True
        assert result.error == ""


class TestFetchError:
    def test_returns_error_result_does_not_advance_cursor(self, tenant: Tenant) -> None:
        tenant.last_catalog_sync_at = _ts(8)
        tenant.save()
        http = FakeHttpClient(raise_on_fetch=RuntimeError)
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is True
        assert "synthetic" in result.error
        # No mirrors written, cursor unchanged.
        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at == _ts(8)
        assert CatalogService.all_tenants.filter(tenant=tenant).count() == 0


class TestAuditAndEvent:
    def test_audit_row_on_success(self, tenant: Tenant) -> None:
        http = FakeHttpClient(
            services=[
                CatalogServiceDTO(
                    external_id=1,
                    external_updated_at=_ts(),
                    slug="s",
                    name="S",
                )
            ]
        )
        CatalogSyncService(http_client=http).run(tenant)
        rows = AuditLog.all_tenants.filter(action=EVENT_CATALOG_SYNCED)
        assert rows.exists()
        row = rows.first()
        assert row is not None
        assert row.payload["counts"]["services"]["created"] == 1


class TestCursorAdvanceMax:
    def test_cursor_takes_max_across_all_mirrors(self, tenant: Tenant) -> None:
        # Different upstream timestamps across mirror types — cursor
        # must land on the MAX across the bundle so a row that arrives
        # in the next minute on a less-updated mirror still gets picked
        # up.
        http = FakeHttpClient(
            services=[
                CatalogServiceDTO(
                    external_id=1,
                    external_updated_at=_ts(10),
                    slug="s",
                    name="S",
                )
            ],
            faqs=[
                CatalogFaqDTO(
                    external_id=1,
                    external_updated_at=_ts(14),  # newer than services
                    question="Q",
                    answer="A",
                )
            ],
        )
        result = CatalogSyncService(http_client=http).run(tenant)  # type: ignore[arg-type]
        assert result.cursor_advanced_to == _ts(14)


class TestPerMirrorCounts:
    def test_all_four_mirrors_counted(self, tenant: Tenant) -> None:
        http = FakeHttpClient(
            services=[
                CatalogServiceDTO(
                    external_id=1,
                    external_updated_at=_ts(),
                    slug="s",
                    name="S",
                )
            ],
            masters=[
                CatalogMasterDTO(
                    external_id=10,
                    external_updated_at=_ts(),
                    name="M",
                )
            ],
            faqs=[
                CatalogFaqDTO(
                    external_id=20,
                    external_updated_at=_ts(),
                    question="Q",
                    answer="A",
                )
            ],
            help_articles=[
                CatalogHelpArticleDTO(
                    external_id=30,
                    external_updated_at=_ts(),
                    question="H",
                    answer="A",
                )
            ],
        )
        result = CatalogSyncService(http_client=http).run(tenant)  # type: ignore[arg-type]
        assert result.services.created == 1
        assert result.masters.created == 1
        assert result.faqs.created == 1
        assert result.help_articles.created == 1


class TestStaleSkip:
    def test_re_run_with_same_data_skips_all(self, tenant: Tenant) -> None:
        ts = _ts()
        http = FakeHttpClient(
            services=[
                CatalogServiceDTO(
                    external_id=1,
                    external_updated_at=ts,
                    slug="s",
                    name="S",
                )
            ]
        )
        CatalogSyncService(http_client=http).run(tenant)  # type: ignore[arg-type]
        # Second run — same data → upserter sees existing rows + equal
        # timestamp → all skipped.
        result = CatalogSyncService(http_client=http).run(tenant)  # type: ignore[arg-type]
        assert result.services.skipped == 1
        assert result.services.created == 0
        assert result.services.updated == 0
