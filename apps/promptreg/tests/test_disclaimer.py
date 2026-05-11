"""DisclaimerLibrary tests (DRF-479 / Sprint 4 / A3)."""

from __future__ import annotations

import pytest
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.promptreg.models import DisclaimerLibrary
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="dl-a", name="DL-A")


class TestDisclaimerLibrary:
    def test_create_defaults(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            d = DisclaimerLibrary.objects.create(
                tenant=tenant,
                category=DisclaimerLibrary.Category.MEDICAL,
                risk_level=DisclaimerLibrary.RiskLevel.HIGH,
                text="Consult a doctor before any procedure.",
            )
        d.refresh_from_db()
        assert d.is_active is True
        assert d.version == 1
        assert d.withdrawn_at is None

    def test_choices_present(self):
        cats = {c[0] for c in DisclaimerLibrary.Category.choices}
        assert cats == {"medical", "pricing", "aftercare", "booking", "general"}
        risks = {r[0] for r in DisclaimerLibrary.RiskLevel.choices}
        assert risks == {"low", "medium", "high"}

    def test_unique_together(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            DisclaimerLibrary.objects.create(
                tenant=tenant,
                category="medical",
                risk_level="high",
                text="t1",
                version=1,
            )
            with pytest.raises(IntegrityError):
                DisclaimerLibrary.objects.create(
                    tenant=tenant,
                    category="medical",
                    risk_level="high",
                    text="t2",
                    version=1,
                )

    def test_withdrawn_at_field(self, tenant, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            d = DisclaimerLibrary.objects.create(
                tenant=tenant,
                category="general",
                risk_level="low",
                text="t",
            )
            d.withdrawn_at = timezone.now()
            d.save(update_fields=["withdrawn_at"])
            d.refresh_from_db()
        assert d.withdrawn_at is not None

    def test_tenant_scoping(self, settings):
        a = Tenant.objects.create(slug="dl-x", name="X")
        b = Tenant.objects.create(slug="dl-y", name="Y")

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(a):
            DisclaimerLibrary.objects.create(
                tenant=a, category="pricing", risk_level="low", text="A"
            )
        with tenant_scope(b):
            DisclaimerLibrary.objects.create(
                tenant=b, category="pricing", risk_level="low", text="B"
            )

        with tenant_scope(a):
            assert DisclaimerLibrary.objects.count() == 1
            assert DisclaimerLibrary.objects.first().text == "A"
        with tenant_scope(b):
            assert DisclaimerLibrary.objects.count() == 1
            assert DisclaimerLibrary.objects.first().text == "B"
