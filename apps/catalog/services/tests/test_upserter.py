"""Catalog upserter tests — Ayla catalog → mirrors (S3B / #1044).

PR-1: ``upsert_salon_services`` re-keys ``CatalogService`` on
``(tenant, ayla_service_id)``. PR-2: ``upsert_masters`` (CatalogMaster ←
#1016) + ``upsert_master_services`` (MasterService ← specialist-services).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.services.http_client import (
    CatalogSalonServiceDTO,
    CatalogSpecialistDTO,
    CatalogSpecialistServiceDTO,
)
from apps.catalog.services.upserter import (
    UpsertResult,
    upsert_master_services,
    upsert_masters,
    upsert_salon_services,
)
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cat-up", name="Cat Up")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="cat-up-b", name="Cat Up B")


def _dto(
    ayla_service_id: str | None = None,
    *,
    name: str = "Manicure",
    is_active: bool = True,
    requires_health_check: bool = False,
    price_from: Decimal | None = Decimal("1500.00"),
    duration_min: int | None = 45,
) -> CatalogSalonServiceDTO:
    return CatalogSalonServiceDTO(
        ayla_service_id=ayla_service_id or str(uuid.uuid4()),
        external_updated_at=datetime(2026, 7, 9, 18, 31, tzinfo=timezone.utc),
        name=name,
        is_active=is_active,
        requires_health_check=requires_health_check,
        price_from=price_from,
        duration_min=duration_min,
        template="9d3f0000-0000-4000-8000-000000000002",
        raw={"id": ayla_service_id, "source": "manual"},
    )


class TestCreate:
    def test_creates_row_keyed_on_ayla_service_id(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        res = upsert_salon_services(tenant, [_dto(aid, name="LPG")])
        assert isinstance(res, UpsertResult)
        assert (res.created, res.updated, res.skipped) == (1, 0, 0)
        svc = CatalogService.all_tenants.get(tenant=tenant, ayla_service_id=aid)
        assert svc.name == "LPG"
        assert svc.price_from == Decimal("1500.00")
        assert svc.duration_min == 45
        # Ayla-fed rows carry no integer external_id.
        assert svc.external_id is None

    def test_maps_health_check_and_active(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        upsert_salon_services(tenant, [_dto(aid, requires_health_check=True, is_active=False)])
        svc = CatalogService.all_tenants.get(ayla_service_id=aid)
        assert svc.requires_health_check is True
        assert svc.is_active is False

    def test_raw_payload_retained(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        upsert_salon_services(tenant, [_dto(aid)])
        svc = CatalogService.all_tenants.get(ayla_service_id=aid)
        assert svc.raw["source"] == "manual"


class TestUpdate:
    def test_second_upsert_updates_same_row(self, tenant: Tenant) -> None:
        aid = str(uuid.uuid4())
        upsert_salon_services(tenant, [_dto(aid, name="Old", price_from=Decimal("1000.00"))])
        res = upsert_salon_services(tenant, [_dto(aid, name="New", price_from=Decimal("2000.00"))])
        assert (res.created, res.updated) == (0, 1)
        assert CatalogService.all_tenants.filter(tenant=tenant, ayla_service_id=aid).count() == 1
        svc = CatalogService.all_tenants.get(ayla_service_id=aid)
        assert svc.name == "New"
        assert svc.price_from == Decimal("2000.00")


class TestTenantIsolation:
    def test_same_ayla_id_different_tenants_two_rows(
        self, tenant: Tenant, tenant_b: Tenant
    ) -> None:
        aid = str(uuid.uuid4())
        upsert_salon_services(tenant, [_dto(aid)])
        upsert_salon_services(tenant_b, [_dto(aid)])
        assert CatalogService.all_tenants.filter(ayla_service_id=aid).count() == 2


class TestErrorIsolation:
    def test_bad_row_does_not_abort_batch(self, tenant: Tenant) -> None:
        good = _dto(name="Good")
        # A malformed DTO: external_updated_at None violates the NOT NULL
        # column -> row fails, batch continues.
        bad = CatalogSalonServiceDTO(
            ayla_service_id=str(uuid.uuid4()),
            external_updated_at=None,  # type: ignore[arg-type]
            name="Bad",
        )
        res = upsert_salon_services(tenant, [good, bad])
        assert res.created == 1
        assert len(res.errors) == 1
        assert res.errors[0]["ayla_service_id"] == bad.ayla_service_id


# ---------------------------------------------------------------------------
# PR-2: masters (#1016) + master-services (specialist-services)
# ---------------------------------------------------------------------------


def _spec(specialist_id: str, *, name: str = "Anna", bio: str = "8 yrs.") -> CatalogSpecialistDTO:
    """#1016 name/bio enrichment DTO (identity lives on the edge now)."""
    return CatalogSpecialistDTO(specialist_id=specialist_id, name=name, bio=bio)


