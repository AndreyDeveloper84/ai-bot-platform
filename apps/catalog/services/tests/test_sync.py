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
    EdgeSnapshot,
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
        specialists: list[CatalogSpecialistDTO] | None = None,
        specialist_services: list[CatalogSpecialistServiceDTO] | None = None,
        edges_complete: bool = True,
        raise_on_fetch: type[Exception] | None = None,
        raise_on_specialists: type[Exception] | None = None,
        raise_on_specialist_services: type[Exception] | None = None,
    ) -> None:
        self._services = services or []
        self._specialists = specialists or []
        self._specialist_services = specialist_services or []
        self._edges_complete = edges_complete
        self._raise = raise_on_fetch
        self._raise_specialists = raise_on_specialists
        self._raise_specialist_services = raise_on_specialist_services
        self.tenant_ids_seen: list[str] = []
        self.edge_tenant_ids_seen: list[str] = []

    def fetch_salon_services(self, *, tenant_id: str) -> list[CatalogSalonServiceDTO]:
        if self._raise is not None:
            raise self._raise("synthetic")
        self.tenant_ids_seen.append(tenant_id)
        return self._services

    def fetch_specialists(self) -> list[CatalogSpecialistDTO]:
        if self._raise_specialists is not None:
            raise self._raise_specialists("synthetic")
        return self._specialists

    def fetch_specialist_services(self, *, tenant_id: str) -> EdgeSnapshot:
        if self._raise_specialist_services is not None:
            raise self._raise_specialist_services("synthetic")
        self.edge_tenant_ids_seen.append(tenant_id)
        return EdgeSnapshot(edges=self._specialist_services, complete=self._edges_complete)

    def close(self) -> None: ...

    def __enter__(self) -> FakeHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


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


def _specialist(mid: str, *, name: str = "Анна") -> CatalogSpecialistDTO:
    return CatalogSpecialistDTO(
        ayla_master_id=mid,
        user_id=str(uuid.uuid4()),
        name=name,
        external_updated_at=_ts(),
        experience="5",
    )


class TestMastersMirror:
    def test_masters_upserted_in_same_cycle(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        http = FakeHttpClient(specialists=[_specialist(mid)])
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.masters.created == 1
        assert result.masters.errors == 0
        from apps.catalog.models import CatalogMaster

        m = CatalogMaster.all_tenants.get(tenant=tenant, id=mid)
        assert m.name == "Анна"
        assert m.invite_status == CatalogMaster.InviteStatus.ACCEPTED

    def test_specialists_fetch_failure_isolated(self, tenant: Tenant) -> None:
        """A specialists fetch failure must not abort the services mirror."""
        http = FakeHttpClient(
            services=[_salon(str(uuid.uuid4()), name="Service 1")],
            raise_on_specialists=RuntimeError,
        )
        result = CatalogSyncService(http_client=http).run(tenant)
        assert result.ran is True
        assert result.services.created == 1
        assert result.masters.errors == 1

    def test_rerun_masters_idempotent(self, tenant: Tenant) -> None:
        mid = str(uuid.uuid4())
        http = FakeHttpClient(specialists=[_specialist(mid)])
        svc = CatalogSyncService(http_client=http)
        svc.run(tenant)
        result = svc.run(tenant)
        assert result.masters.created == 0
        assert result.masters.updated == 1
        from apps.catalog.models import CatalogMaster

        assert CatalogMaster.all_tenants.filter(id=mid).count() == 1

    def test_audit_payload_carries_masters_counts(self, tenant: Tenant) -> None:
        http = FakeHttpClient(specialists=[_specialist(str(uuid.uuid4()))])
        CatalogSyncService(http_client=http).run(tenant)
        log = AuditLog.all_tenants.filter(action=EVENT_CATALOG_SYNCED).first()
        assert log is not None
        assert "masters" in log.payload["counts"]
        assert log.payload["counts"]["masters"]["created"] == 1


def _edge(
    edge_id: str,
    *,
    specialist_id: str,
    salon_service_id: str,
    tenant_id: str,
    is_active: bool = True,
) -> CatalogSpecialistServiceDTO:
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=edge_id,
        salon_service=salon_service_id,
        specialist=specialist_id,
        external_updated_at=_ts(),
        tenant=tenant_id,
        name="Спортивный массаж",
        is_active=is_active,
    )


