"""Дневник за неделю в Mini App — прокси к каталогу под субъектом (DRF-2099, бот).

    GET /customer/diary/days?from&to  → diary_days   (строка на день, пустые есть)
    GET /customer/diary/day?date=     → daily_summary (записи одного дня)

Источник дня — каталог (beautygo_backend#511): границы суток считает он по
поясу человека; бот ни дат, ни поясов не пересчитывает и ничего не хранит.
Изоляция — по построению: субъект каталогу называется
``external_user_id_for(bot_user)`` из подписанного initData.

Ворота — как у сводки (``wellness/today``): ``NUTRITION_ENABLED`` +
согласие ПДн (чтение личных записей). ``nutrition_numbers_hidden`` — тот же
производный булев, что у сводки, с тем же умолчанием: ключа нет, когда
анкета не прочиталась, и экран прячет числа (fail-closed).

Каталог подменён двойником (``AsyncMock``): предмет — что ручка зовёт, при
каких условиях и что отдаёт экрану. Отображение HTTP-статусов каталога в
исключения — в ``apps/integrations/ayla/tests/test_nutrition_client_diary_days_2099.py``.

Красное до правки: до маршрута красны ВСЕ узлы модуля (NoReverseMatch),
включая ворота — они красны по отсутствию маршрута, а не по слагу.
"""

from __future__ import annotations

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
from apps.integrations.ayla import (
    DiaryDayRow,
    DiaryDaysResponse,
    NutritionAPIError,
    NutritionUnavailableError,
    SummaryResponse,
)
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-diary-days"  # noqa: S105 — test fixture  # pragma: allowlist secret
EXT = "bot:max:99001"


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
    t = Tenant.objects.create(slug="diary-days-test", name="Diary Days", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "diary-days-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="99001", display_name="Анна"
    )


@pytest.fixture
def consent():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
    ):
        yield


@pytest.fixture
def no_consent():
    with patch(
        "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=False
    ):
        yield


WEEK = DiaryDaysResponse(
    timezone="Europe/Moscow",
    date_from="2026-09-13",
    date_to="2026-09-19",
    days=(
        DiaryDayRow("2026-09-13", 0, None, False),
        DiaryDayRow("2026-09-14", 2, 640.0, True),
    ),
)


def _summary(entries: list[dict[str, Any]]) -> SummaryResponse:
    return SummaryResponse(
        date="2026-09-14",
        calories_total=640.0,
        calories_goal=None,
        protein_g=1.0,
        fat_g=2.0,
        carbs_g=3.0,
        entries=entries,
        raw={},
    )


class _Profile:
    def __init__(self, flags: dict[str, Any] | None) -> None:
        self.health_flags = flags


def _patch_client(*, days: Any = None, summary: Any = None, profile: Any = None):
    client = AsyncMock()
    for name, value in (
        ("diary_days", days),
        ("daily_summary", summary),
        ("get_profile", profile),
    ):
        if isinstance(value, Exception):
            setattr(client, name, AsyncMock(side_effect=value))
        else:
            setattr(client, name, AsyncMock(return_value=value))
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client), client