def _spec_svc(
    bid: str,
    *,
    specialist: str,
    salon_service: str,
    user_id: str,
    reviews: int = 200,
    is_active: bool = True,
) -> CatalogSpecialistServiceDTO:
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=bid,
        salon_service=salon_service,
        specialist=specialist,
        ayla_user_id=user_id,
        external_updated_at=datetime(2026, 7, 9, 18, 31, tzinfo=timezone.utc),
        resolved_duration=45,
        resolved_requires_health_check=True,
        price=Decimal("1500.00"),
        is_active=is_active,
        rating=Decimal("4.8"),
        review_count=reviews,
    )


class TestUpsertMasters:
    def test_creates_master_identity_from_edge_name_from_1016(self, tenant: Tenant) -> None:
        pid, uid, bid, sid = (str(uuid.uuid4()) for _ in range(4))
        edge = _spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid, reviews=200)
        res = upsert_masters(tenant, [edge], [_spec(pid, name="LPG Master")])
        assert (res.created, res.updated) == (1, 0)
        m = CatalogMaster.all_tenants.get(tenant=tenant, ayla_user_id=uid)
        assert m.name == "LPG Master"  # from #1016
        assert m.review_count == 200  # from the edge
        assert m.rating == Decimal("4.8")  # from the edge
        assert m.external_id is None

    def test_master_created_even_without_1016_enrichment(self, tenant: Tenant) -> None:
        # BLOCKER 2: an is_available=False specialist 404s on #1016, but its
        # edge still carries user_id — the master must still be created.
        pid, uid, bid, sid = (str(uuid.uuid4()) for _ in range(4))
        edge = _spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid, reviews=50)
        res = upsert_masters(tenant, [edge], [])  # no #1016 enrichment
        assert (res.created, res.skipped) == (1, 0)
        m = CatalogMaster.all_tenants.get(ayla_user_id=uid)
        assert m.name == ""  # no #1016 name
        assert m.review_count == 50  # still populated from the edge

    def test_dedupes_masters_by_user_id(self, tenant: Tenant) -> None:
        # One specialist, two service edges → ONE CatalogMaster.
        pid, uid = str(uuid.uuid4()), str(uuid.uuid4())
        s1, s2, b1, b2 = (str(uuid.uuid4()) for _ in range(4))
        edges = [
            _spec_svc(b1, specialist=pid, salon_service=s1, user_id=uid),
            _spec_svc(b2, specialist=pid, salon_service=s2, user_id=uid),
        ]
        res = upsert_masters(tenant, edges, [_spec(pid)])
        assert res.created == 1
        assert CatalogMaster.all_tenants.filter(tenant=tenant, ayla_user_id=uid).count() == 1

    def test_platform_fields_untouched(self, tenant: Tenant) -> None:
        pid, uid, bid, sid = (str(uuid.uuid4()) for _ in range(4))
        upsert_masters(
            tenant, [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)], [_spec(pid)]
        )
        m = CatalogMaster.all_tenants.get(ayla_user_id=uid)
        assert m.invite_status == CatalogMaster.InviteStatus.ACCEPTED


