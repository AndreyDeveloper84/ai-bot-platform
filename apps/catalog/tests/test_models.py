"""Catalog mirror model tests (DRF-572 / Sprint 7 / C1)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from django.db.utils import IntegrityError

from apps.catalog.models import (
    CatalogFaq,
    CatalogHelpArticle,
    CatalogMaster,
    CatalogService,
    MasterService,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cat-test", name="Catalog Test")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="cat-test-b", name="Catalog Test B")


def _ts() -> datetime:
    return datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)


class TestCatalogService:
    def test_create_minimal(self, tenant: Tenant) -> None:
        svc = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=42,
            external_updated_at=_ts(),
            slug="massazh-spiny",
            name="Массаж спины",
        )
        assert svc.is_active is True
        assert svc.goals == []
        assert svc.requires_health_check is False

    def test_unique_external_id_per_tenant(self, tenant: Tenant) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=_ts(),
            slug="a",
            name="A",
        )
        with pytest.raises(IntegrityError):
            CatalogService.all_tenants.create(
                tenant=tenant,
                external_id=1,
                external_updated_at=_ts(),
                slug="dup",
                name="Dup",
            )

    def test_same_external_id_different_tenants_ok(self, tenant: Tenant, tenant_b: Tenant) -> None:
        for owner in (tenant, tenant_b):
            CatalogService.all_tenants.create(
                tenant=owner,
                external_id=1,
                external_updated_at=_ts(),
                slug="a",
                name="A",
            )
        assert CatalogService.all_tenants.filter(external_id=1).count() == 2


class TestCatalogServiceAylaRekey:
    """S3B PR-1: CatalogService re-keyed onto the Ayla stable-id.

    Ayla ids are UUIDs — there is no integer upstream id — so ``external_id``
    is now nullable and Ayla-fed rows key on ``(tenant, ayla_service_id)`` via
    a partial ``UniqueConstraint`` (``WHERE ayla_service_id IS NOT NULL``).
    The legacy ``unique_together (tenant, external_id)`` stays for legacy rows
    (NULL-safe: Postgres treats NULLs as distinct).
    """

    def test_external_id_nullable(self, tenant: Tenant) -> None:
        import uuid

        svc = CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=_ts(),
            ayla_service_id=uuid.uuid4(),
            slug="a",
            name="A",
        )
        assert svc.external_id is None

    def test_ayla_service_id_unique_per_tenant(self, tenant: Tenant) -> None:
        import uuid

        aid = uuid.uuid4()
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_updated_at=_ts(),
            ayla_service_id=aid,
            slug="a",
            name="A",
        )
        with pytest.raises(IntegrityError):
            CatalogService.all_tenants.create(
                tenant=tenant,
                external_updated_at=_ts(),
                ayla_service_id=aid,
                slug="dup",
                name="Dup",
            )

    def test_null_ayla_service_id_not_constrained(self, tenant: Tenant) -> None:
        # Two rows with a NULL ayla_service_id coexist — the partial
        # constraint only bites when ayla_service_id IS NOT NULL.
        CatalogService.all_tenants.create(
            tenant=tenant, external_id=1, external_updated_at=_ts(), slug="a", name="A"
        )
        CatalogService.all_tenants.create(
            tenant=tenant, external_id=2, external_updated_at=_ts(), slug="b", name="B"
        )
        assert (
            CatalogService.all_tenants.filter(tenant=tenant, ayla_service_id__isnull=True).count()
            == 2
        )

    def test_same_ayla_service_id_different_tenants_ok(
        self, tenant: Tenant, tenant_b: Tenant
    ) -> None:
        import uuid

        aid = uuid.uuid4()
        for owner in (tenant, tenant_b):
            CatalogService.all_tenants.create(
                tenant=owner,
                external_updated_at=_ts(),
                ayla_service_id=aid,
                slug="a",
                name="A",
            )
        assert CatalogService.all_tenants.filter(ayla_service_id=aid).count() == 2


class TestCatalogMaster:
    def test_yclients_staff_id_optional(self, tenant: Tenant) -> None:
        m = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=10,
            external_updated_at=_ts(),
            name="Анна",
        )
        assert m.yclients_staff_id is None

    def test_str_representation(self, tenant: Tenant) -> None:
        m = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=10,
            external_updated_at=_ts(),
            name="Анна",
        )
        assert "Анна" in str(m)


class TestCatalogMasterAylaUserId:
    """W2 handoff: ``CatalogMaster.ayla_user_id`` bridge field.

    Pattern mirrors #595/#634/#635. Bridges event-payload ``master_user_id``
    (Ayla UUID) → local ``CatalogMaster`` ORM row so the W2 ``payment_failed``
    skill can resolve the master without an extra REST round-trip.
    """

    def test_ayla_user_id_default_none(self, tenant: Tenant) -> None:
        m = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=11,
            external_updated_at=_ts(),
            name="Без Ayla",
        )
        assert m.ayla_user_id is None

    def test_ayla_user_id_accepts_uuid(self, tenant: Tenant) -> None:
        import uuid

        ayla_uuid = uuid.uuid4()
        m = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=12,
            external_updated_at=_ts(),
            name="С Ayla",
            ayla_user_id=ayla_uuid,
        )
        m.refresh_from_db()
        assert m.ayla_user_id == ayla_uuid

    def test_ayla_user_id_indexed(self) -> None:
        field = CatalogMaster._meta.get_field("ayla_user_id")
        # ``django-stubs`` types ``_meta.get_field`` return as the field
        # subclass (``UUIDField``), which lacks ``db_index`` in the stub
        # generic args — use ``getattr`` so mypy stays happy without
        # casting to the private base.
        assert getattr(field, "db_index") is True
        assert getattr(field, "null") is True
        assert getattr(field, "blank") is True

    def test_ayla_user_id_lookup_by_value(self, tenant: Tenant) -> None:
        import uuid

        target = uuid.uuid4()
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=13,
            external_updated_at=_ts(),
            name="Lookup Target",
            ayla_user_id=target,
        )
        # A second row with a different UUID so the filter is meaningful.
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=14,
            external_updated_at=_ts(),
            name="Other",
            ayla_user_id=uuid.uuid4(),
        )
        # And one with no Ayla id at all.
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=15,
            external_updated_at=_ts(),
            name="No Ayla",
        )
        match = CatalogMaster.all_tenants.get(ayla_user_id=target)
        assert match.external_id == 13


class TestCatalogMasterReviewCount:
    """#1060 — ``CatalogMaster.review_count`` for the discovery Bayesian
    trust-score. Mirrors Ayla's ``reviews_count`` so a 5.0 from 1 review
    can't outrank a 4.8 from 200. Defaults to 0 until sync is retargeted
    to Ayla (#1044).
    """

    def test_review_count_default_zero(self, tenant: Tenant) -> None:
        m = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=20,
            external_updated_at=_ts(),
            name="Без отзывов",
        )
        assert m.review_count == 0

    def test_review_count_persists(self, tenant: Tenant) -> None:
        m = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=21,
            external_updated_at=_ts(),
            name="С отзывами",
            review_count=200,
        )
        m.refresh_from_db()
        assert m.review_count == 200


class TestCatalogMasterAylaRekey:
    """S3B PR-2: CatalogMaster keyed on the Ayla stable-id (ayla_user_id =
    User.id). Partial UniqueConstraint (WHERE ayla_user_id IS NOT NULL) so
    Ayla-fed masters can't duplicate per tenant; legacy NULL rows exempt.
    """

    def test_ayla_user_id_unique_per_tenant(self, tenant: Tenant) -> None:
        import uuid

        uid = uuid.uuid4()
        CatalogMaster.all_tenants.create(
            tenant=tenant, external_updated_at=_ts(), name="A", ayla_user_id=uid
        )
        with pytest.raises(IntegrityError):
            CatalogMaster.all_tenants.create(
                tenant=tenant, external_updated_at=_ts(), name="Dup", ayla_user_id=uid
            )

    def test_null_ayla_user_id_not_constrained(self, tenant: Tenant) -> None:
        CatalogMaster.all_tenants.create(
            tenant=tenant, external_id=1, external_updated_at=_ts(), name="A"
        )
        CatalogMaster.all_tenants.create(
            tenant=tenant, external_id=2, external_updated_at=_ts(), name="B"
        )
        assert (
            CatalogMaster.all_tenants.filter(tenant=tenant, ayla_user_id__isnull=True).count() == 2
        )


class TestMasterServiceBookable:
    """S3B PR-2: MasterService (the master×service edge) carries the Ayla
    bookable identity + resolved fields from ``specialist-services``.
    """

    def _master(self, tenant: Tenant, **kw) -> CatalogMaster:
        import uuid

        kw.setdefault("ayla_user_id", uuid.uuid4())
        return CatalogMaster.all_tenants.create(
            tenant=tenant, external_updated_at=_ts(), name="M", **kw
        )

    def _service(self, tenant: Tenant, **kw) -> CatalogService:
        import uuid

        kw.setdefault("ayla_service_id", uuid.uuid4())
        return CatalogService.all_tenants.create(
            tenant=tenant, external_updated_at=_ts(), slug="s", name="S", **kw
        )

    def test_bookable_fields_persist(self, tenant: Tenant) -> None:
        import uuid
        from decimal import Decimal

        bookable = uuid.uuid4()
        ms = MasterService.all_tenants.create(
            tenant=tenant,
            master=self._master(tenant),
            service=self._service(tenant),
            ayla_specialist_service_id=bookable,
            resolved_duration=45,
            resolved_requires_health_check=True,
            price=Decimal("1500.00"),
            is_active=False,
        )
        ms.refresh_from_db()
        assert ms.ayla_specialist_service_id == bookable
        assert ms.resolved_duration == 45
        assert ms.resolved_requires_health_check is True
        assert ms.price == Decimal("1500.00")
        assert ms.is_active is False

    def test_bookable_defaults(self, tenant: Tenant) -> None:
        ms = MasterService.all_tenants.create(
            tenant=tenant, master=self._master(tenant), service=self._service(tenant)
        )
        assert ms.ayla_specialist_service_id is None
        assert ms.resolved_duration is None
        assert ms.resolved_requires_health_check is False
        assert ms.price is None
        assert ms.is_active is True

    def test_bookable_id_unique_per_tenant(self, tenant: Tenant) -> None:
        import uuid

        bookable = uuid.uuid4()
        MasterService.all_tenants.create(
            tenant=tenant,
            master=self._master(tenant),
            service=self._service(tenant),
            ayla_specialist_service_id=bookable,
        )
        with pytest.raises(IntegrityError):
            # A different (master, service) pair but the same bookable id —
            # the partial UniqueConstraint must reject it.
            MasterService.all_tenants.create(
                tenant=tenant,
                master=self._master(tenant),
                service=self._service(tenant),
                ayla_specialist_service_id=bookable,
            )


class TestCatalogFaq:
    def test_unique_per_tenant(self, tenant: Tenant) -> None:
        CatalogFaq.all_tenants.create(
            tenant=tenant,
            external_id=5,
            external_updated_at=_ts(),
            question="Q",
            answer="A",
        )
        with pytest.raises(IntegrityError):
            CatalogFaq.all_tenants.create(
                tenant=tenant,
                external_id=5,
                external_updated_at=_ts(),
                question="Q2",
                answer="A2",
            )


class TestCatalogHelpArticle:
    def test_ordering(self, tenant: Tenant) -> None:
        for idx, order in enumerate([3, 1, 2]):
            CatalogHelpArticle.all_tenants.create(
                tenant=tenant,
                external_id=100 + idx,
                external_updated_at=_ts(),
                question=f"Q{idx}",
                answer=f"A{idx}",
                order=order,
            )
        with tenant_scope(tenant):
            orders = list(CatalogHelpArticle.objects.values_list("order", flat=True))
        assert orders == [1, 2, 3]


class TestSyncedAt:
    def test_auto_set_on_create(self, tenant: Tenant) -> None:
        svc = CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=_ts(),
            slug="a",
            name="A",
        )
        assert svc.synced_at is not None

    def test_updates_on_save(self, tenant: Tenant) -> None:
        from freezegun import freeze_time

        with freeze_time("2026-05-13 10:00:00"):
            svc = CatalogService.all_tenants.create(
                tenant=tenant,
                external_id=1,
                external_updated_at=_ts(),
                slug="a",
                name="A",
            )
            original_synced = svc.synced_at

        with freeze_time("2026-05-13 10:05:00"):
            svc.name = "B"
            svc.save()
            svc.refresh_from_db()

        assert svc.synced_at > original_synced


class TestTenantScoping:
    def test_default_manager_filters_by_tenant(self, tenant: Tenant, tenant_b: Tenant) -> None:
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=1,
            external_updated_at=_ts(),
            slug="a",
            name="A",
        )
        CatalogService.all_tenants.create(
            tenant=tenant_b,
            external_id=2,
            external_updated_at=_ts(),
            slug="b",
            name="B",
        )
        with tenant_scope(tenant):
            visible = list(CatalogService.objects.all())
        assert len(visible) == 1
        assert visible[0].slug == "a"
