"""DRF-2764 — список «Рядом со мной» сопоставляет расстояние по id каталога.

Каталог отвечает расстоянием под id профиля (``SpecialistProfile.id``).
Список сравнивал его с первичным ключом зеркала. У строки синка они
совпадают, а у склеенного приглашения и соло-мастера первичный ключ —
``uuid4``, и id каталога лежит в ``catalog_specialist_id`` (DRF-1933). Такой
мастер оставался без расстояния в хвосте списка «Рядом с тобой», хотя
каталог его расстояние прислал. Карточка мастера (DRF-2755) сопоставляет по
колонке; список теперь так же.

Что заперто:

- склеенное приглашение получает своё расстояние и встаёт по нему в ряд;
- число под первичным ключом зеркала ему не приписывается;
- пустая колонка — каталог мастера не знает, расстояния нет, мастер в хвосте.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from urllib.parse import urlencode

import pytest
from django.test import Client as DjangoClient

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaMaster
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2764"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str = "27640") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "ayla-nearby-2764"
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-nearby-2764", name="Nearby 2764")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="27640",
        chat_id="27640",
        ayla_user_id=uuid.uuid4(),
    )


def _master(tenant, name: str, *, catalog_id: uuid.UUID | None | str = "own") -> CatalogMaster:
    """Строка зеркала. По умолчанию — строка синка: id каталога равен pk."""
    from django.utils import timezone as tz

    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=tz.now(),
        name=name,
        specialization="Маникюр",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )
    master.catalog_specialist_id = master.id if catalog_id == "own" else catalog_id
    master.save(update_fields=["catalog_specialist_id"])
    return master


class _Stub:
    def __init__(self, distances: dict[str, int | None]):
        self.distances = distances
        self.calls: list[dict] = []

    def get_masters(self, **kwargs):
        self.calls.append(kwargs)
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


def _rows(client: DjangoClient) -> list[tuple[str, int | None]]:
    r = client.get(
        "/api/v1/customer/masters", {"lat": "55.75", "lon": "37.62"}, HTTP_AUTHORIZATION=_auth()
    )
    assert r.status_code == 200, r.content
    return [(m["name"], m.get("distance_meters")) for m in r.json()["masters"]]


class TestTheListMatchesByTheCatalogId:
    def test_n1_a_merged_invite_gets_its_distance_and_its_place(
        self, client, bot_user, tenant, monkeypatch
    ):
        anna = _master(tenant, "Анна")
        catalog_id = uuid.uuid4()
        vera = _master(tenant, "Вера", catalog_id=catalog_id)
        assert vera.id != catalog_id
        _use(monkeypatch, _Stub({str(anna.id): 1800, str(catalog_id): 300}))

        assert _rows(client) == [("Вера", 300), ("Анна", 1800)]

    def test_n2_a_number_under_the_mirror_key_is_not_this_masters(
        self, client, bot_user, tenant, monkeypatch
    ):
        anna = _master(tenant, "Анна")
        catalog_id = uuid.uuid4()
        vera = _master(tenant, "Вера", catalog_id=catalog_id)
        _use(monkeypatch, _Stub({str(anna.id): 1800, str(vera.id): 5, str(catalog_id): 300}))

        assert _rows(client) == [("Вера", 300), ("Анна", 1800)]

    def test_n3_without_a_catalog_id_there_is_no_distance(
        self, client, bot_user, tenant, monkeypatch
    ):
        anna = _master(tenant, "Анна")
        boris = _master(tenant, "Борис", catalog_id=None)
        _use(monkeypatch, _Stub({str(anna.id): 1800, str(boris.id): 5}))

        assert _rows(client) == [("Анна", 1800), ("Борис", None)]
