"""Котировка без выбранного мастера: «от …» только при разных ценах у мастеров;
соло без цены — своя фраза (решения владельца 12.11.2026).

Ayla ставит в запись цену РЕБРА мастер↔услуга (DRF-1067), а не строки
услуги. Когда мастер не выбран, бот раньше печатал ``price_from`` строки как
точную цену — для салона с ценами по мастерам это неправда, а после #2457
``price_from`` у соло-услуг и вовсе минимум по рёбрам.

Узлы:

* q1 — два мастера 2800 и 1600 → «от 1 600 ₽», ``price_is_from``;
* q2 — один мастер 1600 → точная «1 600 ₽»;
* q3 — два мастера с одинаковой ценой → точная;
* q4 — промокод при разбросе → «от 1 600 ₽ → от 1 440 ₽»;
* q5 — рёбер с ценой нет → цена строки услуги, как прежде;
* q6 — непродаваемое ребро (DRF-1962) в разброс не входит;
* q7 — выбран мастер, цена ребра известна → точная даже при разбросе;
* q8 — цены нет: соло → «мастер пока не указал цену…», салон / неизвестно →
  прежняя фраза про администратора.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.provenance import MasterServiceSource, master_service_write
from apps.promotions.models import Promotion
from apps.skills.booking.tools import BookingToolResult, calc_price
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

NBSP = "\xa0"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="quote-from", name="Студия")


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        ayla_service_id=uuid.uuid4(),
        external_updated_at=timezone.now(),
        slug="back-massage",
        name="Массаж спины",
        price_from=Decimal("1600.00"),
        duration_min=30,
    )


def _master(tenant: Tenant, name: str) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant, id=uuid.uuid4(), name=name, external_updated_at=timezone.now()
        )
    )


def _edge(
    tenant: Tenant,
    master: CatalogMaster,
    service: CatalogService,
    price: str,
    **fields: Any,
) -> MasterService:
    with master_service_write(MasterServiceSource.CATALOG_SYNC):
        return MasterService.all_tenants.create(
            tenant=tenant,
            master=master,
            service=service,
            ayla_specialist_service_id=uuid.uuid4(),
            price=Decimal(price),
            duration_min=30,
            **fields,
        )


def _quote(tenant: Tenant, service: CatalogService, **arguments: Any) -> BookingToolResult:
    sid = str(service.ayla_service_id)
    with override_settings(BOOKING_VIA_AYLA_REST=True):
        return calc_price(
            tenant=tenant,
            arguments={"service_id": sid, **arguments},
            allowed_service_ids={sid},
            service_lookup={sid: service.name},
        )


def test_q1_different_master_prices_quote_from_the_lowest(
    tenant: Tenant, service: CatalogService
) -> None:
    _edge(tenant, _master(tenant, "Ольга"), service, "2800.00")
    _edge(tenant, _master(tenant, "Вера"), service, "1600.00")

    result = _quote(tenant, service)

    assert result.error == ""
    assert result.price is not None
    assert result.price.original_price == Decimal("1600.00")
    assert result.price.price_is_from is True
    assert result.text == f"Массаж спины: от 1{NBSP}600{NBSP}₽."


def test_q2_a_single_master_is_quoted_exactly(tenant: Tenant, service: CatalogService) -> None:
    _edge(tenant, _master(tenant, "Ольга"), service, "1600.00")

    result = _quote(tenant, service)

    assert result.price is not None
    assert result.price.price_is_from is False
    assert result.text == f"Массаж спины: 1{NBSP}600{NBSP}₽."


def test_q3_masters_agreeing_on_the_price_are_quoted_exactly(
    tenant: Tenant, service: CatalogService
) -> None:
    _edge(tenant, _master(tenant, "Ольга"), service, "1600.00")
    _edge(tenant, _master(tenant, "Вера"), service, "1600.00")

    result = _quote(tenant, service)

    assert result.price is not None
    assert result.price.price_is_from is False
    assert result.text == f"Массаж спины: 1{NBSP}600{NBSP}₽."


def test_q4_a_promo_on_a_spread_keeps_the_from_on_both_sides(
    tenant: Tenant, service: CatalogService
) -> None:
    _edge(tenant, _master(tenant, "Ольга"), service, "2800.00")
    _edge(tenant, _master(tenant, "Вера"), service, "1600.00")
    Promotion.all_tenants.create(tenant=tenant, code="MAY10", discount_percent=10)

    result = _quote(tenant, service, promo_code="MAY10")

    assert result.price is not None
    assert result.price.final_price == Decimal("1440.00")
    assert result.text == (
        f"Массаж спины: от 1{NBSP}600{NBSP}₽ → от 1{NBSP}440{NBSP}₽ (промокод, −10%)."
    )


def test_q5_without_priced_edges_the_service_row_is_quoted_as_before(
    tenant: Tenant, service: CatalogService
) -> None:
    result = _quote(tenant, service)

    assert result.price is not None
    assert result.price.original_price == Decimal("1600.00")
    assert result.price.price_is_from is False
    assert result.text == f"Массаж спины: 1{NBSP}600{NBSP}₽."


def test_q6_an_unsellable_edge_takes_no_part_in_the_spread(
    tenant: Tenant, service: CatalogService
) -> None:
    _edge(tenant, _master(tenant, "Ольга"), service, "1600.00")
    _edge(
        tenant,
        _master(tenant, "Вера"),
        service,
        "900.00",
        sellable=False,
        unsellable_reason="price_below_minimum",
    )

    result = _quote(tenant, service)

    assert result.price is not None
    assert result.price.original_price == Decimal("1600.00")
    assert result.price.price_is_from is False


def test_q7_a_chosen_master_is_quoted_by_the_edge_even_on_a_spread(
    tenant: Tenant, service: CatalogService
) -> None:
    olga = _master(tenant, "Ольга")
    _edge(tenant, olga, service, "2800.00")
    _edge(tenant, _master(tenant, "Вера"), service, "1600.00")

    with (
        override_settings(BOOKING_VIA_AYLA_REST=True),
        patch("apps.skills.booking.tools._edge_price_for_quote", return_value=Decimal("2800.00")),
    ):
        sid = str(service.ayla_service_id)
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": sid, "master_id": str(olga.id)},
            allowed_service_ids={sid},
            service_lookup={sid: service.name},
            client=object(),
        )

    assert result.price is not None
    assert result.price.original_price == Decimal("2800.00")
    assert result.price.price_is_from is False
    assert result.text == f"Массаж спины: 2{NBSP}800{NBSP}₽."


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        pytest.param(
            "solo",
            "Массаж спины: мастер пока не указал цену на эту услугу — уточни при записи.",
            id="solo",
        ),
        pytest.param(
            "salon",
            "Массаж спины: точную цену озвучит администратор — это услуга по индивидуальному прайсу.",
            id="salon",
        ),
        pytest.param(
            None,
            "Массаж спины: точную цену озвучит администратор — это услуга по индивидуальному прайсу.",
            id="unknown",
        ),
    ],
)
def test_q8_no_price_names_who_can_tell_it(
    tenant: Tenant, service: CatalogService, kind: str | None, expected: str
) -> None:
    CatalogService.all_tenants.filter(pk=service.pk).update(price_from=None)

    with patch("apps.identity.services.workspace_kind.workspace_kind", return_value=kind):
        result = _quote(tenant, service)

    assert result.price is not None
    assert result.price.original_price is None
    assert result.price.solo is (kind == "solo")
    assert result.text == expected