def _get(client: Client, bot_user: BotUser, name: str, **params: str):
    return client.get(
        reverse(f"miniapp_api:{name}"),
        params,
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


# ─── дни ─────────────────────────────────────────────────────────────────────


class TestDays:
    def test_passes_the_period_through_under_the_callers_external_id(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, fake = _patch_client(days=WEEK, profile=None)
        with patcher:
            resp = _get(
                client,
                bot_user,
                "customer_diary_days",
                **{"from": "2026-09-13", "to": "2026-09-19"},
            )
        assert resp.status_code == 200, resp.content
        assert fake.diary_days.await_args.kwargs == {
            "external_user_id": EXT,
            "date_from": "2026-09-13",
            "date_to": "2026-09-19",
        }

    def test_without_a_period_asks_the_catalog_for_its_default_week(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, fake = _patch_client(days=WEEK, profile=None)
        with patcher:
            resp = _get(client, bot_user, "customer_diary_days")
        assert resp.status_code == 200, resp.content
        assert fake.diary_days.await_args.kwargs == {
            "external_user_id": EXT,
            "date_from": None,
            "date_to": None,
        }

    def test_returns_one_row_per_day_including_empty_days(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(days=WEEK, profile=None)
        with patcher:
            body = _get(client, bot_user, "customer_diary_days").json()
        assert body["timezone"] == "Europe/Moscow"
        assert (body["from"], body["to"]) == ("2026-09-13", "2026-09-19")
        assert body["days"] == [
            {"date": "2026-09-13", "meals_count": 0, "kcal": None, "has_entries": False},
            {"date": "2026-09-14", "meals_count": 2, "kcal": 640.0, "has_entries": True},
        ]
        # анкеты нет вовсе — это не отказ чтения, числа показываются
        assert body["nutrition_numbers_hidden"] is False

    def test_numbers_hidden_follows_the_profile_flag(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(days=WEEK, profile=_Profile({"eating_disorder": True}))
        with patcher:
            body = _get(client, bot_user, "customer_diary_days").json()
        assert body["nutrition_numbers_hidden"] is True
        # наружу — один булев, не диагноз: сырого флага в теле нет
        flat = json.dumps(body)
        assert "nutrition_numbers_hidden" in flat
        assert "health_flags" not in flat
        assert "eating_disorder" not in flat

    def test_profile_unreadable_leaves_the_key_out(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(days=WEEK, profile=NutritionUnavailableError("http_503"))
        with patcher:
            body = _get(client, bot_user, "customer_diary_days").json()
        assert body["days"]
        assert "nutrition_numbers_hidden" not in body

    def test_catalog_refusal_of_the_period_is_a_named_error_not_an_empty_week(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(days=NutritionAPIError("http_400"), profile=None)
        with patcher:
            resp = _get(
                client,
                bot_user,
                "customer_diary_days",
                **{"from": "2026-01-01", "to": "2026-09-19"},
            )
        assert resp.status_code == 400
        assert resp.json()["error"] == "ayla_bad_request"

    def test_catalog_down_is_unavailable(self, client: Client, bot_user: BotUser, consent) -> None:
        patcher, _ = _patch_client(days=NutritionUnavailableError("http_503"), profile=None)
        with patcher:
            resp = _get(client, bot_user, "customer_diary_days")
        assert resp.status_code == 502
        assert resp.json()["error"] == "ayla_unavailable"


# ─── день ────────────────────────────────────────────────────────────────────


class TestDay:
    ENTRY = {
        "id": "log-1",
        "dish_name": "Омлет",
        "calories": 320.0,
        "protein_g": 20.0,
        "fat_g": 24.0,
        "carbs_g": 3.0,
        "meal_type": "breakfast",
        "logged_at": "2026-09-14T06:10:00+00:00",
        "entry_origin": "text_estimated_confirmed",
    }

    def test_asks_the_catalog_summary_for_that_date(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, fake = _patch_client(summary=_summary([self.ENTRY]), profile=None)
        with patcher:
            resp = _get(client, bot_user, "customer_diary_day", date="2026-09-14")
        assert resp.status_code == 200, resp.content
        assert fake.daily_summary.await_args.kwargs == {
            "external_user_id": EXT,
            "date": "2026-09-14",
        }
        body = resp.json()
        assert body["date"] == "2026-09-14"
        assert body["entries"] == [self.ENTRY]
        assert body["nutrition_numbers_hidden"] is False

    def test_a_day_without_entries_is_an_empty_list_not_an_error(
        self, client: Client, bot_user: BotUser, consent
    ) -> None:
        patcher, _ = _patch_client(summary=_summary([]), profile=None)
        with patcher:
            resp = _get(client, bot_user, "customer_diary_day", date="2026-09-13")
        assert resp.status_code == 200, resp.content
        assert resp.json()["entries"] == []

    @pytest.mark.parametrize("bad", ["", "14.09.2026", "2026-9-14", "2026-13-01", "tomorrow"])
    def test_date_must_be_iso_before_the_catalog_is_asked(
        self, client: Client, bot_user: BotUser, consent, bad: str
    ) -> None:
        patcher, fake = _patch_client(summary=_summary([]), profile=None)
        with patcher:
            resp = _get(client, bot_user, "customer_diary_day", date=bad)
        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"
        assert fake.daily_summary.await_count == 0


# ─── ворота ──────────────────────────────────────────────────────────────────


class TestGates:
    @pytest.mark.parametrize("name", ["customer_diary_days", "customer_diary_day"])
    def test_without_personal_data_consent_is_403(
        self, client: Client, bot_user: BotUser, no_consent, name: str
    ) -> None:
        patcher, fake = _patch_client(days=WEEK, summary=_summary([]), profile=None)
        with patcher:
            resp = _get(client, bot_user, name, date="2026-09-14")
        assert resp.status_code == 403
        assert resp.json()["error"] == "consent_required"
        assert fake.diary_days.await_count == 0
        assert fake.daily_summary.await_count == 0

    @pytest.mark.parametrize("name", ["customer_diary_days", "customer_diary_day"])
    def test_nutrition_off_is_404(
        self, client: Client, bot_user: BotUser, consent, settings, name: str
    ) -> None:
        settings.NUTRITION_ENABLED = False
        patcher, fake = _patch_client(days=WEEK, summary=_summary([]), profile=None)
        with patcher:
            resp = _get(client, bot_user, name, date="2026-09-14")
        assert resp.status_code == 404
        assert resp.json()["error"] == "nutrition_disabled"
        assert fake.diary_days.await_count == 0

    @pytest.mark.parametrize("name", ["customer_diary_days", "customer_diary_day"])
    def test_without_init_data_is_401(self, client: Client, tenant: Tenant, name: str) -> None:
        resp = client.get(reverse(f"miniapp_api:{name}"), {"date": "2026-09-14"})
        assert resp.status_code == 401