class TestUpsertMasterServices:
    def _svc(self, tenant: Tenant, ayla_service_id: str) -> CatalogService:
        return CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=datetime(2026, 7, 9, tzinfo=timezone.utc),
            ayla_service_id=ayla_service_id,
            slug="s",
            name="S",
        )

    def _sync_master(self, tenant: Tenant, pid: str, uid: str, sid: str, bid: str) -> None:
        upsert_masters(
            tenant, [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)], [_spec(pid)]
        )

    def test_creates_edge_with_bookable_fields(self, tenant: Tenant) -> None:
        pid, uid, sid, bid = (str(uuid.uuid4()) for _ in range(4))
        self._svc(tenant, sid)
        self._sync_master(tenant, pid, uid, sid, bid)
        res = upsert_master_services(
            tenant, [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)]
        )
        assert (res.created, res.skipped) == (1, 0)
        ms = MasterService.all_tenants.get(tenant=tenant, ayla_specialist_service_id=bid)
        assert ms.resolved_duration == 45
        assert ms.resolved_requires_health_check is True
        assert ms.price == Decimal("1500.00")
        assert ms.is_active is True
        assert str(ms.master.ayla_user_id) == uid
        assert str(ms.service.ayla_service_id) == sid

    def test_adopts_preexisting_admin_row(self, tenant: Tenant) -> None:
        # BLOCKER 1: an admin-matrix row for (master, service) with a NULL
        # bookable id must be ADOPTED (backfilled), not collided-on-create.
        pid, uid, sid, bid = (str(uuid.uuid4()) for _ in range(4))
        svc = self._svc(tenant, sid)
        self._sync_master(tenant, pid, uid, sid, bid)
        master = CatalogMaster.all_tenants.get(ayla_user_id=uid)
        admin_row = MasterService.all_tenants.create(tenant=tenant, master=master, service=svc)
        assert admin_row.ayla_specialist_service_id is None

        res = upsert_master_services(
            tenant, [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)]
        )
        assert (res.created, res.updated) == (0, 1)  # adopted, not created
        assert (
            MasterService.all_tenants.filter(tenant=tenant, master=master, service=svc).count() == 1
        )
        admin_row.refresh_from_db()
        assert str(admin_row.ayla_specialist_service_id) == bid
        assert admin_row.resolved_requires_health_check is True

    def test_persists_is_active_false(self, tenant: Tenant) -> None:
        pid, uid, sid, bid = (str(uuid.uuid4()) for _ in range(4))
        self._svc(tenant, sid)
        self._sync_master(tenant, pid, uid, sid, bid)
        upsert_master_services(
            tenant,
            [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid, is_active=False)],
        )
        ms = MasterService.all_tenants.get(ayla_specialist_service_id=bid)
        assert ms.is_active is False

    def test_skip_when_master_mirror_missing(self, tenant: Tenant) -> None:
        pid, uid, sid, bid = (str(uuid.uuid4()) for _ in range(4))
        self._svc(tenant, sid)
        # No master synced → skip.
        res = upsert_master_services(
            tenant, [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)]
        )
        assert (res.created, res.skipped) == (0, 1)
        assert not MasterService.all_tenants.filter(ayla_specialist_service_id=bid).exists()

    def test_skip_when_service_mirror_missing(self, tenant: Tenant) -> None:
        pid, uid, sid, bid = (str(uuid.uuid4()) for _ in range(4))
        self._sync_master(tenant, pid, uid, sid, bid)
        # salon_service sid was never synced → no CatalogService → skip.
        res = upsert_master_services(
            tenant, [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)]
        )
        assert (res.created, res.skipped) == (0, 1)

    def test_idempotent(self, tenant: Tenant) -> None:
        pid, uid, sid, bid = (str(uuid.uuid4()) for _ in range(4))
        self._svc(tenant, sid)
        self._sync_master(tenant, pid, uid, sid, bid)
        edge = [_spec_svc(bid, specialist=pid, salon_service=sid, user_id=uid)]
        upsert_master_services(tenant, edge)
        res = upsert_master_services(tenant, edge)
        assert (res.created, res.updated) == (0, 1)
        assert MasterService.all_tenants.filter(ayla_specialist_service_id=bid).count() == 1
