"""Tests for the GET /api/v1/master/catalog read-only services endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.master_api.services import catalog as svc
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant


def _make_service(
    *,
    tenant: Tenant,
    name: str,
    slug: str,
    price: Decimal | None = Decimal("2400"),
    duration_min: int | None = 90,
    description: str = "",
    is_active: bool = True,
    external_id: int | None = None,
) -> CatalogService:
    now = datetime.now(tz=timezone.utc)
    if external_id is None:
        external_id = CatalogService.all_tenants.filter(tenant=tenant).count() + 1
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=now,
        slug=slug,
        name=name,
        description=description,
        price_from=price,
        duration_min=duration_min,
        is_active=is_active,
    )


# --- service-layer unit ---------------------------------------------------


class TestCategoryDerivation:
    def test_known_prefix_mapped(self) -> None:
        s = CatalogService(slug="manicure-gel", name="Маникюр гель-лак")
        assert svc._derive_category(s) == "Маникюр"
        s = CatalogService(slug="pedicure-apparatnyy", name="Педикюр")
        assert svc._derive_category(s) == "Педикюр"

    def test_unknown_prefix_title_cased(self) -> None:
        s = CatalogService(slug="weirdcat-something", name="—")
        assert svc._derive_category(s) == "Weirdcat"

    def test_empty_slug_falls_back(self) -> None:
        s = CatalogService(slug="", name="—")
        assert svc._derive_category(s) == svc.DEFAULT_CATEGORY


class TestPriceConversion:
    def test_none_passes_through(self) -> None:
        assert svc._price_rub(None) is None

    def test_decimal_rounded_to_int(self) -> None:
        assert svc._price_rub(Decimal("2400")) == 2400
        assert svc._price_rub(Decimal("2399.50")) == 2400  # banker's rounding fine

    def test_invalid_decimal_returns_none(self) -> None:
        # A degenerate value — `Decimal('NaN')` raises ValueError on
        # to_integral_value with the default rounding context.
        out = svc._price_rub(Decimal("NaN"))
        assert out is None or out == 0


# --- endpoint auth gate ---------------------------------------------------


class TestCatalogAuth:
    def test_no_linked_master_returns_401(self, client: Client, bot_user: BotUser) -> None:
        resp = client.get(
            reverse("master_api:catalog_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

    def test_accepted_master_returns_200(
        self, client: Client, bot_user: BotUser, accepted_master: CatalogMaster
    ) -> None:
        resp = client.get(
            reverse("master_api:catalog_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json() == {"services": []}


# --- service-layer aggregation ------------------------------------------


class TestServicesList:
    def test_empty_master_returns_empty(self, accepted_master: CatalogMaster) -> None:
        assert svc.list_master_services(master=accepted_master) == []

    def test_mapping_returns_service(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        s = _make_service(
            tenant=tenant,
            name="Маникюр гель-лак",
            slug="manicure-gel",
            description="Базовый маникюр + покрытие гель-лак",
        )
        MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=s)
        out = svc.list_master_services(master=accepted_master)
        assert len(out) == 1
        row = out[0]
        assert set(row.keys()) == {
            "service_id",
            "name",
            "price_rub",
            "duration_min",
            "description",
            "category",
            "is_active",
        }
        assert row["name"] == "Маникюр гель-лак"
        assert row["price_rub"] == 2400
        assert row["duration_min"] == 90
        assert row["category"] == "Маникюр"
        assert row["is_active"] is True
        assert row["description"] == "Базовый маникюр + покрытие гель-лак"

    def test_inactive_services_filtered(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        active = _make_service(tenant=tenant, name="Активная", slug="manicure-a")
        archived = _make_service(tenant=tenant, name="Архив", slug="manicure-z", is_active=False)
        MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=active)
        MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=archived)
        out = svc.list_master_services(master=accepted_master)
        names = [r["name"] for r in out]
        assert names == ["Активная"]

    def test_sort_by_category_then_name(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        # Three services across two categories. Expected order:
        #   Маникюр / Маникюр гель-лак
        #   Маникюр / Маникюр с дизайном
        #   Педикюр / Педикюр аппаратный
        s1 = _make_service(tenant=tenant, name="Маникюр с дизайном", slug="manicure-design")
        s2 = _make_service(tenant=tenant, name="Педикюр аппаратный", slug="pedicure-apparatnyy")
        s3 = _make_service(tenant=tenant, name="Маникюр гель-лак", slug="manicure-gel")
        for s in (s1, s2, s3):
            MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=s)
        out = svc.list_master_services(master=accepted_master)
        names = [r["name"] for r in out]
        assert names == [
            "Маникюр гель-лак",
            "Маникюр с дизайном",
            "Педикюр аппаратный",
        ]

    def test_null_price_returned_as_none(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        s = _make_service(tenant=tenant, name="По запросу", slug="manicure-on-request", price=None)
        MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=s)
        out = svc.list_master_services(master=accepted_master)
        assert out[0]["price_rub"] is None


# --- cross-tenant isolation ---------------------------------------------


class TestCatalogCrossTenant:
    def test_sibling_tenant_services_not_visible(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        other_tenant: Tenant,
    ) -> None:
        # Tenant A has one service mapped to its master.
        s_a = _make_service(tenant=tenant, name="A-service", slug="manicure-a")
        MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=s_a)
        # Tenant B has its own master + service.
        other_master = make_master(
            other_tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
        )
        s_b = _make_service(tenant=other_tenant, name="B-service", slug="manicure-b")
        MasterService.all_tenants.create(tenant=other_tenant, master=other_master, service=s_b)

        out_a = svc.list_master_services(master=accepted_master)
        assert [r["name"] for r in out_a] == ["A-service"]

        out_b = svc.list_master_services(master=other_master)
        assert [r["name"] for r in out_b] == ["B-service"]

    def test_endpoint_cross_tenant_isolation(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        other_tenant: Tenant,
    ) -> None:
        other_master = make_master(
            other_tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
        )
        s_leak = _make_service(tenant=other_tenant, name="LEAK", slug="manicure-leak")
        MasterService.all_tenants.create(tenant=other_tenant, master=other_master, service=s_leak)
        resp = client.get(
            reverse("master_api:catalog_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        # Calling master is in tenant A with no services mapped → empty.
        assert resp.json() == {"services": []}
