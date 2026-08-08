"""MasterService bookable-edge upserter tests (DRF-945).

``upsert_master_services`` mirrors Ayla's ``SpecialistService`` edges so
service-specific discovery can join master→service instead of relying on the
free-text ``CatalogMaster.specialization`` that no sync path populates.

The invariant under test throughout: **sync owns only the rows it stamped**.
``MasterService`` is co-owned with the operator (MM4 matrix / invite seeding),
and an operator mapping must survive every beat, including reconciliation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.services.http_client import CatalogSpecialistServiceDTO
from apps.catalog.services.upserter import UpsertResult, upsert_master_services
from apps.tenancy.models import Tenant

_TS = datetime(2026, 7, 9, 18, 31, tzinfo=timezone.utc)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ms-up", name="MS Up", city="Пенза")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="ms-up-b", name="MS Up B", city="Москва")


def _master(tenant: Tenant, *, name: str = "Мастер", specialization: str = "") -> CatalogMaster:
    """A mirrored master. ``id`` IS Ayla's SpecialistProfile.id by contract."""
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        id=uuid.uuid4(),
        name=name,
        specialization=specialization,
        external_updated_at=_TS,
    )


def _service(tenant: Tenant, *, name: str, slug: str = "svc") -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        ayla_service_id=uuid.uuid4(),
        slug=slug,
        name=name,
        external_updated_at=_TS,
    )


def _dto(
    master: CatalogMaster,
    service: CatalogService,
    *,
    edge_id: str | None = None,
    is_active: bool = True,
    tenant_id: str | None = None,
    name: str = "",
) -> CatalogSpecialistServiceDTO:
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=edge_id or str(uuid.uuid4()),
        salon_service=str(service.ayla_service_id),
        specialist=str(master.id),
        external_updated_at=_TS,
        tenant=tenant_id if tenant_id is not None else str(master.tenant_id),
        user_id=str(uuid.uuid4()),
        name=name or service.name,
        category_slug="massage",
        is_active=is_active,
        raw={},
    )


class TestCreate:
    def test_creates_edge_stamped_with_ayla_id(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())

        res = upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id)])

        assert isinstance(res, UpsertResult)
        assert (res.created, res.updated, res.skipped, res.deactivated) == (1, 0, 0, 0)
        row = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)
        assert str(row.ayla_specialist_service_id) == edge_id
        assert row.is_active is True

    def test_one_specialist_many_services(self, tenant: Tenant) -> None:
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")

        res = upsert_master_services(tenant, [_dto(m, s1), _dto(m, s2)])

        assert res.created == 2
        assert MasterService.all_tenants.filter(tenant=tenant, master=m).count() == 2

    def test_one_service_many_specialists(self, tenant: Tenant) -> None:
        s = _service(tenant, name="Спортивный массаж")
        m1, m2 = _master(tenant, name="Анна"), _master(tenant, name="Борис")

        res = upsert_master_services(tenant, [_dto(m1, s), _dto(m2, s)])

        assert res.created == 2
        assert MasterService.all_tenants.filter(tenant=tenant, service=s).count() == 2


class TestIdempotency:
    def test_repeated_sync_is_stable(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        dto = _dto(m, s)

        first = upsert_master_services(tenant, [dto])
        second = upsert_master_services(tenant, [dto])
        third = upsert_master_services(tenant, [dto])

        assert first.created == 1
        assert (second.created, second.updated) == (0, 1)
        assert (third.created, third.updated) == (0, 1)
        # The whole point: no duplicate rows accumulate across beats.
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 1
        assert second.deactivated == third.deactivated == 0

    def test_updates_changed_activity(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id)])

        res = upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, is_active=False)])

        assert (res.created, res.updated) == (0, 1)
        assert MasterService.all_tenants.get(master=m, service=s).is_active is False

    def test_reactivates_when_upstream_returns(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, is_active=False)])

        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, is_active=True)])

        assert MasterService.all_tenants.get(master=m, service=s).is_active is True


