"""DRF-1708 — цена и длительность, которые видел человек, едут в создание
записи; расхождение возвращается ему по имени, а не молча применяется.

Решение владельца (пакет 2, D4): авторитетна ровно та execution option,
которую клиент видел и подтвердил; расхождение → MATERIAL_CHANGE →
показать → новое подтверждение. Сервер каталога с #393 отвечает
``409 QUOTE_CHANGED`` с ``details = {field, quoted, applied}``.

Что заперто здесь (бот, Mini App API):

- ``GET /customer/quote`` отдаёт цену/длительность ребра мастер+услуга
  (то, что Ayla штампует на запись); без ребра — значения услуги из
  зеркала с ``source: "service"``; неизвестное — ``null``, не число;
- ``POST /customer/bookings`` пропускает ``quoted_*`` в Ayla ровно как
  прислано (десятичная СТРОКА, не float) и НЕ шлёт их, когда клиент их
  не прислал (положительная стража: прежние вызывающие не тронуты);
- ``409 QUOTE_CHANGED`` от Ayla → ``409 quote_changed`` наружу с обеими
  парами дословно; это не ``slot_unavailable`` и не ``bad_request``;
- кривая котировка — 400 до любого вызова Ayla.
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

from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    AylaBookingRecord,
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-1708"
AYLA_UID = uuid.uuid4()
SERVICE_AYLA_ID = uuid.uuid4()
MASTER_AYLA_ID = uuid.uuid4()
SALON_TZ = ZoneInfo("Europe/Moscow")


def visit_at() -> datetime:
    return (datetime.now(SALON_TZ) + timedelta(days=7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str = "17080") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "ayla-quote-1708"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-quote-1708", name="Quote 1708")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="17080", chat_id="17080",
        ayla_user_id=AYLA_UID,
    )


@pytest.fixture
def master(tenant) -> CatalogMaster:
    from django.utils import timezone as tz

    return CatalogMaster.all_tenants.create(
        tenant=tenant, external_updated_at=tz.now(), name="Ольга",
        specialization="Маникюр", is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED, ayla_user_id=MASTER_AYLA_ID,
    )


@pytest.fixture
def service(tenant, master) -> CatalogService:
    from django.utils import timezone as tz

    from apps.catalog.models import MasterService

    svc = CatalogService.all_tenants.create(
        tenant=tenant, external_updated_at=tz.now(), name="Маникюр", slug="manikyur",
        duration_min=60, price_from=1500, is_active=True, ayla_service_id=SERVICE_AYLA_ID,
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=svc)
    return svc


class _StubAylaClient:
    def __init__(self, *, exc: Exception | None = None, edges: list[dict] | None = None,
                 edges_exc: Exception | None = None) -> None:
        self.exc = exc
        self.edges = edges if edges is not None else []
        self.edges_exc = edges_exc
        self.calls: list[dict] = []
        self.edge_calls: list[dict] = []

    def create_appointment(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return AylaBookingRecord(appointment_id=str(uuid.uuid4()), raw={"id": "x", "status": "confirmed"})

    def get_specialist_service_edges(self, **kwargs):
        self.edge_calls.append(kwargs)
        if self.edges_exc:
            raise self.edges_exc
        return self.edges


@pytest.fixture
def stub(monkeypatch) -> _StubAylaClient:
    s = _StubAylaClient()
    monkeypatch.setattr("apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: s)
    return s


def _post(client: DjangoClient, service, master, **extra: Any):
    body = {"service_id": str(service.id), "master_id": str(master.id), "visit_at": visit_at().isoformat(), **extra}
    return client.post("/api/v1/customer/bookings", data=json.dumps(body),
                       content_type="application/json", HTTP_AUTHORIZATION=_auth())


def _quote(client: DjangoClient, service, master):
    return client.get("/api/v1/customer/quote", {"master_id": str(master.id), "service_id": str(service.id)},
                      HTTP_AUTHORIZATION=_auth())


# ─── GET /customer/quote ───────────────────────────────────────────────────


class TestQuote:
    def test_edge_wins_over_service_base(self, client, bot_user, master, service, stub):
        stub.edges = [{"price": "1700.00", "duration_minutes": 45}]
        r = _quote(client, service, master)
        assert r.status_code == 200, r.content
        assert r.json()["quote"] == {"price": "1700.00", "duration_minutes": 45, "source": "edge"}
        # Ребро спрашивается по тем же id, что и создание записи.
        assert stub.edge_calls == [{"specialist_id": str(master.id), "service_id": str(SERVICE_AYLA_ID)}]

    def test_no_edge_falls_back_to_the_mirror_service_values(self, client, bot_user, master, service, stub):
        r = _quote(client, service, master)
        assert r.status_code == 200
        assert r.json()["quote"] == {"price": "1500.00", "duration_minutes": 60, "source": "service"}

    def test_edge_read_failure_degrades_to_service_values(self, client, bot_user, master, service, stub):
        stub.edges_exc = BookingUnavailableError("down")
        r = _quote(client, service, master)
        assert r.status_code == 200
        assert r.json()["quote"]["source"] == "service"

    def test_unknown_values_are_null_not_numbers(self, client, bot_user, master, service, stub):
        CatalogService.all_tenants.filter(id=service.id).update(price_from=None, duration_min=None)
        r = _quote(client, service, master)
        assert r.json()["quote"] == {"price": None, "duration_minutes": None, "source": "service"}

    def test_unknown_master_is_404(self, client, bot_user, master, service, stub):
        r = client.get("/api/v1/customer/quote", {"master_id": str(uuid.uuid4()), "service_id": str(service.id)},
                       HTTP_AUTHORIZATION=_auth())
        assert r.status_code == 404

    def test_malformed_ids_are_400(self, client, bot_user, master, service, stub):
        r = client.get("/api/v1/customer/quote", {"master_id": "nope", "service_id": str(service.id)},
                       HTTP_AUTHORIZATION=_auth())
        assert r.status_code == 400


# ─── POST /customer/bookings — passthrough ─────────────────────────────────


class TestQuotedPassthrough:
    def test_quoted_values_ride_to_ayla_verbatim(self, client, bot_user, master, service, stub):
        r = _post(client, service, master, quoted_price="1500.00", quoted_duration_minutes=60)
        assert r.status_code == 201, r.content
        assert stub.calls[0]["quoted_price"] == "1500.00"
        assert stub.calls[0]["quoted_duration_minutes"] == 60

    def test_price_stays_a_decimal_string_not_a_float(self, client, bot_user, master, service, stub):
        # 1500 как число в JSON → строка «1500», не 1500.0: сравнение по
        # значению делает Ayla, написание — наше и должно быть точным.
        r = _post(client, service, master, quoted_price=1500)
        assert r.status_code == 201
        assert stub.calls[0]["quoted_price"] == "1500"
        assert isinstance(stub.calls[0]["quoted_price"], str)

    def test_without_quote_nothing_is_sent(self, client, bot_user, master, service, stub):
        """Положительная стража: прежний клиент не шлёт — прежний вызов."""
        r = _post(client, service, master)
        assert r.status_code == 201
        assert "quoted_price" not in stub.calls[0]
        assert "quoted_duration_minutes" not in stub.calls[0]

    @pytest.mark.parametrize("extra", [
        {"quoted_price": "abc"},
        {"quoted_price": "-1"},
        {"quoted_duration_minutes": 0},
        {"quoted_duration_minutes": "60"},
        {"quoted_duration_minutes": True},
    ])
    def test_malformed_quote_is_400_before_ayla(self, client, bot_user, master, service, stub, extra):
        r = _post(client, service, master, **extra)
        assert r.status_code == 400, r.content
        assert stub.calls == []


# ─── POST /customer/bookings — QUOTE_CHANGED ───────────────────────────────


class TestQuoteChanged:
    def test_409_quote_changed_carries_both_values_verbatim(self, client, bot_user, master, service, monkeypatch):
        s = _StubAylaClient(exc=BookingBadRequestError(
            "http_409_QUOTE_CHANGED", status_code=409, code="QUOTE_CHANGED",
            details={"field": "price", "quoted": "1500.00", "applied": "1700.00"},
        ))
        monkeypatch.setattr("apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: s)
        r = _post(client, service, master, quoted_price="1500.00", quoted_duration_minutes=60)
        assert r.status_code == 409, r.content
        body = r.json()
        assert body["error"] == "quote_changed"
        assert body["details"] == {"field": "price", "quoted": "1500.00", "applied": "1700.00"}

    def test_duration_change_keeps_its_own_field(self, client, bot_user, master, service, monkeypatch):
        s = _StubAylaClient(exc=BookingBadRequestError(
            "http_409_QUOTE_CHANGED", status_code=409, code="QUOTE_CHANGED",
            details={"field": "duration_minutes", "quoted": 60, "applied": 45},
        ))
        monkeypatch.setattr("apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: s)
        r = _post(client, service, master, quoted_price="1500.00", quoted_duration_minutes=60)
        assert r.status_code == 409
        assert r.json()["details"] == {"field": "duration_minutes", "quoted": 60, "applied": 45}

    def test_other_409_is_still_not_quote_changed(self, client, bot_user, master, service, monkeypatch):
        """Положительная стража: занятый слот не переименовывается."""
        s = _StubAylaClient(exc=BookingBadRequestError(
            "http_409_SLOT_TAKEN", status_code=409, code="slot_taken",
        ))
        monkeypatch.setattr("apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: s)
        r = _post(client, service, master, quoted_price="1500.00")
        assert r.status_code == 409
        assert r.json()["error"] == "slot_unavailable"
