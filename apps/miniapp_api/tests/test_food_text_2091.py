"""Запись еды текстом из Mini App — прокси ``customer/food/estimate`` и ``customer/food/log`` (DRF-2091, F8).

Что сторожится:

* обе ручки ЕСТЬ (до правки — 404 по маршруту; узел присутствия первым);
* три ворот на каждой, до разбора тела: NUTRITION_ENABLED (404), PERSONAL_DATA
  (403 ``consent_required``) и согласие дневника из реестра DRF-1963
  (403 ``food_diary_consent_required`` — своим слагом, экран ведёт на согласие);
* оценка идёт тем же клиентским методом и с теми же аргументами, что текст в
  чате (``estimate_dish(external_user_id, dish_name, portion_g)``; текст
  разбирается ``parse_food_text`` — граммы в конце фразы, DRF-2078), и ничего
  не пишет;
* запись — ``log_meal`` с кодом происхождения §136: ``corrected=false`` →
  ``text_estimated_confirmed``, ``true`` → ``text_user_corrected``;
  ``portion_multiplier = граммы / 100``; ключ идемпотентности — от экрана;
* отказы каталога — по имени: ``food_not_recognized`` 400, ``nutrition_unavailable`` 503.

Положительная пара «чат работает как раньше» — ``apps/skills/food_clarify/tests``
не тронут; этот файл трогает только прокси.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.nutrition_client import (
    FoodNotRecognizedError,
    NutritionUnavailableError,
)
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-food-text"  # noqa: S105 — test fixture  # pragma: allowlist secret


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Клиент"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings):
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture(autouse=True)
def _diary_on(settings):
    settings.NUTRITION_ENABLED = True


@pytest.fixture(autouse=True)
def personal_consent():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
    ) as m:
        yield m


@pytest.fixture(autouse=True)
def diary_consent():
    """Согласие дневника из реестра (DRF-1963) — третьи ворота."""
    with patch("apps.consent.nutrition.diary_is_granted", return_value=True) as m:
        yield m


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="food-text", name="Food Text", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "food-text"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="92091",
        display_name="Клиент",
        client_name="Клиент",
    )


@dataclass
class _Estimate:
    matched_dish: str = "борщ"
    portion_g: float = 250.0
    portion_estimated: bool = False
    kcal: float = 120.0
    protein_g: float | None = 5.0
    fat_g: float | None = 4.0
    carbs_g: float | None = 12.0


@dataclass
class _Log:
    log_id: str = "01J9FOODTEXT000000000000AA"
    dish_name: str = "борщ"
    meal_type: str = "other"
    calories: float = 300.0


def _patch_client(*, estimate=None, log=None):
    client = AsyncMock()
    client.estimate_dish = AsyncMock(
        side_effect=estimate if isinstance(estimate, Exception) else None,
        return_value=None if isinstance(estimate, Exception) else (estimate or _Estimate()),
    )
    client.log_meal = AsyncMock(
        side_effect=log if isinstance(log, Exception) else None,
        return_value=None if isinstance(log, Exception) else (log or _Log()),
    )
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


def _post(client: Client, bot_user: BotUser, name: str, body: dict):
    return client.post(
        reverse(f"miniapp_api:{name}"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
    )


LOG_BODY = {"dish_name": "борщ", "portion_g": 250, "corrected": False, "idempotency_key": "k-1"}


class TestRoutesExist:
    def test_estimate_and_log_are_routed(self):
        """Присутствие впереди: до DRF-2091 обоих маршрутов не было (карта F8)."""
        assert reverse("miniapp_api:customer_food_estimate").endswith("/food/estimate")
        assert reverse("miniapp_api:customer_food_log").endswith("/food/log")


class TestThreeGatesOnBothRoutes:
    @pytest.mark.parametrize(
        "name,body",
        [("customer_food_estimate", {"text": "борщ 250"}), ("customer_food_log", LOG_BODY)],
    )
    def test_diary_off_is_404_before_any_catalog_call(self, client, bot_user, settings, name, body):
        settings.NUTRITION_ENABLED = False
        patcher, fake = _patch_client()
        with patcher:
            resp = _post(client, bot_user, name, body)
        assert resp.status_code == 404
        assert resp.json()["error"] == "nutrition_disabled"
        fake.estimate_dish.assert_not_called()
        fake.log_meal.assert_not_called()

    @pytest.mark.parametrize(
        "name,body",
        [("customer_food_estimate", {"text": "борщ 250"}), ("customer_food_log", LOG_BODY)],
    )
    def test_no_personal_data_consent_is_403(self, client, bot_user, name, body):
        with patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
        ):
            patcher, fake = _patch_client()
            with patcher:
                resp = _post(client, bot_user, name, body)
        assert resp.status_code == 403
        assert resp.json()["error"] == "consent_required"
        fake.estimate_dish.assert_not_called()
        fake.log_meal.assert_not_called()

    @pytest.mark.parametrize(
        "name,body",
        [("customer_food_estimate", {"text": "борщ 250"}), ("customer_food_log", LOG_BODY)],
    )
    def test_no_diary_registry_consent_is_403_by_its_own_name(self, client, bot_user, name, body):
        """F11: строка ``food_diary_processing`` покрывает и текст. Свой слаг — свой экран."""
        with patch("apps.consent.nutrition.diary_is_granted", return_value=False):
            patcher, fake = _patch_client()
            with patcher:
                resp = _post(client, bot_user, name, body)
        assert resp.status_code == 403
        assert resp.json()["error"] == "food_diary_consent_required"
        fake.estimate_dish.assert_not_called()
        fake.log_meal.assert_not_called()


class TestEstimate:
    def test_text_goes_through_the_chats_parser_and_client_method(self, client, bot_user):
        """Та же тропа, что у текста в чате: dish + граммы в конце фразы (DRF-2078)."""
        patcher, fake = _patch_client(estimate=_Estimate(portion_g=250.0, portion_estimated=False))
        with patcher:
            resp = _post(client, bot_user, "customer_food_estimate", {"text": "съела борщ 250"})
        assert resp.status_code == 200, resp.content
        kwargs = fake.estimate_dish.call_args.kwargs
        assert kwargs["dish_name"] == "борщ"
        assert kwargs["portion_g"] == 250.0
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        # Оценка НЕ пишет: log_meal не звался.
        fake.log_meal.assert_not_called()
        data = resp.json()
        assert data["matched_dish"] == "борщ"
        assert data["portion_estimated"] is False
        assert data["kcal"] == 120.0

    def test_no_grams_in_text_means_estimated_portion(self, client, bot_user):
        patcher, fake = _patch_client(estimate=_Estimate(portion_g=100.0, portion_estimated=True))
        with patcher:
            resp = _post(client, bot_user, "customer_food_estimate", {"text": "борщ"})
        assert resp.status_code == 200
        assert fake.estimate_dish.call_args.kwargs["portion_g"] is None
        assert resp.json()["portion_estimated"] is True

    def test_explicit_portion_g_overrides_the_number_in_the_text(self, client, bot_user):
        """«Поправить граммы» на карточке: поправка сильнее числа в тексте."""
        patcher, fake = _patch_client()
        with patcher:
            resp = _post(
                client, bot_user, "customer_food_estimate", {"text": "борщ 250", "portion_g": 300}
            )
        assert resp.status_code == 200
        assert fake.estimate_dish.call_args.kwargs["portion_g"] == 300.0

    def test_unparseable_text_is_400_by_name_without_a_catalog_call(self, client, bot_user):
        patcher, fake = _patch_client()
        with patcher:
            resp = _post(client, bot_user, "customer_food_estimate", {"text": "cb:something"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "food_not_recognized"
        fake.estimate_dish.assert_not_called()

    def test_catalog_not_recognized_is_400_and_unavailable_is_503(self, client, bot_user):
        patcher, _ = _patch_client(estimate=FoodNotRecognizedError("no such dish"))
        with patcher:
            resp = _post(client, bot_user, "customer_food_estimate", {"text": "нечто"})
        assert resp.status_code == 400 and resp.json()["error"] == "food_not_recognized"
        patcher, _ = _patch_client(estimate=NutritionUnavailableError("circuit_open"))
        with patcher:
            resp = _post(client, bot_user, "customer_food_estimate", {"text": "борщ"})
        assert resp.status_code == 503 and resp.json()["error"] == "nutrition_unavailable"


class TestLog:
    def test_confirmed_as_shown_logs_with_text_estimated_confirmed(self, client, bot_user):
        patcher, fake = _patch_client()
        with patcher:
            resp = _post(client, bot_user, "customer_food_log", LOG_BODY)
        assert resp.status_code == 201, resp.content
        kwargs = fake.log_meal.call_args.kwargs
        assert kwargs["dish_name"] == "борщ"
        assert kwargs["meal_type"] == "other"
        assert kwargs["portion_multiplier"] == 2.5  # 250 г / 100 г — как в чате
        assert kwargs["entry_origin"] == "text_estimated_confirmed"
        assert kwargs["idempotency_key"].startswith("food-text-ma:") and kwargs[
            "idempotency_key"
        ].endswith(":k-1")
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        data = resp.json()
        assert data["log_id"] == "01J9FOODTEXT000000000000AA"
        assert data["entry_origin"] == "text_estimated_confirmed"

    def test_corrected_grams_log_with_text_user_corrected(self, client, bot_user):
        patcher, fake = _patch_client()
        with patcher:
            resp = _post(
                client,
                bot_user,
                "customer_food_log",
                {**LOG_BODY, "portion_g": 300, "corrected": True},
            )
        assert resp.status_code == 201
        assert fake.log_meal.call_args.kwargs["entry_origin"] == "text_user_corrected"
        assert fake.log_meal.call_args.kwargs["portion_multiplier"] == 3.0

    @pytest.mark.parametrize(
        "body",
        [
            {**LOG_BODY, "idempotency_key": ""},
            {k: v for k, v in LOG_BODY.items() if k != "idempotency_key"},
            {**LOG_BODY, "portion_g": 0},
            {**LOG_BODY, "portion_g": "250"},
            {**LOG_BODY, "corrected": "yes"},
            {**LOG_BODY, "dish_name": " "},
        ],
    )
    def test_malformed_body_is_400_without_a_catalog_call(self, client, bot_user, body):
        patcher, fake = _patch_client()
        with patcher:
            resp = _post(client, bot_user, "customer_food_log", body)
        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"
        fake.log_meal.assert_not_called()

    def test_catalog_refusals_keep_their_names(self, client, bot_user):
        patcher, _ = _patch_client(log=FoodNotRecognizedError("gone"))
        with patcher:
            resp = _post(client, bot_user, "customer_food_log", LOG_BODY)
        assert resp.status_code == 400 and resp.json()["error"] == "food_not_recognized"
        patcher, _ = _patch_client(log=NutritionUnavailableError("http_503"))
        with patcher:
            resp = _post(client, bot_user, "customer_food_log", LOG_BODY)
        assert resp.status_code == 503 and resp.json()["error"] == "nutrition_unavailable"
