"""ThresholdConfig model tests (DRF-478 / Sprint 4 / A2)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.db.utils import IntegrityError

from apps.promptreg.models import ThresholdConfig
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="tc-a", name="TC-A")


class TestThresholdConfig:
    def test_create_defaults(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            tc = ThresholdConfig.objects.create(
                tenant=tenant, key="intent_confidence_min", value=Decimal("0.65")
            )
        tc.refresh_from_db()
        assert tc.is_active is True  # default True for ThresholdConfig
        assert tc.applied_to == ""
        assert tc.version == 1
        assert tc.value == Decimal("0.65")

    def test_decimal_precision_preserved(self, tenant, settings):
        """4 decimal places preserved end-to-end."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            tc = ThresholdConfig.objects.create(
                tenant=tenant, key="precise", value=Decimal("0.1234")
            )
        tc.refresh_from_db()
        assert tc.value == Decimal("0.1234")

    def test_unique_together(self, tenant, settings):
        """Same (tenant, key, applied_to, version) → IntegrityError."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            ThresholdConfig.objects.create(
                tenant=tenant, key="k", applied_to="faq", version=1, value="1.0"
            )
            with pytest.raises(IntegrityError):
                ThresholdConfig.objects.create(
                    tenant=tenant, key="k", applied_to="faq", version=1, value="2.0"
                )

    def test_applied_to_scoping(self, tenant, settings):
        """Same key, different applied_to coexist."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            ThresholdConfig.objects.create(
                tenant=tenant, key="threshold", applied_to="", value="0.5"
            )
            ThresholdConfig.objects.create(
                tenant=tenant, key="threshold", applied_to="faq", value="0.7"
            )
            ThresholdConfig.objects.create(
                tenant=tenant, key="threshold", applied_to="handoff", value="0.8"
            )
            assert ThresholdConfig.objects.filter(key="threshold").count() == 3

    def test_tenant_scoping(self, settings):
        a = Tenant.objects.create(slug="tc-x", name="X")
        b = Tenant.objects.create(slug="tc-y", name="Y")

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(a):
            ThresholdConfig.objects.create(tenant=a, key="k", value="1.0")
        with tenant_scope(b):
            ThresholdConfig.objects.create(tenant=b, key="k", value="2.0")

        with tenant_scope(a):
            assert ThresholdConfig.objects.count() == 1
            assert ThresholdConfig.objects.first().value == Decimal("1.0")
        with tenant_scope(b):
            assert ThresholdConfig.objects.count() == 1
            assert ThresholdConfig.objects.first().value == Decimal("2.0")