class TestReconciliation:
    def test_removed_edge_is_deactivated_not_deleted(self, tenant: Tenant) -> None:
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")
        keep, drop = _dto(m, s1), _dto(m, s2)
        upsert_master_services(tenant, [keep, drop])

        res = upsert_master_services(tenant, [keep])

        assert res.deactivated == 1
        # Deactivated, NOT deleted — the MM4 matrix reads row existence.
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 2
        assert MasterService.all_tenants.get(master=m, service=s2).is_active is False
        assert MasterService.all_tenants.get(master=m, service=s1).is_active is True

    def test_operator_row_is_never_reconciled(self, tenant: Tenant) -> None:
        """The core co-ownership guarantee: NULL stamp ⇒ sync must not touch it."""
        m = _master(tenant)
        s_op = _service(tenant, name="Ручная услуга оператора", slug="manual")
        s_ayla = _service(tenant, name="Спортивный массаж", slug="sport")
        operator_row = MasterService.all_tenants.create(tenant=tenant, master=m, service=s_op)

        res = upsert_master_services(tenant, [_dto(m, s_ayla)])

        assert res.deactivated == 0
        operator_row.refresh_from_db()
        assert operator_row.is_active is True
        assert operator_row.ayla_specialist_service_id is None

    def test_empty_snapshot_vetoes_reconciliation(self, tenant: Tenant) -> None:
        """An empty feed is far likelier a tenant-id mismatch than a real wipe.

        Deactivating everything here would silently re-create the exact
        zero-result discovery bug this mirror exists to fix.
        """
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        upsert_master_services(tenant, [_dto(m, s)])

        res = upsert_master_services(tenant, [])

        assert res.deactivated == 0
        assert MasterService.all_tenants.get(master=m, service=s).is_active is True

    def test_partial_batch_vetoes_reconciliation(self, tenant: Tenant) -> None:
        """Requirement 8 — a partial failure must not leave knowingly-false state."""
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")
        upsert_master_services(tenant, [_dto(m, s1), _dto(m, s2)])

        # s1's edge resolves; the second row blows up on a malformed service id.
        broken = CatalogSpecialistServiceDTO(
            ayla_specialist_service_id=str(uuid.uuid4()),
            salon_service="not-a-uuid",
            specialist=str(m.id),
            external_updated_at=_TS,
            tenant=str(tenant.id),
        )
        res = upsert_master_services(tenant, [_dto(m, s1), broken])

        assert res.errors
        assert res.deactivated == 0
        assert MasterService.all_tenants.get(master=m, service=s2).is_active is True

    def test_reconcile_disabled_by_caller(self, tenant: Tenant) -> None:
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")
        upsert_master_services(tenant, [_dto(m, s1), _dto(m, s2)])

        res = upsert_master_services(tenant, [_dto(m, s1)], reconcile=False)

        assert res.deactivated == 0
        assert MasterService.all_tenants.get(master=m, service=s2).is_active is True


class TestAdoption:
    def test_adopts_existing_operator_pair_without_duplicating(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)
        edge_id = str(uuid.uuid4())

        res = upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id)])

        assert (res.created, res.updated) == (0, 1)
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).count() == 1
        row = MasterService.all_tenants.get(master=m, service=s)
        assert str(row.ayla_specialist_service_id) == edge_id

    def test_reparented_edge_id_does_not_wedge_the_beat(self, tenant: Tenant) -> None:
        """Same Ayla edge id moved to another pair — must not fail forever.

        Without the defensive un-stamp the partial unique constraint would
        reject this write on every single beat.
        """
        m1, m2 = _master(tenant, name="Анна"), _master(tenant, name="Борис")
        s = _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m1, s, edge_id=edge_id)])

        res = upsert_master_services(tenant, [_dto(m2, s, edge_id=edge_id)])

        assert not res.errors
        assert res.created == 1
        assert MasterService.all_tenants.get(master=m2, service=s).ayla_specialist_service_id
        # The old holder released the id rather than colliding on it.
        assert (
            MasterService.all_tenants.get(master=m1, service=s).ayla_specialist_service_id is None
        )


