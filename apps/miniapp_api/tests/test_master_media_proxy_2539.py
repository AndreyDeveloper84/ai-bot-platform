"""Фото мастера доезжает до телефона — через бот, а не из хранилища (DRF-2539).

Каталог отдавал ``FieldFile.url`` с настройками стенда:
``http://minio:9000/…?AWSAccessKeyId=…&Signature=…&Expires=…`` — хост
контейнера, подпись на час. Решение владельца 29.09 — вариант 3: каталог
отдаёт байты боту (``InternalSpecialistAvatarFileView`` /
``InternalSpecialistPortfolioFileView``, каталог #594), бот — Mini App.
Здесь — **бот-половина** пути; каталожная — в
``users/tests/test_specialist_media_file_2539.py`` репозитория каталога.

Узлы:

* p1 — фото есть: 200, байты, тип из перечня, ``nosniff``, приватный кэш;
* p2 — работа портфолио: каталог спрошен о ТОЙ работе ТОГО мастера;
* p3 — каталог «нет такого» → 404; нет мастера / не UUID → 404 без похода
  в каталог;
* p4 — каталог молчит → 502, не пустая картинка;
* p5 — без initData ручка не отвечает (подпись — единственный пропуск);
* p6 — тип объявляется из перечня, а не отражается: ``text/html`` в
  источнике Mini App исполнился бы вместе с initData;
* p7 — мастер другого салона тоже находится (витрина межсалонная), а
  клиентская строка за показ фото НЕ заводится;
* p8 — каталожный клиент: путь ручки, 404 → ``None``, 5xx → недоступен.

Подмены проверены — см. тело PR.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla import booking_client as bc
from apps.miniapp_api.tests.test_diary_days_2099 import (  # переиспользуем стенд
    _auth,
    _bot_token,  # noqa: F401 — autouse
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

JPEG = b"\xff\xd8\xff\xe0" + b"pixels" * 300
CATALOG_ID = uuid.UUID("5a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d")
ITEM_ID = "0f5e2d1c-9b8a-4c3d-8e7f-6a5b4c3d2e1f"


@pytest.fixture
def master(db) -> CatalogMaster:
    tenant = Tenant.objects.create(slug="media-2539", name="Салон", timezone="Europe/Moscow")
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=None,
        external_updated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        name="Анна Петрова",
        is_active=True,
        catalog_specialist_id=CATALOG_ID,
        photo_url="http://minio:9000/beautygo-media/specialists/avatars/a.jpg?AWSAccessKeyId=K&Signature=S&Expires=1",
    )


def _catalog(*, photo=None, exc=None):
    client = MagicMock()
    client.specialist_media_file = MagicMock(side_effect=exc, return_value=None if exc else photo)
    return (
        patch("apps.miniapp_api.master_media.get_ayla_booking_client", return_value=client),
        client,
    )


def _get(client: Client, master_id, item_id=None, *, with_auth: bool = True):
    if item_id is None:
        url = reverse("miniapp_api:master_media_photo", kwargs={"master_id": str(master_id)})
    else:
        url = reverse(
            "miniapp_api:master_media_portfolio_image",
            kwargs={"master_id": str(master_id), "item_id": str(item_id)},
        )
    if not with_auth:
        return client.get(url)
    return client.get(url, HTTP_AUTHORIZATION=_auth("99001"))


class TestP1PhotoComesThrough:
    def test_bytes_type_and_headers(self, client, master) -> None:
        patcher, catalog = _catalog(photo=(JPEG, "image/jpeg"))
        with patcher:
            resp = _get(client, master.id)
        assert resp.status_code == 200
        assert resp.content == JPEG
        assert resp["Content-Type"] == "image/jpeg"
        assert resp["X-Content-Type-Options"] == "nosniff"
        assert resp["Cache-Control"] == "private, max-age=300"
        kwargs = catalog.specialist_media_file.call_args.kwargs
        assert kwargs == {"specialist_id": str(CATALOG_ID), "item_id": None}


class TestP2PortfolioItem:
    def test_asks_catalog_for_that_item_of_that_master(self, client, master) -> None:
        patcher, catalog = _catalog(photo=(JPEG, "image/webp"))
        with patcher:
            resp = _get(client, master.id, ITEM_ID)
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/webp"
        kwargs = catalog.specialist_media_file.call_args.kwargs
        assert kwargs == {"specialist_id": str(CATALOG_ID), "item_id": ITEM_ID}


class TestP3Absent:
    def test_catalog_says_none(self, client, master) -> None:
        patcher, _ = _catalog(photo=None)
        with patcher:
            resp = _get(client, master.id)
        assert resp.status_code == 404
        assert JPEG not in resp.content

    # Постоянный UUID, не uuid4(): случайный id узла расходится между
    # воркерами xdist, и сбор падает («Different tests were collected»).
    @pytest.mark.parametrize("master_id", ["not-a-uuid", "9d3f1c2b-8a7e-4f60-b5d4-3c2b1a0f9e8d"])
    def test_unknown_master_never_reaches_catalog(self, client, master, master_id) -> None:
        patcher, catalog = _catalog(photo=(JPEG, "image/jpeg"))
        with patcher:
            resp = _get(client, master_id)
        assert resp.status_code == 404
        catalog.specialist_media_file.assert_not_called()

    def test_bad_item_id_never_reaches_catalog(self, client, master) -> None:
        patcher, catalog = _catalog(photo=(JPEG, "image/jpeg"))
        with patcher:
            resp = _get(client, master.id, "x")
        assert resp.status_code == 404
        catalog.specialist_media_file.assert_not_called()


class TestP4CatalogSilent:
    def test_unavailable_is_502(self, client, master) -> None:
        patcher, _ = _catalog(exc=bc.BookingUnavailableError("timeout"))
        with patcher:
            resp = _get(client, master.id)
        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"


class TestP5NoInitDataNoPhoto:
    @pytest.mark.parametrize("item_id", [None, ITEM_ID])
    def test_refused_without_signature(self, client, master, item_id) -> None:
        patcher, catalog = _catalog(photo=(JPEG, "image/jpeg"))
        with patcher:
            resp = _get(client, master.id, item_id, with_auth=False)
        assert resp.status_code == 401
        assert JPEG not in resp.content
        catalog.specialist_media_file.assert_not_called()


class TestP6TypeFromList:
    @pytest.mark.parametrize("declared", ["text/html", "image/svg+xml", "", "application/json"])
    def test_foreign_type_is_octet_stream(self, client, master, declared) -> None:
        patcher, _ = _catalog(photo=(JPEG, declared))
        with patcher:
            resp = _get(client, master.id)
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/octet-stream"


class TestP7AnySalonNoClientRow:
    def test_master_of_another_salon_and_no_bot_user_created(self, client, master) -> None:
        # Мастер — в салоне, у которого нет клиентского бота этой подписи.
        before = BotUser.all_tenants.count()
        patcher, _ = _catalog(photo=(JPEG, "image/png"))
        with patcher:
            resp = _get(client, master.id)
        assert resp.status_code == 200
        assert BotUser.all_tenants.count() == before


class TestP8CatalogClient:
    def _client(self, handler) -> bc.AylaBookingHTTPClient:
        return bc.AylaBookingHTTPClient(
            base_url="https://ayla.test", api_token="tok", transport=httpx.MockTransport(handler)
        )

    def test_avatar_and_portfolio_paths(self) -> None:
        seen: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req.url.path)
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

        c = self._client(handler)
        assert c.specialist_media_file(specialist_id=str(CATALOG_ID)) == (JPEG, "image/jpeg")
        c.specialist_media_file(specialist_id=str(CATALOG_ID), item_id=ITEM_ID)
        assert seen[0].endswith(f"specialists/{CATALOG_ID}/media/avatar/file/")
        assert seen[1].endswith(f"specialists/{CATALOG_ID}/portfolio/{ITEM_ID}/file/")

    def test_404_is_none(self) -> None:
        c = self._client(lambda req: httpx.Response(404, json={"error": "not_found"}))
        assert c.specialist_media_file(specialist_id=str(CATALOG_ID)) is None

    def test_5xx_is_unavailable(self) -> None:
        c = self._client(lambda req: httpx.Response(503, json={}))
        with pytest.raises(bc.BookingUnavailableError):
            c.specialist_media_file(specialist_id=str(CATALOG_ID))
