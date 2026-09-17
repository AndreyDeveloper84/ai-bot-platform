"""Салонная админка: осознанный отказ каталога — не 502, причина видна (DRF-1989).

Каталог отказывает в записи на непродаваемое предложение ``422
SERVICE_NOT_ACTIVE`` с ``details.reason``. Клиент салона превращал это в
безымянный ``SalonNotAllowed``, а вью — в ``outcome="failed"`` с HTTP 502:
администратор читал поломку сервера о системе, которая решила ровно так, как
задумано. Теперь — ``blocked`` с ``reason_code="offer_not_sellable"`` и
текстом, который говорит, что исправить.

Карточка мастера в админке показывает у каждой услуги ``sellable`` и причину.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header, make_master
from apps.admin_api.tests.test_create_booking import _post, _service, _StubSalon
from apps.catalog.models import CatalogMaster, MasterService
from apps.integrations.ayla import salon_client
from apps.integrations.ayla.tests.test_offer_refusal_1989 import STAFF_PRICE
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def stub_salon(monkeypatch):
    def _install(stub: _StubSalon) -> _StubSalon:
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: stub)
        return stub

    return _install


def _catalog_refusal() -> httpx.Response:
    return httpx.Response(
        422,
        json={
            "error": {
                "code": "SERVICE_NOT_ACTIVE",
                "message": "This service is not available for booking",
                "details": {"reason": "price_below_minimum"},
            }
        },
    )


class _RefusingSalon(_StubSalon):
    """Отказ приходит через настоящий разбор ответа, а не готовым исключением."""

    def create_appointment(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        salon_client.AylaSalonClient._raise_for_status(_catalog_refusal())
        raise AssertionError("unreachable")


def test_salon_client_names_an_unsellable_offer() -> None:
    named = getattr(salon_client, "SalonOfferNotSellable", None)
    assert named is not None, "нет salon_client.SalonOfferNotSellable"

    with pytest.raises(named) as exc:
        salon_client.AylaSalonClient._raise_for_status(_catalog_refusal())

    assert getattr(exc.value, "reason", None) == "price_below_minimum"
    assert isinstance(exc.value, salon_client.SalonNotAllowed)


def test_admin_create_refused_as_unsellable_is_blocked_with_the_reason(
    client: Client, tenant: Tenant, owner_bot_user, stub_salon
) -> None:
    master = make_master(tenant, name="Анна", external_id=1)
    service = _service(tenant)
    stub_salon(_RefusingSalon())

    resp = _post(client, tenant, master, service)

    data = resp.json()
    assert (resp.status_code, data.get("outcome"), data.get("reason_code")) == (
        409,
        "blocked",
        "offer_not_sellable",
    ), data
    assert data.get("detail") == STAFF_PRICE


def test_admin_master_card_services_carry_the_sale_state(
    client: Client, owner_bot_user, master_with_service: CatalogMaster
) -> None:
    MasterService.all_tenants.filter(master=master_with_service).update(
        sellable=False, unsellable_reason="price_below_minimum"
    )

    resp = client.get(
        reverse("admin_api:master_detail", args=[str(master_with_service.id)]),
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )

    assert resp.status_code == 200, resp.content
    service = resp.json()["master"]["services"][0]
    assert (service.get("sellable"), service.get("unsellable_reason")) == (
        False,
        "price_below_minimum",
    )
