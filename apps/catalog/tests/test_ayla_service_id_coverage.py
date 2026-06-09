"""Tests for the ``ayla_service_id_coverage`` management command (#1016 / PR-B)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from io import StringIO

import pytest
from django.core.management import call_command

from apps.catalog.models import CatalogService
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="cov-test", name="Coverage Test")


def _ts() -> datetime:
    return datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)


def _svc(tenant: Tenant, *, external_id: int, ayla_service_id, is_active: bool = True) -> None:
    CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=_ts(),
        slug=f"svc-{external_id}",
        name=f"Service {external_id}",
        is_active=is_active,
        ayla_service_id=ayla_service_id,
    )


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("ayla_service_id_coverage", stdout=out, **kwargs)
    return out.getvalue()


@pytest.mark.django_db
def test_coverage_counts_grounded_active_rows(tenant: Tenant) -> None:
    _svc(tenant, external_id=1, ayla_service_id=uuid.uuid4())
    _svc(tenant, external_id=2, ayla_service_id=uuid.uuid4())
    _svc(tenant, external_id=3, ayla_service_id=None)
    output = _run()
    # 2 of 3 active rows grounded → 66.7%.
    assert "66.7%" in output
    assert "TOTAL" in output


@pytest.mark.django_db
def test_inactive_rows_excluded(tenant: Tenant) -> None:
    _svc(tenant, external_id=1, ayla_service_id=uuid.uuid4())
    # Inactive rows don't count toward the denominator.
    _svc(tenant, external_id=2, ayla_service_id=None, is_active=False)
    output = _run()
    assert "100.0%" in output


@pytest.mark.django_db
def test_low_coverage_warns(tenant: Tenant) -> None:
    _svc(tenant, external_id=1, ayla_service_id=uuid.uuid4())
    _svc(tenant, external_id=2, ayla_service_id=None)
    _svc(tenant, external_id=3, ayla_service_id=None)
    output = _run()
    # 1 of 3 = 33.3% < 50% → product-risk warning.
    assert "Do NOT flip" in output


@pytest.mark.django_db
def test_tenant_slug_filter(tenant: Tenant) -> None:
    other = Tenant.objects.create(slug="cov-other", name="Other")
    _svc(tenant, external_id=1, ayla_service_id=uuid.uuid4())
    _svc(other, external_id=1, ayla_service_id=None)
    output = _run(tenant_slug="cov-test")
    assert "cov-test" in output
    assert "cov-other" not in output