class TestMasterServicesMirror:
    """Third mirror: specialist-services → MasterService (DRF-945)."""

    def test_full_cycle_links_master_to_service(self, tenant: Tenant) -> None:
        mid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        http = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[
                _edge(eid, specialist_id=mid, salon_service_id=sid, tenant_id=str(tenant.id))
            ],
        )

        result = CatalogSyncService(http_client=http).run(tenant)

        assert result.ran is True
        assert result.master_services.created == 1
        row = MasterService.all_tenants.get(tenant=tenant)
        assert str(row.master_id) == mid
        assert row.service.name == "Спортивный массаж"
        assert row.ayla_specialist_service_id is not None

    def test_edge_fetch_is_tenant_scoped(self, tenant: Tenant) -> None:
        http = FakeHttpClient()
        CatalogSyncService(http_client=http).run(tenant)
        assert http.edge_tenant_ids_seen == [str(tenant.id)]

    def test_edge_fetch_failure_isolated(self, tenant: Tenant) -> None:
        """An edges failure must not undo the two mirrors that already landed."""
        mid, sid = str(uuid.uuid4()), str(uuid.uuid4())
        http = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            raise_on_specialist_services=RuntimeError,
        )

        result = CatalogSyncService(http_client=http).run(tenant)

        assert result.ran is True
        assert result.services.created == 1
        assert result.masters.created == 1
        assert result.master_services.errors == 1
        assert result.master_services.created == 0

    def test_edge_fetch_failure_does_not_remove_existing(self, tenant: Tenant) -> None:
        """Requirement 8 — a failed fetch must not reconcile anything away."""
        mid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        good = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[
                _edge(eid, specialist_id=mid, salon_service_id=sid, tenant_id=str(tenant.id))
            ],
        )
        CatalogSyncService(http_client=good).run(tenant)

        broken = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            raise_on_specialist_services=RuntimeError,
        )
        CatalogSyncService(http_client=broken).run(tenant)

        assert MasterService.all_tenants.filter(tenant=tenant).count() == 1

    def test_rerun_is_idempotent(self, tenant: Tenant) -> None:
        mid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        http = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[
                _edge(eid, specialist_id=mid, salon_service_id=sid, tenant_id=str(tenant.id))
            ],
        )
        svc = CatalogSyncService(http_client=http)
        svc.run(tenant)
        result = svc.run(tenant)

        assert result.master_services.created == 0
        assert result.master_services.updated == 1
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 1

    def test_mirror_order_lets_edges_resolve_first_run(self, tenant: Tenant) -> None:
        """Edges must resolve on the SAME beat that creates master + service."""
        mid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        http = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[
                _edge(eid, specialist_id=mid, salon_service_id=sid, tenant_id=str(tenant.id))
            ],
        )

        result = CatalogSyncService(http_client=http).run(tenant)

        assert result.master_services.skipped == 0
        assert result.master_services.created == 1
        assert CatalogMaster.all_tenants.filter(id=mid).exists()

    def test_audit_payload_carries_master_services_counts(self, tenant: Tenant) -> None:
        mid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        http = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[
                _edge(eid, specialist_id=mid, salon_service_id=sid, tenant_id=str(tenant.id))
            ],
        )
        CatalogSyncService(http_client=http).run(tenant)

        log = AuditLog.all_tenants.filter(action=EVENT_CATALOG_SYNCED).first()
        assert log is not None
        assert log.payload["counts"]["master_services"]["created"] == 1
        assert "removed" in log.payload["counts"]["master_services"]

    def test_upsert_failure_preserves_run_bookkeeping(
        self, tenant: Tenant, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An upsert blowup must not swallow the audit row + cursor advance.

        The two mirrors that already landed committed in their own
        transactions; if the exception escaped, the beat would look like a
        total failure and the operator would lose the counters entirely.
        """
        import apps.catalog.services.sync as sync_mod

        mid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        http = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[
                _edge(eid, specialist_id=mid, salon_service_id=sid, tenant_id=str(tenant.id))
            ],
        )

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("synthetic upsert failure")

        monkeypatch.setattr(sync_mod, "upsert_master_services", _boom)

        result = CatalogSyncService(http_client=http).run(tenant)

        assert result.ran is True
        assert result.services.created == 1
        assert result.masters.created == 1
        assert result.master_services.errors == 1
        assert result.cursor_advanced_to is not None
        assert AuditLog.all_tenants.filter(action=EVENT_CATALOG_SYNCED).exists()

    def test_incomplete_snapshot_downgrades_to_additive_only(self, tenant: Tenant) -> None:
        """A page-walk that lost a row must not license deletion.

        Reconciliation infers "gone upstream" from absence; a row dropped by a
        shifted page window is indistinguishable from a deleted one.
        """
        mid, sid, eid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        edge = _edge(eid, specialist_id=mid, salon_service_id=sid, tenant_id=str(tenant.id))
        seeded = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[edge],
        )
        CatalogSyncService(http_client=seeded).run(tenant)
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 1

        # Next beat: the edge is missing AND the snapshot is flagged incomplete.
        degraded = FakeHttpClient(
            services=[_salon(sid, name="Спортивный массаж")],
            specialists=[_specialist(mid)],
            specialist_services=[],
            edges_complete=False,
        )
        result = CatalogSyncService(http_client=degraded).run(tenant)

        assert result.master_services.removed == 0
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 1
