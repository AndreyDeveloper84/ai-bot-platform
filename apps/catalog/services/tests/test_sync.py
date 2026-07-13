"""CatalogSyncService tests — Ayla salon-services (S3B / #1044)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from django.core.cache import cache

from apps.audit.models import AuditLog
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.services.http_client import (
    CatalogSalonServiceDTO,
    CatalogSpecialistDTO,
    CatalogSpecialistServiceDTO,
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


def _salon(aid: str, *, name: str = "S", ts: datetime | None = None) -> CatalogSalonServiceDTO:
    return CatalogSalonServiceDTO(
        ayla_service_id=aid,
        external_updated_at=ts or _ts(),
        name=name,
    )


class FakeHttpClient:
    """Mocks CatalogHttpClient — captures calls + returns canned DTOs."""

    def __init__(
        self,
        *,
        services: list[CatalogSalonServiceDTO] | None = None,
        spec_svcs: list[CatalogSpecialistServiceDTO] | None = None,
        specialists: list[CatalogSpecialistDTO] | None = None,
        raise_on_fetch: type[Exception] | None = None,
    ) -> None:
        self._services = services or []
        self._spec_svcs = spec_svcs or []
        self._specialists = specialists or []
        self._raise = raise_on_fetch
        self.tenant_ids_seen: list[str] = []
        self.specialist_ids_seen: list[str] = []

    def fetch_salon_services(self, *, tenant_id: str) -> list[CatalogSalonServiceDTO]:
        if self._raise is not None:
            raise self._raise("synthetic")
        self.tenant_ids_seen.append(tenant_id)
        return self._services

    def fetch_specialist_services(self, *, tenant_id: str) -> list[CatalogSpecialistServiceDTO]:
        return self._spec_svcs

    def fetch_specialists(self, *, specialist_ids: list[str]) -> list[CatalogSpecialistDTO]:
        self.specialist_ids_seen = list(specialist_ids)
        return self._specialists

    def close(self) -> None: ...

    def __enter__(self) -> FakeHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _spec(pid: str) -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(specialist_id=pid, name="M", bio="")


def _spec_svc(
    bid: str, *, specialist: str, salon_service: str, user_id: str
) -> CatalogSpecialistServiceDTO:
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=bid,
        salon_service=salon_service,
        specialist=specialist,
        ayla_user_id=user_id,
        external_updated_at=_ts(),
        resolved_duration=45,
        resolved_requires_health_check=True,
        review_count=5,
    )


class TestThreeEndpointFlow:
    def test_masters_and_bookable_edges_synced(self, tenant: Tenant) -> None:
        sid, pid, uid, bid = (str(uuid.uuid4()) for _ in range(4))
        http = FakeHttpClient(
            services=[_salon(sid)],
            spec_svcs=[_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)],
            specialists=[_spec(pid)],
        )
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.services.created == 1
        assert result.masters.created == 1
        assert result.master_services.created == 1
        # The bot passed the specialist ids the edges referenced.
        assert http.specialist_ids_seen == [pid]
        ms = MasterService.all_tenants.get(tenant=tenant, ayla_specialist_service_id=bid)
        assert str(ms.master.ayla_user_id) == uid
        assert str(ms.service.ayla_service_id) == sid
        assert ms.resolved_requires_health_check is True
        assert CatalogMaster.all_tenants.filter(tenant=tenant, ayla_user_id=uid).exists()


class TestHappyPath:
    def test_first_run_pulls_and_creates(self, tenant: Tenant) -> None:
        http = FakeHttpClient(services=[_salon(str(uuid.uuid4()), name="Service 1")])
        result = CatalogSyncService(http_client=http).run(tenant)
        assert isinstance(result, SyncResult)
        assert result.ran is True
        assert result.skipped is False
        assert result.services.created == 1
        assert result.cursor_advanced_to == _ts()
        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at == _ts()

    def test_passes_tenant_id_to_fetch(self, tenant: Tenant) -> None:
        http = FakeHttpClient()
        CatalogSyncService(http_client=http).run(tenant)
        assert http.tenant_ids_seen == [str(tenant.id)]

    def test_cursor_unchanged_on_empty_pull(self, tenant: Tenant) -> None:
        http = FakeHttpClient()  # empty
        before = tenant.last_catalog_sync_at
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is True
        assert result.cursor_advanced_to is None
        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at == before

    def test_cursor_takes_max_across_rows(self, tenant: Tenant) -> None:
        http = FakeHttpClient(
            services=[
                _salon(str(uuid.uuid4()), ts=_ts(10)),
                _salon(str(uuid.uuid4()), ts=_ts(14)),  # newer
            ]
        )
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.cursor_advanced_to == _ts(14)


class TestLock:
    def test_lock_held_returns_skipped(self, tenant: Tenant) -> None:
        cache.add(f"catalog_sync_lock:{tenant.id}", "1", timeout=60)
        http = FakeHttpClient()
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is False
        assert result.skipped is True
        assert http.tenant_ids_seen == []  # no fetch happened

    def test_lock_released_on_completion(self, tenant: Tenant) -> None:
        http = FakeHttpClient()
        CatalogSyncService(http_client=http).run(tenant)
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is True

    def test_lock_released_on_fetch_error(self, tenant: Tenant) -> None:
        http = FakeHttpClient(raise_on_fetch=RuntimeError)
        CatalogSyncService(http_client=http).run(tenant)
        http2 = FakeHttpClient()
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
        tenant.refresh_from_db()
        assert tenant.last_catalog_sync_at == _ts(8)
        assert CatalogService.all_tenants.filter(tenant=tenant).count() == 0


class TestAuditAndEvent:
    def test_audit_row_on_success(self, tenant: Tenant) -> None:
        http = FakeHttpClient(services=[_salon(str(uuid.uuid4()))])
        CatalogSyncService(http_client=http).run(tenant)
        rows = AuditLog.all_tenants.filter(action=EVENT_CATALOG_SYNCED)
        assert rows.exists()
        row = rows.first()
        assert row is not None
        assert row.payload["counts"]["services"]["created"] == 1


class TestIdempotent:
    def test_re_run_same_data_updates_not_duplicates(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        http = FakeHttpClient(services=[_salon(aid, name="S")])
        CatalogSyncService(http_client=http).run(tenant)
        result = CatalogSyncService(http_client=http).run(tenant)
        # update_or_create on the stable UUID → same row updated, no dupe.
        assert result.services.created == 0
        assert result.services.updated == 1
        assert CatalogService.all_tenants.filter(tenant=tenant, ayla_service_id=aid).count() == 1