class TestSafety:
    def test_foreign_tenant_payload_is_skipped(self, tenant: Tenant, tenant_b: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")

        res = upsert_master_services(tenant, [_dto(m, s, tenant_id=str(tenant_b.id))])

        assert (res.created, res.skipped) == (0, 1)
        assert not MasterService.all_tenants.filter(tenant=tenant).exists()

    def test_cross_tenant_edge_cannot_be_created(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """Master here, service in another tenant — unrepresentable, not merely rejected."""
        m = _master(tenant)
        foreign_service = _service(tenant_b, name="Спортивный массаж")
        dto = CatalogSpecialistServiceDTO(
            ayla_specialist_service_id=str(uuid.uuid4()),
            salon_service=str(foreign_service.ayla_service_id),
            specialist=str(m.id),
            external_updated_at=_TS,
            tenant=str(tenant.id),
        )

        res = upsert_master_services(tenant, [dto])

        assert (res.created, res.skipped) == (0, 1)
        assert not MasterService.all_tenants.filter(tenant=tenant).exists()
        assert not MasterService.all_tenants.filter(tenant=tenant_b).exists()

    def test_unknown_master_is_skipped(self, tenant: Tenant) -> None:
        s = _service(tenant, name="Спортивный массаж")
        dto = CatalogSpecialistServiceDTO(
            ayla_specialist_service_id=str(uuid.uuid4()),
            salon_service=str(s.ayla_service_id),
            specialist=str(uuid.uuid4()),  # not mirrored yet
            external_updated_at=_TS,
            tenant=str(tenant.id),
        )

        res = upsert_master_services(tenant, [dto])

        assert (res.created, res.skipped) == (0, 1)
        assert not res.errors
        assert not MasterService.all_tenants.filter(tenant=tenant).exists()

    def test_unknown_service_is_skipped(self, tenant: Tenant) -> None:
        m = _master(tenant)
        dto = CatalogSpecialistServiceDTO(
            ayla_specialist_service_id=str(uuid.uuid4()),
            salon_service=str(uuid.uuid4()),  # not mirrored yet
            specialist=str(m.id),
            external_updated_at=_TS,
            tenant=str(tenant.id),
        )

        res = upsert_master_services(tenant, [dto])

        assert (res.created, res.skipped) == (0, 1)
        assert not res.errors

    def test_skipped_edge_does_not_deactivate_live_rows(self, tenant: Tenant) -> None:
        """A skipped (unresolvable) edge must not count as 'seen' nor as absence."""
        m = _master(tenant)
        s = _service(tenant, name="Спортивный массаж")
        live = _dto(m, s)
        upsert_master_services(tenant, [live])

        unresolvable = CatalogSpecialistServiceDTO(
            ayla_specialist_service_id=str(uuid.uuid4()),
            salon_service=str(uuid.uuid4()),
            specialist=str(m.id),
            external_updated_at=_TS,
            tenant=str(tenant.id),
        )
        res = upsert_master_services(tenant, [live, unresolvable])

        assert res.skipped == 1
        assert res.deactivated == 0
        assert MasterService.all_tenants.get(master=m, service=s).is_active is True


class TestPilotShape:
    """Targeted fixture for the formula-tela-like case behind the live failure."""

    def test_massage_specialist_gets_both_services_linked(self, tenant: Tenant) -> None:
        # specialization deliberately empty — this is exactly the production
        # shape: nothing populates it, so discovery must not depend on it.
        master = _master(tenant, name="Массажист Пилот", specialization="")
        sport = _service(tenant, name="Спортивный массаж", slug="sport-massage")
        classic = _service(tenant, name="Классический массаж", slug="classic-massage")

        res = upsert_master_services(
            tenant,
            [_dto(master, sport), _dto(master, classic)],
        )

        assert res.created == 2
        linked = set(
            MasterService.all_tenants.filter(
                tenant=tenant, master=master, is_active=True
            ).values_list("service__name", flat=True)
        )
        assert linked == {"Спортивный массаж", "Классический массаж"}
        assert master.specialization == ""
