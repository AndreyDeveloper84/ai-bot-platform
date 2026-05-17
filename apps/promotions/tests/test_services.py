"""validate_promo service tests (DRF-842 / Phase 1 / B6).

Covers the full 7-value reason taxonomy + cross-tenant isolation +
case-insensitive matching + empty applicable_service_ids = all
services.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.promotions.models import Promotion
from apps.promotions.services import validate_promo
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant_a(db) -> Tenant:
    return Tenant.objects.create(slug="svc-a", name="Salon A")


@pytest.fixture
def tenant_b(db) -> Tenant:
    return Tenant.objects.create(slug="svc-b", name="Salon B")


@pytest.fixture
def service_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def other_service_id() -> uuid.UUID:
    return uuid.uuid4()


class TestValidatePromoHappyPath:
    def test_ok_with_active_unbounded_promo(self, tenant_a: Tenant, service_id: uuid.UUID) -> None:
        promo = Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MAY10",
            discount_percent=10,
        )
        result = validate_promo(tenant=tenant_a, code="MAY10", service_id=service_id)
        assert result.ok is True
        assert result.reason == "ok"
        assert result.promo is not None
        assert result.promo.pk == promo.pk

    def test_ok_case_insensitive_lowercase_input(
        self, tenant_a: Tenant, service_id: uuid.UUID
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MAY10",
            discount_percent=10,
        )
        result = validate_promo(tenant=tenant_a, code="may10", service_id=service_id)
        assert result.ok is True
        assert result.reason == "ok"

    def test_ok_empty_applicable_service_ids_applies_to_all(
        self, tenant_a: Tenant, service_id: uuid.UUID
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="ALL",
            discount_percent=10,
            applicable_service_ids=[],
        )
        result = validate_promo(tenant=tenant_a, code="ALL", service_id=service_id)
        assert result.ok is True

    def test_ok_when_service_in_applicable_list(
        self, tenant_a: Tenant, service_id: uuid.UUID
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MASSAGE",
            discount_percent=10,
            applicable_service_ids=[str(service_id)],
        )
        result = validate_promo(tenant=tenant_a, code="MASSAGE", service_id=service_id)
        assert result.ok is True


class TestValidatePromoNegativeReasons:
    def test_not_found_when_code_absent(self, tenant_a: Tenant, service_id: uuid.UUID) -> None:
        result = validate_promo(tenant=tenant_a, code="NOPE", service_id=service_id)
        assert result.ok is False
        assert result.reason == "not_found"
        assert result.promo is None

    def test_not_found_when_empty_code(self, tenant_a: Tenant, service_id: uuid.UUID) -> None:
        result = validate_promo(tenant=tenant_a, code="", service_id=service_id)
        assert result.ok is False
        assert result.reason == "not_found"

    def test_inactive(self, tenant_a: Tenant, service_id: uuid.UUID) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="DEAD",
            discount_percent=10,
            is_active=False,
        )
        result = validate_promo(tenant=tenant_a, code="DEAD", service_id=service_id)
        assert result.ok is False
        assert result.reason == "inactive"

    def test_not_started(self, tenant_a: Tenant, service_id: uuid.UUID) -> None:
        future = timezone.now() + timedelta(days=7)
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="FUTURE",
            discount_percent=10,
            valid_from=future,
        )
        result = validate_promo(tenant=tenant_a, code="FUTURE", service_id=service_id)
        assert result.ok is False
        assert result.reason == "not_started"

    def test_expired(self, tenant_a: Tenant, service_id: uuid.UUID) -> None:
        past = timezone.now() - timedelta(days=7)
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="PAST",
            discount_percent=10,
            valid_to=past,
        )
        result = validate_promo(tenant=tenant_a, code="PAST", service_id=service_id)
        assert result.ok is False
        assert result.reason == "expired"

    def test_wrong_service(
        self,
        tenant_a: Tenant,
        service_id: uuid.UUID,
        other_service_id: uuid.UUID,
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MASSAGEONLY",
            discount_percent=10,
            applicable_service_ids=[str(other_service_id)],
        )
        result = validate_promo(
            tenant=tenant_a,
            code="MASSAGEONLY",
            service_id=service_id,
        )
        assert result.ok is False
        assert result.reason == "wrong_service"


class TestValidatePromoTenantIsolation:
    def test_promo_on_tenant_a_invisible_to_tenant_b(
        self, tenant_a: Tenant, tenant_b: Tenant, service_id: uuid.UUID
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="MAY10",
            discount_percent=10,
        )
        # Tenant B asking about MAY10 sees not_found — the row exists
        # but belongs to tenant A.
        result = validate_promo(tenant=tenant_b, code="MAY10", service_id=service_id)
        assert result.ok is False
        assert result.reason == "not_found"


class TestValidatePromoClockOverride:
    def test_now_override_allows_simulating_future(
        self, tenant_a: Tenant, service_id: uuid.UUID
    ) -> None:
        # Promo only valid from tomorrow.
        future = timezone.now() + timedelta(days=1)
        Promotion.all_tenants.create(
            tenant=tenant_a,
            code="LATER",
            discount_percent=10,
            valid_from=future,
        )
        # Passing a clock 2 days ahead → promo is active.
        future_clock = future + timedelta(days=1)
        result = validate_promo(
            tenant=tenant_a,
            code="LATER",
            service_id=service_id,
            now=future_clock,
        )
        assert result.ok is True
