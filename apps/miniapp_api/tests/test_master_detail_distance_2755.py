"""DRF-2755 — расстояние до мастера на его карточке (``GET /masters/{id}``).

BC-правка, одобренная владельцем 02.10: ручка карточки принимает
необязательные ``?lat=&lon=`` и берёт расстояние у каталога тем же путём,
что список (``get_masters`` → ``distance_meters``). Второго алгоритма нет.
Правила D3 те же: координаты одноразовые, не хранятся, в журнал не пишутся.

Что заперто:

- с координатами — ``distance_meters`` с провода, ноль — настоящее значение;
- ``null`` с провода — поле ``null`` (экран блок не рисует);
- без координат, без флага ``BOOKING_VIA_AYLA_REST``, при лежащем каталоге
  и при чужом мастере в ответе — поля нет вовсе, карточка прежняя;
- кривые координаты — 400; мастер чужого салона — 404 и каталог не зовётся;
- координаты не попадают в журнал.
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
from apps.integrations.ayla.booking_client import AylaMaster, BookingUnavailableError
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2755"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str = "27550") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "ayla-card-2755"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-card-2755", name="Card 2755")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="27550",
        chat_id="27550",
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
def anna(tenant) -> CatalogMaster:
    return _master(tenant, "Анна")


class _Stub:
    def __init__(
        self,
        distances: dict[str, int | None] | None = None,
        exc: Exception | None = None,
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


def _use(monkeypatch, stub: _Stub) -> None:
    monkeypatch.setattr(
        "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: stub
    )


def _get(client: DjangoClient, master_id, **params):
    return client.get(f"/api/v1/customer/masters/{master_id}", params, HTTP_AUTHORIZATION=_auth())


class TestTheDistanceOnTheCard:
    def test_c1_distance_from_the_wire_for_this_master(self, client, bot_user, anna, monkeypatch):
        stub = _Stub({str(anna.id): 1303})
        _use(monkeypatch, stub)

        r = _get(client, anna.id, lat="53.2", lon="45.0")

        assert r.status_code == 200, r.content
        assert r.json()["master"]["distance_meters"] == 1303
        assert r.json()["master"]["name"] == "Анна"
        assert stub.calls == [{"specialist_id": str(anna.id), "lat": 53.2, "lon": 45.0}]

    def test_c2_zero_metres_is_a_real_distance(self, client, bot_user, anna, monkeypatch):
        _use(monkeypatch, _Stub({str(anna.id): 0}))

        r = _get(client, anna.id, lat="53.2", lon="45.0")

        assert r.json()["master"]["distance_meters"] == 0

    def test_c3_unknown_distance_stays_null(self, client, bot_user, anna, monkeypatch):
        _use(monkeypatch, _Stub({str(anna.id): None}))

        r = _get(client, anna.id, lat="53.2", lon="45.0")

        master = r.json()["master"]
        assert "distance_meters" in master
        assert master["distance_meters"] is None


class TestNoDistanceMeansTheOldCard:
    def test_c4_without_coordinates_the_catalog_is_not_asked(
        self, client, bot_user, anna, monkeypatch
    ):
        stub = _Stub({str(anna.id): 1303})
        _use(monkeypatch, stub)

        r = _get(client, anna.id)

        assert r.status_code == 200
        assert "distance_meters" not in r.json()["master"]
        assert r.json()["master"]["name"] == "Анна"
        assert stub.calls == []

    def test_c5_with_the_rest_flag_off_the_catalog_is_not_asked(
        self, client, bot_user, anna, monkeypatch, settings
    ):
        settings.BOOKING_VIA_AYLA_REST = False
        stub = _Stub({str(anna.id): 1303})
        _use(monkeypatch, stub)

        r = _get(client, anna.id, lat="53.2", lon="45.0")

        assert r.status_code == 200
        assert "distance_meters" not in r.json()["master"]
        assert stub.calls == []

    def test_c6_catalog_down_keeps_the_card(self, client, bot_user, anna, monkeypatch):
        _use(monkeypatch, _Stub(exc=BookingUnavailableError("down")))

        r = _get(client, anna.id, lat="53.2", lon="45.0")

        assert r.status_code == 200
        assert "distance_meters" not in r.json()["master"]
        assert r.json()["master"]["name"] == "Анна"

    def test_c7_another_master_in_the_answer_is_not_this_ones_distance(
        self, client, bot_user, anna, monkeypatch
    ):
        _use(monkeypatch, _Stub({str(uuid.uuid4()): 40}))

        r = _get(client, anna.id, lat="53.2", lon="45.0")

        assert r.status_code == 200
        assert "distance_meters" not in r.json()["master"]
        # Положительная пара: карточка того мастера, о котором спросили.
        assert r.json()["master"]["id"] == str(anna.id)


class TestTheBorders:
    @pytest.mark.parametrize(
        "params",
        [
            {"lat": "53.2"},
            {"lon": "45.0"},
            {"lat": "north", "lon": "45.0"},
            {"lat": "91", "lon": "45.0"},
        ],
    )
    def test_c8_bad_coordinates_are_400(self, client, bot_user, anna, monkeypatch, params):
        stub = _Stub({str(anna.id): 1303})
        _use(monkeypatch, stub)

        r = _get(client, anna.id, **params)

        assert r.status_code == 400
        assert stub.calls == []

    def test_c9_a_foreign_salons_master_is_404_and_the_catalog_is_not_asked(
        self, client, bot_user, monkeypatch
    ):
        other = Tenant.objects.create(slug="ayla-other-2755", name="Other 2755")
        stranger = _master(other, "Чужая")
        stub = _Stub({str(stranger.id): 10})
        _use(monkeypatch, stub)

        r = _get(client, stranger.id, lat="53.2", lon="45.0")

        assert r.status_code == 404
        assert stub.calls == []

    def test_c10_coordinates_never_reach_the_log(self, client, bot_user, anna, monkeypatch, caplog):
        _use(monkeypatch, _Stub({str(anna.id): 1303}))
        caplog.set_level(logging.DEBUG)

        r = _get(client, anna.id, lat="53.195878", lon="45.018316")

        assert r.status_code == 200
        text = caplog.text
        assert "master_detail.distance geo=1 matched=1" in text
        assert "53.195878" not in text
        assert "45.018316" not in text
