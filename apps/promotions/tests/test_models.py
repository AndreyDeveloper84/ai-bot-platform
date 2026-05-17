"""Promotion model tests (DRF-842 / Phase 1 / B6).

Covers:

* code normalisation to upper-case on save
* TenantScopedManager filters by current_tenant (cross-tenant leak guard)
* unique_together(tenant, code) — case-insensitive by virtue of normalisation
* validator bounds on discount_percent (1..99)
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError

from apps.promotions.models import Promotion
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant_a(db) -> Tenant:
    return Tenant.objects.create(slug="promo-a", name="Salon A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="promo-b", name="Salon B")


class TestPromotionSave:
    def test_code_uppercased_on_save(self, tenant_a: Tenant) -> None:
        promo = Promotion.all_tenants.create(
            tenant=tenant_a,
            code="may10",
            discount_percent=10,
        )
        promo.refresh_from_db()
        assert promo.code == "MAY10"

    def test_code_stripped_on_save(self, tenant_a: Tenant) -> None:
        promo = Promotion.all_tenants.create(
            tenant=tenant_a,
            code="  spring  ",
            discount_percent=15,
        )
        promo.refresh_from_db()
        assert promo.code == "SPRING"

    def test_defaults(self, tenant_a: Tenant) -> None:
        promo = Promotion.all_tenants.create(
            tenant=tenant_a,
            code="FOO",
            discount_percent=5,
        )
        assert promo.is_active is True
        assert promo.applicable_service_ids == []
        assert promo.notes == ""
        assert promo.valid_from is None
        assert promo.valid_to is None


class TestPromotionUniqueness:
    def test_unique_together_tenant_code(self, tenant_a: Tenant) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MAY10",
            discount_percent=10,
        )
        with pytest.raises(IntegrityError):
            Promotion.all_tenants.create(
                tenant=tenant_a,
                code="MAY10",
                discount_percent=15,
            )

    def test_unique_is_case_insensitive_via_normalisation(self, tenant_a: Tenant) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MAY10",
            discount_percent=10,
        )
        # Lower-case input — gets upper-cased on save → collides with the
        # existing MAY10 row.
        with pytest.raises(IntegrityError):
            Promotion.all_tenants.create(
                tenant=tenant_a,
                code="may10",
                discount_percent=20,
            )

    def test_same_code_different_tenants_allowed(self, tenant_a: Tenant, tenant_b: Tenant) -> None:
        Promotion.all_tenants.create(tenant=tenant_a, code="MAY10", discount_percent=10)
        # Same code on a different tenant is fine — promos are scoped.
        Promotion.all_tenants.create(tenant=tenant_b, code="MAY10", discount_percent=20)
        assert Promotion.all_tenants.count() == 2


class TestPromotionTenantScope:
    def test_default_manager_filters_by_current_tenant(
        self, tenant_a: Tenant, tenant_b: Tenant
    ) -> None:
        Promotion.all_tenants.create(tenant=tenant_a, code="A", discount_percent=5)
        Promotion.all_tenants.create(tenant=tenant_b, code="B", discount_percent=5)

        with tenant_scope(tenant_a):
            assert Promotion.objects.count() == 1
            assert Promotion.objects.get().code == "A"

        with tenant_scope(tenant_b):
            assert Promotion.objects.count() == 1
            assert Promotion.objects.get().code == "B"

        # all_tenants escape hatch sees both.
        assert Promotion.all_tenants.count() == 2


class TestPromotionValidators:
    def test_discount_percent_too_low_invalid(self, tenant_a: Tenant) -> None:
        promo = Promotion(tenant=tenant_a, code="ZERO", discount_percent=0)
        with pytest.raises(ValidationError):
            promo.full_clean()

    def test_discount_percent_too_high_invalid(self, tenant_a: Tenant) -> None:
        promo = Promotion(tenant=tenant_a, code="HUNDRED", discount_percent=100)
        with pytest.raises(ValidationError):
            promo.full_clean()

    def test_discount_percent_in_range_valid(self, tenant_a: Tenant) -> None:
        promo = Promotion(tenant=tenant_a, code="OK", discount_percent=50)
        promo.full_clean()  # should not raise


class TestPromotionStr:
    def test_str_includes_code_and_percent(self, tenant_a: Tenant) -> None:
        promo = Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MAY10",
            discount_percent=10,
        )
        s = str(promo)
        assert "MAY10" in s
        assert "10" in s
