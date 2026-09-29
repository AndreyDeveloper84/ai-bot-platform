"""Mini App клиента: непродаваемое предложение называется (DRF-1989).

Две точки, где Mini App узнаёт от каталога, что предложение не продаётся:

* ``GET /customer/quote`` — ребро несёт ``sellable=false`` и причину; экран
  подтверждения рисовал «Цена 0 ₽»;
* ``POST /customer/bookings`` — каталог отвечает ``422 SERVICE_NOT_ACTIVE`` с
  ``details.reason``; наружу уходило ``400 bad_request`` «booking rejected».

Обе → ``409 offer_not_sellable`` с причиной и человеческим текстом.
Положительная стража: ``SERVICE_NOT_ACTIVE`` без причины остаётся прежним
``bad_request``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from django.test import Client as DjangoClient
from django.utils import timezone

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord, BookingBadRequestError
from apps.integrations.ayla.tests.test_offer_refusal_1989 import CLIENT_PRICE
from apps.tenancy.models import Tenant
from tests.support.catalog_mirror import sync_shaped

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-1989"  # pragma: allowlist secret
AYLA_UID = uuid.uuid4()
SERVICE_AYLA_ID = uuid.uuid4()
MASTER_AYLA_ID = uuid.uuid4()
SALON_TZ = ZoneInfo("Europe/Moscow")

UNSELLABLE_EDGE = {
    "price": "0.00",
    "duration_minutes": 60,
    "sellable": False,
    "unsellable_reason": "price_below_minimum",
}


def _auth(user_id: str = "19890") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Клиент"}),
        "auth_date": str(int(time_module.time())),
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return f"MaxInitData {urlencode({**params, 'hash': digest})}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "offer-1989"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="offer-1989", name="Offer 1989")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="19890",
        chat_id="19890",
        ayla_user_id=AYLA_UID,
        ayla_user_id_is_proxy=False,
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return sync_shaped(
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_updated_at=timezone.now(),
            name="Ольга",
            specialization="Маникюр",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            ayla_user_id=MASTER_AYLA_ID,
        )
    )


@pytest.fixture
def service(tenant: Tenant, master: CatalogMaster) -> CatalogService:
    svc = CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=timezone.now(),
        name="Маникюр",
        slug="manikyur-1989",
        duration_min=60,
        price_from=1500,
        is_active=True,
        ayla_service_id=SERVICE_AYLA_ID,
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=svc)
    return svc


class _StubAylaClient:
    def __init__(self) -> None:
        self.edges: list[dict[str, Any]] = []
        self.exc: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    def create_appointment(self, **kwargs: Any) -> AylaBookingRecord:
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return AylaBookingRecord(appointment_id=str(uuid.uuid4()), raw={"status": "confirmed"})

    def get_specialist_service_edges(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self.edges)


@pytest.fixture
def stub(monkeypatch) -> _StubAylaClient:
    s = _StubAylaClient()
    monkeypatch.setattr("apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: s)
    return s


def _visit_at() -> datetime:
    return (datetime.now(SALON_TZ) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )


def _quote(client: DjangoClient, service: CatalogService, master: CatalogMaster):
    return client.get(
        "/api/v1/customer/quote",
        {"master_id": str(master.id), "service_id": str(service.id)},
        HTTP_AUTHORIZATION=_auth(),
    )


def _post(client: DjangoClient, service: CatalogService, master: CatalogMaster):
    body = {
        "service_id": str(service.id),
        "master_id": str(master.id),
        "visit_at": _visit_at().isoformat(),
    }
    return client.post(
        "/api/v1/customer/bookings",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(),
    )


def test_quote_of_an_unsellable_edge_is_a_named_refusal(client, bot_user, master, service, stub):
    stub.edges = [dict(UNSELLABLE_EDGE)]

    resp = _quote(client, service, master)

    body = resp.json()
    assert (resp.status_code, body.get("error"), body.get("reason")) == (
        409,
        "offer_not_sellable",
        "price_below_minimum",
    ), body
    assert body.get("detail") == CLIENT_PRICE
    assert "quote" not in body


def test_create_refused_as_price_below_minimum_is_named(client, bot_user, master, service, stub):
    stub.exc = BookingBadRequestError(
        "http_422_SERVICE_NOT_ACTIVE",
        status_code=422,
        code="SERVICE_NOT_ACTIVE",
        details={"reason": "price_below_minimum"},
    )

    resp = _post(client, service, master)

    body = resp.json()
    assert (resp.status_code, body.get("error"), body.get("reason")) == (
        409,
        "offer_not_sellable",
        "price_below_minimum",
    ), body
    assert body.get("detail") == CLIENT_PRICE


def test_service_not_active_without_a_reason_stays_as_before(
    client, bot_user, master, service, stub
):
    """Положительная стража: безымянный отказ каталога — прежний ответ."""
    stub.exc = BookingBadRequestError(
        "http_422_SERVICE_NOT_ACTIVE", status_code=422, code="SERVICE_NOT_ACTIVE"
    )

    resp = _post(client, service, master)

    assert (resp.status_code, resp.json().get("error")) == (400, "bad_request")
