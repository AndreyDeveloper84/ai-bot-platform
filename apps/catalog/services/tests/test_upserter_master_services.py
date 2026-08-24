"""MasterService bookable-edge upserter tests (DRF-945).

``upsert_master_services`` mirrors Ayla's ``SpecialistService`` edges so
service-specific discovery can join master→service instead of relying on the
free-text ``CatalogMaster.specialization`` that no sync path populates.

Two invariants carry most of these tests:

* **Sync owns only the rows it created.** ``MasterService`` is co-owned with
  the operator (MM4 matrix / invite seeding); an operator mapping must survive
  every beat and must never be adopted.
* **Row existence is the contract.** No reader filters a status column, so an
  edge that is inactive or gone upstream must leave no row — a tombstone would
  read as "offered" to booking, slots and the miniapp catalogs.
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
    omit_tenant: bool = False,
    resolved_health_check: bool | None = None,
) -> CatalogSpecialistServiceDTO:
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=edge_id or str(uuid.uuid4()),
        salon_service=str(service.ayla_service_id),
        specialist=str(master.id),
        external_updated_at=_TS,
        tenant=None if omit_tenant else (tenant_id or str(master.tenant_id)),
        user_id=str(uuid.uuid4()),
        name=service.name,
        category_slug="massage",
        is_active=is_active,
        resolved_requires_health_check=resolved_health_check,
        raw={},
    )


def _unresolvable(
    tenant: Tenant, master: CatalogMaster, edge_id: str
) -> CatalogSpecialistServiceDTO:
    """A real upstream edge whose service isn't mirrored locally (yet)."""
    return CatalogSpecialistServiceDTO(
        ayla_specialist_service_id=edge_id,
        salon_service=str(uuid.uuid4()),
        specialist=str(master.id),
        external_updated_at=_TS,
        tenant=str(tenant.id),
    )


class TestCreate:
    def test_creates_edge_stamped_with_ayla_id(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())

        res = upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id)])

        assert isinstance(res, UpsertResult)
        assert (res.created, res.updated, res.skipped, res.removed) == (1, 0, 0, 0)
        row = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)
        assert str(row.ayla_specialist_service_id) == edge_id

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

    def test_null_payload_tenant_is_accepted(self, tenant: Tenant) -> None:
        """Contract marks the denormalized ``tenant`` nullable — guard #1 must
        not reject on absence, only on mismatch."""
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")

        res = upsert_master_services(tenant, [_dto(m, s, omit_tenant=True)])

        assert res.created == 1
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).exists()


class TestInactiveUpstream:
    """Presence is the contract — no reader filters a status column."""

    def test_inactive_edge_creates_no_row(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")

        res = upsert_master_services(tenant, [_dto(m, s, is_active=False)])

        assert res.created == 0
        assert not MasterService.all_tenants.filter(tenant=tenant).exists()

    def test_edge_going_inactive_removes_the_row(self, tenant: Tenant) -> None:
        """The regression that matters: a tombstoned row would still satisfy
        the ``.exists()`` check every booking/slots reader performs."""
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id)])
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).exists()

        res = upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, is_active=False)])

        assert res.removed == 1
        assert not MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).exists()

    def test_inactive_edge_does_not_touch_operator_row(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

        res = upsert_master_services(tenant, [_dto(m, s, is_active=False)])

        assert res.removed == 0
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).exists()

    def test_reoffered_edge_comes_back(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, is_active=False)])

        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, is_active=True)])

        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).exists()


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
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 1
        assert second.removed == third.removed == 0

    def test_noop_beat_does_not_bump_updated_at(self, tenant: Tenant) -> None:
        """``updated_at`` is auto_now and the MM4 matrix derives its
        optimistic-concurrency token from MAX(updated_at). An unconditional
        save would 409 any operator mid-edit, every 15 minutes."""
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        dto = _dto(m, s)
        upsert_master_services(tenant, [dto])
        before = MasterService.all_tenants.get(tenant=tenant).updated_at

        upsert_master_services(tenant, [dto])

        assert MasterService.all_tenants.get(tenant=tenant).updated_at == before


