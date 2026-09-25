"""Снимок записи доезжает до телефона — через бот, а не из хранилища (DRF-2455).

Прямой адрес хранилища каталога телефону бесполезен: MinIO живёт по
внутреннему адресу контейнера. И отдавать его наружу нельзя: бакет
создаётся `public-read`, то есть снимок стал бы доступен любому, кто знает
адрес, — а это фотографии еды, снятые людьми дома.

Поэтому файл идёт через бот: Mini App просит у бота, бот — у каталога под
идентификатором того же человека, каталог проверяет владение. Здесь
проверяется **бот-половина этого пути**; каталожная — в
`nutrition/tests/test_food_photo_endpoint_2455.py`.

Узлы:

* k1 — свой снимок: 200, тип `image/*` и **байты файла**;
* k2 — каталог ответил «нет такого»: 404, и тела нет;
* k3 — каталог не отвечает: отказ, а не пустая картинка;
* k4 — **без initData ручка не отвечает вовсе**: снимок еды не должен
  доставаться тому, кто просто знает адрес.

Подмена для k4: снять `@require_init_data` — узел обязан покраснеть.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.miniapp_api.tests.test_diary_days_2099 import (  # переиспользуем стенд
    _auth,
    _bot_token,  # noqa: F401 — autouse
    _nutrition_on,  # noqa: F401 — autouse
    bot_user,  # noqa: F401 — фикстура
    consent,  # noqa: F401 — фикстура
    tenant,  # noqa: F401 — фикстура
)


pytestmark = pytest.mark.django_db

LOG_ID = "11111111-2222-3333-4444-555555555555"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"pixels" * 4


def _photo(client: Client, user_id: str, *, with_auth: bool = True):
    url = reverse("miniapp_api:customer_food_photo", kwargs={"log_id": LOG_ID})
    if not with_auth:
        return client.get(url)
    return client.get(url, HTTP_AUTHORIZATION=_auth(user_id))


def _client_returning(*, photo=None, exc=None):
    """Каталожный клиент, отвечающий снимком, отказом или падением."""
    client = AsyncMock()
    client.food_photo = AsyncMock(
        side_effect=exc,
        return_value=None if exc else photo,
    )
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


class TestK1OwnPhotoComesThrough:
    def test_bytes_and_type_reach_the_phone(self, client, bot_user, consent) -> None:  # noqa: F811
        patcher, catalog = _client_returning(photo=(PNG_BYTES, "image/png"))
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/png"
        body = b"".join(resp.streaming_content) if resp.streaming else resp.content
        assert body == PNG_BYTES
        # И спрошено у каталога под тем же человеком, а не «вообще».
        assert catalog.food_photo.await_count == 1
        assert catalog.food_photo.await_args.kwargs["log_id"] == LOG_ID


class TestK2CatalogSaysNoSuchPhoto:
    def test_absent_photo_is_404_without_a_body(self, client, bot_user, consent) -> None:  # noqa: F811
        patcher, _ = _client_returning(photo=None)
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        assert resp.status_code == 404
        body = b"".join(resp.streaming_content) if resp.streaming else resp.content
        assert PNG_BYTES not in body


class TestK3CatalogSilent:
    def test_an_unreachable_catalog_is_a_named_refusal(self, client, bot_user, consent) -> None:  # noqa: F811
        from apps.integrations.ayla import NutritionUnavailableError

        patcher, _ = _client_returning(exc=NutritionUnavailableError("timeout"))
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        # Не 200 с пустым телом: пустая картинка читается как «фото сломано».
        assert resp.status_code >= 400
        assert resp.status_code != 200


class TestK4NoInitDataNoPhoto:
    def test_the_route_does_not_answer_without_init_data(self, client, bot_user, consent) -> None:  # noqa: F811
        patcher, catalog = _client_returning(photo=(PNG_BYTES, "image/png"))
        with patcher:
            resp = _photo(client, bot_user.channel_user_id, with_auth=False)

        assert resp.status_code in (401, 403)
        # И каталог даже не спрошен: отказ раньше похода за файлом.
        assert catalog.food_photo.await_count == 0


class TestK5TheFlagReachesTheScreen:
    """Признак `has_photo` обязан доехать до экрана целиком.

    Узел на путь, а не на функцию: ручка дня собирает ответ из записей
    каталога, и «почистить» их по дороге можно молча. Экран, не знающий о
    снимке, либо дёргает файл у каждой записи, либо рисует пустую рамку
    там, где снимка не было никогда.
    """

    def test_entries_carry_has_photo_verbatim(self, client, bot_user, consent) -> None:  # noqa: F811
        from apps.miniapp_api.tests.test_diary_days_2099 import _get, _patch_client, _summary

        with_photo = {
            "id": "fl-1",
            "dish_name": "Овсянка",
            "calories": 320,
            "protein_g": 11,
            "fat_g": 6,
            "carbs_g": 54,
            "meal_type": "breakfast",
            "logged_at": "2026-09-14T05:31:00Z",
            "entry_origin": "photo_estimated_confirmed",
            "has_photo": True,
        }
        without = {**with_photo, "id": "fl-2", "has_photo": False}

        patcher, _ = _patch_client(summary=_summary([with_photo, without]), profile=None)
        with patcher:
            resp = _get(client, bot_user, "customer_diary_day", date="2026-09-14")

        assert resp.status_code == 200, resp.content
        entries = {e["id"]: e for e in resp.json()["entries"]}
        # Сначала о наличии: записи доехали обе.
        assert set(entries) == {"fl-1", "fl-2"}
        # И признак у каждой — тем же значением, без перевода по дороге.
        assert entries["fl-1"]["has_photo"] is True
        assert entries["fl-2"]["has_photo"] is False
