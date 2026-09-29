"""Кабинет мастера и готовность: «не продаётся» показывается, 0 ₽ — не «настроено» (DRF-1989).

Служебные поверхности не скрывают непродаваемое предложение, а называют его:
мастер должен видеть, какую услугу у него нельзя купить и почему, иначе
причину некому исправить. Готовность считала «настроенной» услугу с ценой
0 ₽ (``price_rub is not None``) и не знала про ``sellable``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.master_api import views
from apps.master_api.services import catalog as svc
from apps.master_api.services.onboarding_readiness import ReadinessItem, build_readiness

pytestmark = pytest.mark.django_db


def _offer(
    master: CatalogMaster,
    *,
    ext: int,
    price: Decimal | None,
    sellable: bool = True,
    reason: str = "",
) -> CatalogService:
    service = CatalogService.all_tenants.create(
        tenant=master.tenant,
        external_id=ext,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug=f"offer-{ext}",
        name=f"Услуга {ext}",
        duration_min=60,
        price_from=price,
        is_active=True,
    )
    MasterService.all_tenants.create(
        tenant=master.tenant,
        master=master,
        service=service,
        sellable=sellable,
        unsellable_reason=reason,
    )
    return service


def _services_item(master: CatalogMaster) -> ReadinessItem:
    return next(item for item in build_readiness(master).items if item.key == "services")


def test_master_catalog_rows_carry_the_sale_state(accepted_master: CatalogMaster) -> None:
    sold = _offer(accepted_master, ext=19891, price=Decimal("2400"))
    unsold = _offer(
        accepted_master, ext=19892, price=Decimal("0"), sellable=False, reason="price_below_minimum"
    )

    rows = {r["service_id"]: r for r in svc.list_master_services(master=accepted_master)}

    sold_row, unsold_row = rows[str(sold.id)], rows[str(unsold.id)]
    assert (sold_row.get("sellable"), sold_row.get("unsellable_reason")) == (True, None)
    assert (unsold_row.get("sellable"), unsold_row.get("unsellable_reason")) == (
        False,
        "price_below_minimum",
    )


def test_master_profile_services_carry_the_sale_state(accepted_master: CatalogMaster) -> None:
    sold = _offer(accepted_master, ext=19893, price=Decimal("2400"))
    unsold = _offer(
        accepted_master, ext=19894, price=Decimal("2400"), sellable=False, reason="inactive"
    )

    rows = {r["id"]: r for r in views._services_for_master(accepted_master)}

    sold_row, unsold_row = rows[str(sold.id)], rows[str(unsold.id)]
    assert (sold_row.get("sellable"), sold_row.get("unsellable_reason")) == (True, None)
    assert (unsold_row.get("sellable"), unsold_row.get("unsellable_reason")) == (False, "inactive")


def test_below_one_rouble_is_not_a_price() -> None:
    assert svc._price_rub(Decimal("0.40")) is None
    assert svc._price_rub(Decimal("0")) is None


def test_readiness_does_not_count_an_unsellable_offer_as_configured(
    accepted_master: CatalogMaster,
) -> None:
    _offer(
        accepted_master,
        ext=19895,
        price=Decimal("2400"),
        sellable=False,
        reason="price_below_minimum",
    )

    item = _services_item(accepted_master)

    assert (item.state, item.detail, item.reason) == (
        "missing",
        {"selected": 1, "configured": 0},
        "offer_not_sellable",
    )


def test_readiness_does_not_count_zero_roubles_as_configured(
    accepted_master: CatalogMaster,
) -> None:
    _offer(accepted_master, ext=19896, price=Decimal("0.00"))

    item = _services_item(accepted_master)

    assert (item.state, item.detail) == ("missing", {"selected": 1, "configured": 0})


def test_readiness_counts_a_priced_sellable_offer(accepted_master: CatalogMaster) -> None:
    """Положительная стража: настоящая цена на продаваемом ребре — «настроено»."""
    _offer(accepted_master, ext=19897, price=Decimal("2400"))

    item = _services_item(accepted_master)

    assert (item.state, item.detail, item.reason) == (
        "done",
        {"selected": 1, "configured": 1},
        None,
    )
