"""DRF-1707 — «Рядом со мной»: расстояние с провода каталога, а не из зеркала.

OD-PILOT-9 distance contract: канонический ``distance_meters: integer | null``,
считает каталог до подтверждённого места оказания услуги (§9); бот его не
выводит из координат профиля в зеркале. Решение владельца D3: координаты
клиента — одноразовые, по кнопке, не сохраняются.

Что заперто:

- ``?lat=&lon=`` → у мастеров появляется ``distance_meters`` с провода,
  список отсортирован по близости, неизвестное — в конец;
- без координат поля нет вовсе (положительная стража: прежний ответ);
- каталог недоступен → поля нет, список прежний (честно, не ноль);
- координаты не попадают в журнал; кривые координаты — 400;
- клиент каталога передаёт lat/lon только в этом запросе и разбирает
  ``distance_meters`` строго (целое или null).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time as time_module
import uuid
from urllib.parse import urlencode

import pytest
from django.test import Client as DjangoClient

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    AylaMaster,
    BookingUnavailableError,
    _distance_meters_from_wire,
    _master_from_wire,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-1707"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str = "17070") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "ayla-nearby-1707"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-nearby-1707", name="Nearby 1707")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="17070",
        chat_id="17070",
        ayla_user_id=uuid.uuid4(),
    )


def _master(tenant, name: str) -> CatalogMaster:
    from django.utils import timezone as tz

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name=name,
        specialization="Маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def masters(tenant) -> list[CatalogMaster]:
    return [_master(tenant, "Анна"), _master(tenant, "Борис"), _master(tenant, "Вера")]


class _Stub:
    def __init__(
        self, distances: dict[str, int | None] | None = None, exc: Exception | None = None
    ):
        self.distances = distances or {}
        self.exc = exc
        self.calls: list[dict] = []

    def get_masters(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return [
            AylaMaster(
                id=mid, name="", specialization="", rating=0.0, position="", distance_meters=d
            )
            for mid, d in self.distances.items()
        ]


def _get(client: DjangoClient, **params):
    return client.get("/api/v1/customer/masters", params, HTTP_AUTHORIZATION=_auth())


class TestNearby:
    def test_distance_from_the_wire_and_sorted_by_it(self, client, bot_user, masters, monkeypatch):
        anna, boris, vera = masters
        stub = _Stub({str(anna.id): 1800, str(boris.id): 850, str(vera.id): None})
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )

        r = _get(client, lat="55.75", lon="37.62")
        assert r.status_code == 200, r.content
        rows = r.json()["masters"]
        assert [(m["name"], m.get("distance_meters")) for m in rows] == [
            ("Борис", 850),
            ("Анна", 1800),
            ("Вера", None),
        ]
        # Координаты ушли в каталог ровно один раз, только для этого запроса.
        assert stub.calls == [{"lat": 55.75, "lon": 37.62}]

    def test_without_coords_no_field_and_no_upstream_call(
        self, client, bot_user, masters, monkeypatch
    ):
        """Положительная стража: прежний ответ, каталог не спрашивается."""
        stub = _Stub()
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        r = _get(client)
        rows = r.json()["masters"]
        assert [m["name"] for m in rows] == ["Анна", "Борис", "Вера"]
        assert all("name" in m and "distance_meters" not in m for m in rows)
        assert stub.calls == []

    def test_upstream_down_keeps_the_list_without_distance(
        self, client, bot_user, masters, monkeypatch
    ):
        stub = _Stub(exc=BookingUnavailableError("down"))
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        r = _get(client, lat="55.75", lon="37.62")
        assert r.status_code == 200
        rows = r.json()["masters"]
        assert [m["name"] for m in rows] == ["Анна", "Борис", "Вера"]
        assert all("distance_meters" not in m for m in rows)

    def test_no_match_keeps_the_list_without_distance(self, client, bot_user, masters, monkeypatch):
        stub = _Stub({str(uuid.uuid4()): 100})
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        rows = _get(client, lat="55.75", lon="37.62").json()["masters"]
        assert all("distance_meters" not in m for m in rows)

    def test_coordinates_never_reach_the_log(self, client, bot_user, masters, monkeypatch, caplog):
        """D3: координаты не сохраняются — в журнале только факт «с гео»."""
        anna, *_ = masters
        stub = _Stub({str(anna.id): 100})
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        with caplog.at_level(logging.INFO):
            _get(client, lat="55.751244", lon="37.618423")
        joined = "\n".join(r.getMessage() for r in caplog.records)
        assert "geo=1" in joined
        assert "55.75" not in joined and "37.61" not in joined

    @pytest.mark.parametrize(
        "params",
        [
            {"lat": "55.75"},
            {"lon": "37.62"},
            {"lat": "abc", "lon": "37.62"},
            {"lat": "91", "lon": "37.62"},
            {"lat": "55.75", "lon": "181"},
        ],
    )
    def test_malformed_coords_are_400(self, client, bot_user, masters, monkeypatch, params):
        stub = _Stub()
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
        )
        r = _get(client, **params)
        assert r.status_code == 400
        assert stub.calls == []


class TestWireParsing:
    def test_distance_is_int_or_none_strictly(self):
        assert _distance_meters_from_wire(850) == 850
        assert _distance_meters_from_wire(0) == 0
        assert _distance_meters_from_wire(None) is None
        assert _distance_meters_from_wire("850") is None
        assert _distance_meters_from_wire(1.8) is None
        assert _distance_meters_from_wire(-5) is None
        assert _distance_meters_from_wire(True) is None

    def test_master_from_wire_carries_distance(self):
        m = _master_from_wire({"id": "x", "display_name": "Анна", "distance_meters": 1800})
        assert m.distance_meters == 1800
        assert _master_from_wire({"id": "x", "display_name": "Анна"}).distance_meters is None

    def test_http_client_sends_coords_only_when_given(self, monkeypatch, settings):
        """lat/lon уезжают параметрами запроса ровно когда переданы."""
        from apps.integrations.ayla.booking_client import AylaBookingHTTPClient

        seen: list[dict] = []

        def fake_rows(self, endpoint, params=None, **kwargs):
            seen.append(dict(params or {}))
            return []

        monkeypatch.setattr(AylaBookingHTTPClient, "_get_all_rows", fake_rows)
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client._require_tenant_id", lambda: "t-1"
        )
        client = AylaBookingHTTPClient.__new__(AylaBookingHTTPClient)
        client.get_masters()
        client.get_masters(lat=55.75, lon=37.62)
        assert seen == [
            {"tenant": "t-1"},
            {"tenant": "t-1", "lat": "55.750000", "lon": "37.620000"},
        ]
