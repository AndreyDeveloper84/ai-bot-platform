"""Избранные блюда в Mini App — прокси к каталогу под субъектом (DRF-2092, F12, бот).

    GET    /customer/saved-meals            → list_saved_meals
    POST   /customer/saved-meals            → save_meal (снимок ИЛИ food_log_id)
    DELETE /customer/saved-meals/{meal_id}  → delete_saved_meal

Источник — каталог (beautygo_backend#505): строки живут под внешним
идентификатором человека и переживают переустановку; бот ничего не хранит.
Изоляция — по построению: субъект каталогу называется
``external_user_id_for(bot_user)`` из подписанного initData, чужой id в
запросе не существует как понятие.

Ворота — как у соседей на этой же поверхности: GET — ``NUTRITION_ENABLED`` +
согласие ПДн (чтение личных записей, как ``wellness/today``); POST — плюс
согласие дневника из реестра (``diary_is_granted``, DRF-1963: «в избранное»
пишет личные данные, как F8); DELETE — как удаление записи (DRF-1838):
убрать своё человек вправе всегда.

Каталог подменён двойником (``AsyncMock``): предмет — что ручка зовёт, при
каких условиях и что отдаёт экрану. Отображение HTTP-статусов каталога в
исключения — в ``apps/integrations/ayla/tests/test_nutrition_client_saved_meals_2092.py``.

Красное до правки объявлено поимённо до прогона как 9 красных / 2 контроля;
измерено шире: до маршрута и типа ``SavedMealRow`` красны ВСЕ 16 узлов этого
модуля (NoReverseMatch / ImportError), включая три «контроля» — они красны по
отсутствию маршрута, а не по слагу; названо честно.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import time as time_module
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla import MealNotFoundError, NutritionUnavailableError
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-saved-meals"  # noqa: S105 — test fixture  # pragma: allowlist secret
MEAL_ID = "0b6f3c2e-9d1a-4c55-8e2f-2092aaaa0001"
LOG_ID = "0b6f3c2e-9d1a-4c55-8e2f-2092bbbb0001"
EXT = "bot:max:92001"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _auth(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Анна"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="saved-meals-test", name="Saved Meals", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "saved-meals-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="92001", display_name="Анна"
    )


@pytest.fixture
def consent():
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield


@pytest.fixture
def no_consent():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
    ):
        yield


@pytest.fixture
def no_diary_consent():
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=False),
    ):
        yield


def _row(**overrides: Any):
    from apps.integrations.ayla import SavedMealRow

    row = SavedMealRow(
        meal_id=MEAL_ID,
        dish_name="Борщ",
        portion_g=250.0,
        calories=125.0,
        protein_g=5.0,
        fat_g=7.5,
        carbs_g=10.0,
        source_food_log_id=None,
        created_at="2026-09-18T12:00:00+00:00",
    )
    return dataclasses.replace(row, **overrides)


def _patch_client(*, list_: Any = None, save: Any = None, delete: Any = None):
    client = AsyncMock()
    for name, value in (
        ("list_saved_meals", list_),
        ("save_meal", save),
        ("delete_saved_meal", delete),
    ):
        if isinstance(value, Exception):
            setattr(client, name, AsyncMock(side_effect=value))
        else:
            setattr(client, name, AsyncMock(return_value=value))
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


def _list_url() -> str:
    return reverse("miniapp_api:customer_saved_meals")


def _item_url(meal_id: str = MEAL_ID) -> str:
    return reverse("miniapp_api:customer_saved_meal", args=[meal_id])


def _get(client: Client, bot_user: BotUser):
    return client.get(_list_url(), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))


def _post(client: Client, bot_user: BotUser, body: dict):
    return client.post(
        _list_url(),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


def _delete(client: Client, bot_user: BotUser, meal_id: str = MEAL_ID):
    return client.delete(_item_url(meal_id), HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id))


# ─── список ──────────────────────────────────────────────────────────────────


class TestList:
    def test_list_proxies_to_the_catalog_under_the_callers_external_id(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, fake = _patch_client(list_=[_row()])
        with patcher:
            resp = _get(client, bot_user)
        assert resp.status_code == 200, resp.content
        assert fake.list_saved_meals.await_args.kwargs == {"external_user_id": EXT}

    def test_list_returns_the_rows_as_the_screen_needs(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(list_=[_row(), _row(meal_id=LOG_ID, dish_name="Омлет")])
        with patcher:
            body = _get(client, bot_user).json()
        assert [i["dish_name"] for i in body["items"]] == ["Борщ", "Омлет"]
        first = body["items"][0]
        assert first == {
            "id": MEAL_ID,
            "dish_name": "Борщ",
            "portion_g": 250.0,
            "calories": 125.0,
            "protein_g": 5.0,
            "fat_g": 7.5,
            "carbs_g": 10.0,
            "source_food_log_id": None,
            "created_at": "2026-09-18T12:00:00+00:00",
        }


# ─── сохранить ───────────────────────────────────────────────────────────────


class TestSave:
    def test_save_from_a_record_sends_food_log_id(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, fake = _patch_client(save=(_row(source_food_log_id=LOG_ID), True))
        with patcher:
            resp = _post(client, bot_user, {"food_log_id": LOG_ID})
        assert resp.status_code == 201, resp.content
        assert fake.save_meal.await_args.kwargs == {
            "external_user_id": EXT,
            "food_log_id": LOG_ID,
        }
        assert resp.json()["source_food_log_id"] == LOG_ID

    def test_save_from_a_snapshot_sends_dish_and_portion(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, fake = _patch_client(save=(_row(), True))
        with patcher:
            resp = _post(
                client,
                bot_user,
                {"dish_name": "Борщ", "portion_g": 250, "calories": 125.0, "protein_g": 5.0},
            )
        assert resp.status_code == 201, resp.content
        assert fake.save_meal.await_args.kwargs == {
            "external_user_id": EXT,
            "dish_name": "Борщ",
            "portion_g": 250.0,
            "calories": 125.0,
            "protein_g": 5.0,
            "fat_g": None,
            "carbs_g": None,
        }

    def test_save_returns_200_when_the_catalog_says_it_already_existed(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(save=(_row(), False))
        with patcher:
            resp = _post(client, bot_user, {"dish_name": "Борщ", "portion_g": 250})
        assert resp.status_code == 200, resp.content
        assert resp.json()["id"] == MEAL_ID

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"dish_name": "Борщ"},
            {"portion_g": 250},
            {"dish_name": "Борщ", "portion_g": 0},
            {"dish_name": "Борщ", "portion_g": True},
        ],
    )
    def test_a_body_without_a_dish_and_portion_or_a_record_is_400(
        self, client: Client, bot_user: BotUser, consent, body: dict
    ) -> None:
        patcher, fake = _patch_client(save=(_row(), True))
        with patcher:
            resp = _post(client, bot_user, body)
        assert resp.status_code == 400, resp.content
        assert resp.json()["error"] == "malformed"
        assert fake.save_meal.await_count == 0


# ─── удалить ─────────────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_proxies_and_returns_deleted(
        self, client: Client, bot_user: BotUser, no_consent
    ) -> None:
        """Без согласия: убрать своё человек вправе всегда (как запись, DRF-1838)."""
        patcher, fake = _patch_client(delete=MEAL_ID)
        with patcher:
            resp = _delete(client, bot_user)
        assert resp.status_code == 200, resp.content
        assert resp.json() == {"id": MEAL_ID, "deleted": True}
        assert fake.delete_saved_meal.await_args.kwargs == {
            "external_user_id": EXT,
            "meal_id": MEAL_ID,
        }

    def test_a_catalog_404_is_named_not_found(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(delete=MealNotFoundError("not_found"))
        with patcher:
            resp = _delete(client, bot_user)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_an_unavailable_catalog_is_named_not_an_empty_list(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(list_=NutritionUnavailableError("down"))
        with patcher:
            resp = _get(client, bot_user)
        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"


# ─── ворота (контроли — стоят у соседей и до правки) ─────────────────────────


class TestGates:
    def test_nutrition_off_is_404_nutrition_disabled(
        self, client: Client, bot_user: BotUser, consent, settings
    ) -> None:
        settings.NUTRITION_ENABLED = False
        patcher, fake = _patch_client(list_=[])
        with patcher:
            resp = _get(client, bot_user)
        assert resp.status_code == 404
        assert resp.json()["error"] == "nutrition_disabled"
        assert fake.list_saved_meals.await_count == 0

    def test_list_without_consent_is_403(
        self, client: Client, bot_user: BotUser, no_consent
    ) -> None:
        patcher, fake = _patch_client(list_=[])
        with patcher:
            resp = _get(client, bot_user)
        assert resp.status_code == 403
        assert resp.json()["error"] == "consent_required"
        assert fake.list_saved_meals.await_count == 0

    def test_save_without_diary_consent_is_403_food_diary_consent_required(
        self, client: Client, bot_user: BotUser, no_diary_consent
    ) -> None:
        patcher, fake = _patch_client(save=(_row(), True))
        with patcher:
            resp = _post(client, bot_user, {"dish_name": "Борщ", "portion_g": 250})
        assert resp.status_code == 403
        assert resp.json()["error"] == "food_diary_consent_required"
        assert fake.save_meal.await_count == 0
