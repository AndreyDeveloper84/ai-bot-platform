"""DRF-1838 (F4, половина Mini App) — правка, удаление и возврат записи еды.

    DELETE /customer/wellness/food/{entry_id}          → delete_meal
    POST   /customer/wellness/food/{entry_id}/restore  → restore_meal
    PATCH  /customer/wellness/food/{entry_id}          → update_meal (граммы)

§109 шаг 7: сохранённую запись можно изменить или удалить. Бот это умеет с
ai-bot-platform#1742; экран дневника Mini App — нет.

Ворота — как у бота (``apps.skills.food_clarify.text_entry``), а не как у
воды на этой же поверхности: удаление своей записи согласия не требует;
правка и возврат пишут в дневник — нужны открытый дневник и согласие на
персональные данные. Каждый отказ каталога назван своим кодом: экран
обязан сказать «окно закрылось» не тем же словом, что «дневник не отвечает».

Каталог подменён двойником (``AsyncMock``): предмет — что ручка зовёт, при
каких условиях и что отдаёт экрану. Пересчёт и окно держит каталог
(beautygo_backend ``nutrition/tests/test_internal_food_log_edit.py``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla import (
    MealDeletion,
    MealEditConflictError,
    MealNotFoundError,
    MealRestoreExpiredError,
    NutritionUncertainOutcomeError,
    NutritionUnavailableError,
)
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-food-entry"  # noqa: S105 — test fixture  # pragma: allowlist secret
ENTRY_ID = "0b6f3c2e-9d1a-4c55-8e2f-1838aaaa0002"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str) -> str:
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
    t = Tenant.objects.create(slug="food-entry-test", name="Food Entry", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "food-entry-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="93001",
        display_name="Анна",
        client_name="Анна К.",
    )


@pytest.fixture
def consent():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
    ) as p:
        yield p


@pytest.fixture
def no_consent():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
    ) as p:
        yield p


@dataclass
class _FakeLog:
    log_id: str = ENTRY_ID
    dish_name: str = "Гречка"
    meal_type: str = "other"
    calories: float = 125.0
    raw: dict = field(default_factory=dict)


def _patch_client(*, delete: Any = None, restore: Any = None, update: Any = None):
    client = AsyncMock()
    for name, value in (
        ("delete_meal", delete),
        ("restore_meal", restore),
        ("update_meal", update),
    ):
        if isinstance(value, Exception):
            setattr(client, name, AsyncMock(side_effect=value))
        else:
            setattr(client, name, AsyncMock(return_value=value))
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


def _entry_url(entry_id: str = ENTRY_ID) -> str:
    return reverse("miniapp_api:customer_wellness_food_entry", args=[entry_id])


def _restore_url(entry_id: str = ENTRY_ID) -> str:
    return reverse("miniapp_api:customer_wellness_food_entry_restore", args=[entry_id])


DELETION = MealDeletion(log_id=ENTRY_ID, restore_window_expires_at="2026-09-15T12:15:00+00:00")


class TestDelete:
    def test_delete_returns_the_restore_window(self, client: Client, bot_user: BotUser, consent):
        patcher, fake = _patch_client(delete=DELETION)
        with patcher:
            resp = client.delete(
                _entry_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert resp.status_code == 200, resp.content
        assert resp.json() == {
            "entry_id": ENTRY_ID,
            "restore_window_expires_at": "2026-09-15T12:15:00+00:00",
        }
        assert fake.delete_meal.await_args.kwargs == {
            "external_user_id": "bot:max:93001",
            "log_id": ENTRY_ID,
        }

    def test_delete_does_not_need_consent(self, client: Client, bot_user: BotUser, no_consent):
        patcher, fake = _patch_client(delete=DELETION)
        with patcher:
            resp = client.delete(
                _entry_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert resp.status_code == 200
        assert fake.delete_meal.await_count == 1

    @pytest.mark.parametrize(
        ("refusal", "status", "slug"),
        [
            (MealNotFoundError("not_found"), 404, "not_found"),
            (MealEditConflictError("conflict"), 409, "water_managed"),
            (NutritionUncertainOutcomeError("network: ReadTimeout"), 502, "ayla_uncertain"),
            (NutritionUnavailableError("http_503"), 502, "ayla_unavailable"),
        ],
    )
    def test_each_refusal_keeps_its_own_code(
        self, client: Client, bot_user: BotUser, consent, refusal, status, slug
    ):
        patcher, fake = _patch_client(delete=refusal)
        with patcher:
            resp = client.delete(
                _entry_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert fake.delete_meal.await_count == 1
        assert resp.status_code == status
        assert resp.json()["error"] == slug

    def test_nutrition_off_refuses_without_calling_ayla(
        self, client: Client, bot_user: BotUser, consent, settings
    ):
        settings.NUTRITION_ENABLED = False
        patcher, fake = _patch_client(delete=DELETION)
        with patcher:
            resp = client.delete(
                _entry_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert resp.status_code == 404
        assert resp.json()["error"] == "nutrition_disabled"
        fake.delete_meal.assert_not_awaited()

    def test_unauthenticated_delete_is_refused(self, client: Client, db):
        # 15.09.2026 UTC (DRF-1893): отказ транспорта — один код 401 no_init_data (было 400 malformed / 401 bad_signature).
        patcher, fake = _patch_client(delete=DELETION)
        with patcher:
            resp = client.delete(_entry_url())

        assert resp.status_code == 401
        fake.delete_meal.assert_not_awaited()


class TestRestore:
    def test_restore_returns_the_entry(self, client: Client, bot_user: BotUser, consent):
        patcher, fake = _patch_client(restore=_FakeLog(calories=300.0))
        with patcher:
            resp = client.post(
                _restore_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert resp.status_code == 200, resp.content
        assert resp.json() == {
            "id": ENTRY_ID,
            "dish_name": "Гречка",
            "calories": 300.0,
            "meal_type": "other",
        }
        assert fake.restore_meal.await_args.kwargs["log_id"] == ENTRY_ID

    def test_a_closed_window_is_410_not_a_generic_failure(
        self, client: Client, bot_user: BotUser, consent
    ):
        patcher, fake = _patch_client(restore=MealRestoreExpiredError("restore_window_expired"))
        with patcher:
            resp = client.post(
                _restore_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert fake.restore_meal.await_count == 1
        assert resp.status_code == 410
        assert resp.json()["error"] == "restore_expired"

    def test_restore_without_consent_is_refused_before_ayla(
        self, client: Client, bot_user: BotUser, no_consent
    ):
        patcher, fake = _patch_client(restore=_FakeLog())
        with patcher:
            resp = client.post(
                _restore_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert resp.status_code == 403
        assert resp.json()["error"] == "consent_required"
        fake.restore_meal.assert_not_awaited()


class TestCorrectGrams:
    def test_grams_become_the_portion_multiplier(self, client: Client, bot_user: BotUser, consent):
        patcher, fake = _patch_client(update=_FakeLog(calories=125.0))
        with patcher:
            resp = client.patch(
                _entry_url(),
                data=json.dumps({"grams": 250}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 200, resp.content
        assert resp.json()["calories"] == 125.0
        assert fake.update_meal.await_args.kwargs == {
            "external_user_id": "bot:max:93001",
            "log_id": ENTRY_ID,
            "portion_multiplier": 2.5,
        }

    def test_grams_out_of_range_are_refused_before_ayla(
        self, client: Client, bot_user: BotUser, consent
    ):
        patcher, fake = _patch_client(update=_FakeLog())
        with patcher:
            resp = client.patch(
                _entry_url(),
                data=json.dumps({"grams": 5}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"
        fake.update_meal.assert_not_awaited()

    def test_correction_without_consent_is_refused_before_ayla(
        self, client: Client, bot_user: BotUser, no_consent
    ):
        patcher, fake = _patch_client(update=_FakeLog())
        with patcher:
            resp = client.patch(
                _entry_url(),
                data=json.dumps({"grams": 250}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 403
        assert resp.json()["error"] == "consent_required"
        fake.update_meal.assert_not_awaited()


class TestEdgesFromReview:
    """Ревью: края, которые код уже отвергает, — теперь со сторожем."""

    @pytest.mark.parametrize("body", [{"grams": True}, {"grams": 250.0}, {"grams": None}, [250]])
    def test_grams_that_are_not_an_integer_are_refused_before_ayla(
        self, client: Client, bot_user: BotUser, consent, body
    ):
        patcher, fake = _patch_client(update=_FakeLog())
        with patcher:
            resp = client.patch(
                _entry_url(),
                data=json.dumps(body),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"
        fake.update_meal.assert_not_awaited()

    def test_nutrition_off_refuses_patch_and_restore_too(
        self, client: Client, bot_user: BotUser, consent, settings
    ):
        settings.NUTRITION_ENABLED = False
        patcher, fake = _patch_client(update=_FakeLog(), restore=_FakeLog())
        with patcher:
            patched = client.patch(
                _entry_url(),
                data=json.dumps({"grams": 250}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
            restored = client.post(
                _restore_url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )

        assert (patched.status_code, patched.json()["error"]) == (404, "nutrition_disabled")
        assert (restored.status_code, restored.json()["error"]) == (404, "nutrition_disabled")
        fake.update_meal.assert_not_awaited()
        fake.restore_meal.assert_not_awaited()

    def test_any_other_refusal_is_a_bad_request_not_an_outage(
        self, client: Client, bot_user: BotUser, consent
    ):
        from apps.integrations.ayla import NutritionAPIError

        patcher, fake = _patch_client(update=NutritionAPIError("http_400_VALIDATION_ERROR"))
        with patcher:
            resp = client.patch(
                _entry_url(),
                data=json.dumps({"grams": 250}),
                content_type="application/json",
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert fake.update_meal.await_count == 1
        assert resp.status_code == 400
        assert resp.json()["error"] == "ayla_bad_request"
