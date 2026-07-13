"""Catalog upserter tests — Ayla salon-services → CatalogService (S3B / #1044).

PR-1 re-keys the mirror onto the Ayla stable-id: ``upsert_salon_services``
does an ``update_or_create`` on ``(tenant, ayla_service_id)``. Ayla-fed rows
leave ``external_id`` NULL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogService
from apps.catalog.services.http_client import CatalogSalonServiceDTO
from apps.catalog.services.upserter import UpsertResult, upsert_salon_services
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
    name: str = "Маникюр",
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
        # column → row fails, batch continues.
        bad = CatalogSalonServiceDTO(
            ayla_service_id=str(uuid.uuid4()),
            external_updated_at=None,  # type: ignore[arg-type]
            name="Bad",
        )
        res = upsert_salon_services(tenant, [good, bad])
        assert res.created == 1
        assert len(res.errors) == 1
        assert res.errors[0]["ayla_service_id"] == bad.ayla_service_id
