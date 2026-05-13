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