class TestResolvedHealthCheck:
    """DRF-1353 — the mirrored resolved (master×service) screening verdict.

    ``MasterService.resolved_requires_health_check`` is what the booking gate
    reads instead of failing closed for every tenant outside a one-entry
    allowlist. Its tri-state is load-bearing, so the writer has to preserve
    the difference between "Ayla said no" and "we never heard".
    """

    def test_verdict_is_mirrored_on_create(self, tenant: Tenant) -> None:
        m, s = (
            _master(tenant),
            _service(tenant, name="\u0418\u043d\u044a\u0435\u043a\u0446\u0438\u0438"),
        )

        upsert_master_services(tenant, [_dto(m, s, resolved_health_check=True)])

        assert MasterService.all_tenants.get(tenant=tenant).resolved_requires_health_check is True

    def test_absent_verdict_stays_null_on_create(self, tenant: Tenant) -> None:
        """NULL, not False. The gate reads NULL as "screening required"."""
        m, s = _master(tenant), _service(tenant, name="\u041c\u0430\u0441\u0441\u0430\u0436")

        upsert_master_services(tenant, [_dto(m, s, resolved_health_check=None)])

        assert MasterService.all_tenants.get(tenant=tenant).resolved_requires_health_check is None

    def test_verdict_change_is_written(self, tenant: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="\u041c\u0430\u0441\u0441\u0430\u0436")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, resolved_health_check=False)])

        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, resolved_health_check=True)])

        assert MasterService.all_tenants.get(tenant=tenant).resolved_requires_health_check is True

    def test_absent_verdict_never_downgrades_a_known_one(self, tenant: Tenant) -> None:
        """A beat whose payload omits the key says nothing. Writing NULL over
        a known True would flip the gate open the moment upstream hiccups —
        or, over a known False, dead-end a working salon."""
        m, s = _master(tenant), _service(tenant, name="\u041c\u0430\u0441\u0441\u0430\u0436")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, resolved_health_check=True)])

        upsert_master_services(tenant, [_dto(m, s, edge_id=edge_id, resolved_health_check=None)])

        assert MasterService.all_tenants.get(tenant=tenant).resolved_requires_health_check is True

    def test_unchanged_verdict_does_not_bump_updated_at(self, tenant: Tenant) -> None:
        """Same contract as the edge stamp: the MM4 matrix derives its
        optimistic-concurrency token from MAX(updated_at)."""
        m, s = _master(tenant), _service(tenant, name="\u041c\u0430\u0441\u0441\u0430\u0436")
        edge_id = str(uuid.uuid4())
        dto = _dto(m, s, edge_id=edge_id, resolved_health_check=False)
        upsert_master_services(tenant, [dto])
        before = MasterService.all_tenants.get(tenant=tenant).updated_at

        upsert_master_services(tenant, [dto])

        assert MasterService.all_tenants.get(tenant=tenant).updated_at == before

    def test_operator_row_is_still_not_adopted(self, tenant: Tenant) -> None:
        """An operator-owned pair keeps its NULL verdict: sync must not touch
        the row at all, so the gate keeps treating it as unproven."""
        m, s = _master(tenant), _service(tenant, name="\u041c\u0430\u0441\u0441\u0430\u0436")
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

        upsert_master_services(tenant, [_dto(m, s, resolved_health_check=False)])

        row = MasterService.all_tenants.get(tenant=tenant)
        assert row.ayla_specialist_service_id is None
        assert row.resolved_requires_health_check is None


