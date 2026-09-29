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
  доставаться тому, кто просто знает адрес;
* k5 — признак `has_photo` доезжает до экрана тем же значением;
* k6 — каталог спрошен **под тем же человеком**: владение проверяет он, но
  только по имени, которое назвал бот;
* k7 — «снимка нет» и «каталог молчит» отвечают по-разному, иначе экран
  перестанет их различать;
* k8 — тип объявляется из перечня, а не отражается: Mini App и эта ручка
  живут в одном источнике, и `text/html` исполнился бы в нём;
* k9 — читающие ручки отвечают только на GET. Узел заведён не впрок: при
  первой сборке этого листа новая функция встала между декораторами и
  соседней ручкой, и та молча потеряла ограничение метода.

Каталожная половина — `nutrition/tests/test_food_photo_endpoint_2455.py`
в репозитории каталога; отсюда она не проверяется.

Подмены проверены: снять `@require_init_data` — краснеют четыре узла;
подставить чужой идентификатор человека — краснеет k6; отражать тип как
пришёл — краснеют три узла k8.
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


class TestK6AskedForTheRightPerson:
    """Несущая посылка бот-половины: спрошено под ТЕМ ЖЕ человеком.

    Владение проверяет каталог, но только по тому идентификатору, который
    назвал бот. Подстановка любого другого сделала бы проверку каталога
    бессмысленной, а узлы выше этого не замечали: k1 сверял только
    `log_id`.
    """

    def test_the_catalog_is_asked_under_this_person(self, client, bot_user, consent) -> None:  # noqa: F811
        from apps.integrations.ayla import external_user_id_for

        patcher, catalog = _client_returning(photo=(PNG_BYTES, "image/png"))
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        assert resp.status_code == 200
        assert catalog.food_photo.await_args.kwargs["external_user_id"] == (
            external_user_id_for(bot_user)
        )


class TestK7RefusalsAreToldApart:
    """404 «снимка нет» и сбой каталога — разные ответы, а не «всё 404».

    Без этого узла подмена «отвечать 404 на любой отказ» проходила молча:
    экран перестал бы отличать «фото нет» от «дневник не отвечает».
    """

    def test_absent_photo_says_not_found(self, client, bot_user, consent) -> None:  # noqa: F811
        patcher, _ = _client_returning(photo=None)
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_a_silent_catalog_says_unavailable(self, client, bot_user, consent) -> None:  # noqa: F811
        from apps.integrations.ayla import NutritionUnavailableError

        patcher, _ = _client_returning(exc=NutritionUnavailableError("timeout"))
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"


class TestK8TheTypeIsNotReflected:
    """Тип объявляем из перечня, а не как отдал каталог.

    Mini App и эта ручка живут в одном источнике, поэтому объект,
    объявленный `text/html`, исполнился бы в нём вместе с initData.
    """

    def test_an_image_type_passes(self, client, bot_user, consent) -> None:  # noqa: F811
        patcher, _ = _client_returning(photo=(PNG_BYTES, "image/png"))
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        assert resp["Content-Type"] == "image/png"

    @pytest.mark.parametrize("wire_type", ["text/html", "image/svg+xml", "application/javascript"])
    def test_an_executable_type_is_downgraded(self, client, bot_user, consent, wire_type) -> None:  # noqa: F811
        patcher, _ = _client_returning(photo=(b"<svg onload=alert(1)>", wire_type))
        with patcher:
            resp = _photo(client, bot_user.channel_user_id)

        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/octet-stream"
        assert resp["Content-Disposition"] == "inline"


class TestK9OnlyGet:
    """Соседняя ручка дня однажды уже потеряла ограничение метода.

    Оно потерялось молча — новая функция встала между декораторами и той,
    которой они принадлежали. Узел ниже ловит именно это.
    """

    @pytest.mark.parametrize("route", ["customer_diary_day", "customer_food_photo"])
    def test_a_post_is_not_a_read(self, client, bot_user, consent, route) -> None:  # noqa: F811
        from django.urls import reverse

        kwargs = {"log_id": LOG_ID} if route == "customer_food_photo" else {}
        url = reverse(f"miniapp_api:{route}", kwargs=kwargs)

        resp = client.post(url, HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))

        assert resp.status_code == 405