class TestReconciliation:
    def test_removed_edge_is_deleted(self, tenant: Tenant) -> None:
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")
        keep, drop = _dto(m, s1), _dto(m, s2)
        upsert_master_services(tenant, [keep, drop])

        res = upsert_master_services(tenant, [keep])

        assert res.removed == 1
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 1
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s1).exists()
        assert not MasterService.all_tenants.filter(tenant=tenant, master=m, service=s2).exists()

    def test_operator_row_is_never_reconciled(self, tenant: Tenant) -> None:
        """The core co-ownership guarantee: NULL stamp ⇒ sync must not touch it."""
        m = _master(tenant)
        s_op = _service(tenant, name="Ручная услуга оператора", slug="manual")
        s_ayla = _service(tenant, name="Спортивный массаж", slug="sport")
        operator_row = MasterService.all_tenants.create(tenant=tenant, master=m, service=s_op)

        res = upsert_master_services(tenant, [_dto(m, s_ayla)])

        assert res.removed == 0
        operator_row.refresh_from_db()
        assert operator_row.ayla_specialist_service_id is None

    def test_skipped_edge_survives_reconciliation(self, tenant: Tenant) -> None:
        """A previously-mirrored edge that fails to resolve this beat is NOT
        absent upstream — deleting it would destroy a live relation."""
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")
        stays, fragile = _dto(m, s1), _dto(m, s2)
        upsert_master_services(tenant, [stays, fragile])

        # Same edge id, but its service is no longer resolvable locally.
        broken = _unresolvable(tenant, m, fragile.ayla_specialist_service_id)
        res = upsert_master_services(tenant, [stays, broken])

        assert res.skipped == 1
        assert res.removed == 0
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s2).exists()

    def test_mass_skip_does_not_wipe_the_tenant(self, tenant: Tenant) -> None:
        """The scale version: most edges failing to resolve must not delete them."""
        m = _master(tenant)
        services = [_service(tenant, name=f"Услуга {i}", slug=f"s{i}") for i in range(5)]
        dtos = [_dto(m, svc) for svc in services]
        upsert_master_services(tenant, dtos)
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 5

        # Only the first still resolves; the other four go unresolvable.
        degraded = [dtos[0]] + [
            _unresolvable(tenant, m, d.ayla_specialist_service_id) for d in dtos[1:]
        ]
        res = upsert_master_services(tenant, degraded)

        assert res.skipped == 4
        assert res.removed == 0
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 5

    def test_empty_snapshot_vetoes_reconciliation(self, tenant: Tenant) -> None:
        """An empty feed is far likelier a tenant-id mismatch than a real wipe."""
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        upsert_master_services(tenant, [_dto(m, s)])

        res = upsert_master_services(tenant, [])

        assert res.removed == 0
        assert MasterService.all_tenants.filter(tenant=tenant).exists()

    def test_partial_batch_vetoes_reconciliation(self, tenant: Tenant) -> None:
        """Requirement 8 — a partial failure must not leave knowingly-false state."""
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")
        upsert_master_services(tenant, [_dto(m, s1), _dto(m, s2)])

        broken = CatalogSpecialistServiceDTO(
            ayla_specialist_service_id=str(uuid.uuid4()),
            salon_service="not-a-uuid",
            specialist=str(m.id),
            external_updated_at=_TS,
            tenant=str(tenant.id),
        )
        res = upsert_master_services(tenant, [_dto(m, s1), broken])

        assert res.errors
        assert res.removed == 0
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 2

    def test_reconcile_disabled_by_caller(self, tenant: Tenant) -> None:
        m = _master(tenant)
        s1 = _service(tenant, name="Спортивный массаж", slug="sport")
        s2 = _service(tenant, name="Классический массаж", slug="classic")
        upsert_master_services(tenant, [_dto(m, s1), _dto(m, s2)])

        res = upsert_master_services(tenant, [_dto(m, s1)], reconcile=False)

        assert res.removed == 0
        assert MasterService.all_tenants.filter(tenant=tenant).count() == 2

    def test_reconciliation_is_tenant_scoped(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """Tenant A's beat must not reach tenant B's owned rows."""
        m_a, s_a = _master(tenant), _service(tenant, name="Спортивный массаж")
        m_b, s_b = _master(tenant_b), _service(tenant_b, name="Спортивный массаж")
        upsert_master_services(tenant, [_dto(m_a, s_a)])
        upsert_master_services(tenant_b, [_dto(m_b, s_b)])

        # Tenant A's next beat drops its only edge.
        upsert_master_services(tenant, [_dto(m_a, _service(tenant, name="Другое", slug="other"))])

        assert MasterService.all_tenants.filter(tenant=tenant_b).count() == 1


class TestOwnership:
    def test_operator_pair_is_not_adopted(self, tenant: Tenant) -> None:
        """Adoption would hand an operator-authored row to reconciliation."""
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

        res = upsert_master_services(tenant, [_dto(m, s)])

        assert (res.created, res.skipped) == (0, 1)
        assert MasterService.all_tenants.filter(tenant=tenant, master=m, service=s).count() == 1
        row = MasterService.all_tenants.get(tenant=tenant, master=m, service=s)
        assert row.ayla_specialist_service_id is None

    def test_reparented_edge_removes_the_stale_row(self, tenant: Tenant) -> None:
        """Same Ayla edge id moved to another pair.

        The old row is provably sync-owned (it carries our stamp), so it is
        deleted. Merely releasing the stamp would leave a NULL-stamped row
        indistinguishable from an operator row — permanently un-reconcilable,
        and a live false claim that the master performs that service.
        """
        m1, m2 = _master(tenant, name="Анна"), _master(tenant, name="Борис")
        s = _service(tenant, name="Спортивный массаж")
        edge_id = str(uuid.uuid4())
        upsert_master_services(tenant, [_dto(m1, s, edge_id=edge_id)])

        res = upsert_master_services(tenant, [_dto(m2, s, edge_id=edge_id)])

        assert not res.errors
        assert res.created == 1
        # A move is not a destruction: it must not inflate ``removed``, which
        # is the destructive-action signal the beat log surfaces.
        assert (res.reparented, res.removed) == (1, 0)
        assert MasterService.all_tenants.filter(tenant=tenant, master=m2, service=s).exists()
        assert not MasterService.all_tenants.filter(tenant=tenant, master=m1, service=s).exists()


class TestSafety:
    def test_foreign_tenant_payload_is_skipped(self, tenant: Tenant, tenant_b: Tenant) -> None:
        m, s = _master(tenant), _service(tenant, name="Спортивный массаж")

        res = upsert_master_services(tenant, [_dto(m, s, tenant_id=str(tenant_b.id))])

        assert (res.created, res.skipped) == (0, 1)
        assert not MasterService.all_tenants.filter(tenant=tenant).exists()

    def test_cross_tenant_edge_cannot_be_created(self, tenant: Tenant, tenant_b: Tenant) -> None:
        """Master here, service in another tenant."""
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
            specialist=str(uuid.uuid4()),
            external_updated_at=_TS,
            tenant=str(tenant.id),
        )

        res = upsert_master_services(tenant, [dto])

        assert (res.created, res.skipped) == (0, 1)
        assert not res.errors
        assert not MasterService.all_tenants.filter(tenant=tenant).exists()

    def test_unknown_service_is_skipped(self, tenant: Tenant) -> None:
        m = _master(tenant)

        res = upsert_master_services(tenant, [_unresolvable(tenant, m, str(uuid.uuid4()))])

        assert (res.created, res.skipped) == (0, 1)
        assert not res.errors


class TestPilotShape:
    """Targeted fixture for the formula-tela-like case behind the live failure."""

    def test_massage_specialist_gets_both_services_linked(self, tenant: Tenant) -> None:
        # specialization deliberately empty — this is exactly the production
        # shape: nothing populates it, so discovery must not depend on it.
        master = _master(tenant, name="Массажист Пилот", specialization="")
        sport = _service(tenant, name="Спортивный массаж", slug="sport-massage")
        classic = _service(tenant, name="Классический массаж", slug="classic-massage")

        res = upsert_master_services(tenant, [_dto(master, sport), _dto(master, classic)])

        assert res.created == 2
        linked = set(
            MasterService.all_tenants.filter(tenant=tenant, master=master).values_list(
                "service__name", flat=True
            )
        )
        assert linked == {"Спортивный массаж", "Классический массаж"}
        assert master.specialization == ""

    def test_id_contract_holds_end_to_end(self, tenant: Tenant) -> None:
        """Ties the master mirror's output ids to this mirror's input ids.

        The other tests mint their own master and hand its id straight back,
        which proves nothing about the upstream contract. Here the master is
        created by ``upsert_specialists`` from a specialists-feed DTO, and the
        edge references it exactly the way Ayla does — by SpecialistProfile.id.
        """
        from apps.catalog.services.http_client import CatalogSpecialistDTO
        from apps.catalog.services.upserter import upsert_specialists

        specialist_id = str(uuid.uuid4())
        upsert_specialists(
            tenant,
            [
                CatalogSpecialistDTO(
                    ayla_master_id=specialist_id,
                    user_id=str(uuid.uuid4()),  # deliberately different — not the join key
                    name="Массажист Пилот",
                    external_updated_at=_TS,
                )
            ],
        )
        service = _service(tenant, name="Спортивный массаж", slug="sport-massage")

        res = upsert_master_services(
            tenant,
            [
                CatalogSpecialistServiceDTO(
                    ayla_specialist_service_id=str(uuid.uuid4()),
                    salon_service=str(service.ayla_service_id),
                    specialist=specialist_id,
                    external_updated_at=_TS,
                    tenant=str(tenant.id),
                )
            ],
        )

        assert res.created == 1
        assert MasterService.all_tenants.filter(
            tenant=tenant, master_id=uuid.UUID(specialist_id), service=service
        ).exists()
