"""Tests for GET /customer/wellness/today — nutrition composition.

Covers:
- Happy path: daily_summary + get_water_today compose into WellnessToday
- Water ml→glasses conversion
- Graceful degradation: summary fails / water fails / both fail
- pfc omitted when summary unavailable
- active_goals always [] (no Goals endpoint yet)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.nutrition_client import NutritionUnavailableError
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-wellness"  # noqa: S105 — test fixture  # pragma: allowlist secret


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


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="wellness-test", name="Wellness Test", timezone="Europe/Moscow")
    settings.MAX_BOT_TENANT_SLUG = "wellness-test"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="91001",
        display_name="Анна",
        client_name="Анна К.",
    )


# Minimal stand-ins for the dataclass responses the NutritionClient returns.
@dataclass
class _FakeSummary:
    calories_total: float = 1240.0
    calories_goal: int = 2100
    protein_g: float = 65.4
    fat_g: float = 40.1
    carbs_g: float = 120.9
    entries: list = None  # type: ignore[assignment]
    raw: dict = None  # type: ignore[assignment]
    date: str = "2026-05-29"
    ai_comment: Any = None


@dataclass
class _FakeWater:
    total_ml: int = 1000  # 4 glasses
    norm_ml: int = 2000  # 8 glasses
    entries: list = None  # type: ignore[assignment]
    kcal_from_beverages: float = 0.0
    caffeine_mg: float = 0.0
    coffee_cups: int = 0
    tea_cups: int = 0
    raw: dict = None  # type: ignore[assignment]


def _url() -> str:
    return reverse("miniapp_api:customer_wellness_today")


def _patch_nutrition(*, summary, water):
    """Patch get_nutrition_client to return an async-method stub.

    `summary` / `water` are either a value (returned) or an Exception
    instance (raised) to exercise the degrade paths.
    """
    from unittest.mock import AsyncMock

    client = AsyncMock()
    if isinstance(summary, Exception):
        client.daily_summary = AsyncMock(side_effect=summary)
    else:
        client.daily_summary = AsyncMock(return_value=summary)
    if isinstance(water, Exception):
        client.get_water_today = AsyncMock(side_effect=water)
    else:
        client.get_water_today = AsyncMock(return_value=water)
    return patch("apps.integrations.ayla.get_nutrition_client", return_value=client)


class TestWellnessTodayHappyPath:
    def test_full_composition(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["calories_eaten"] == 1240
        assert data["calories_target"] == 2100
        assert data["pfc"] == {"protein_g": 65, "fat_g": 40, "carbs_g": 121}
        assert data["water_glasses_eaten"] == 4  # 1000 / 250
        assert data["water_glasses_target"] == 8  # 2000 / 250
        assert data["active_goals"] == []
        assert data["display_name"] == "Анна К."

    def test_water_conversion_rounds(self, client: Client, bot_user: BotUser):
        # 1300 ml → 5.2 → rounds to 5; 1875 ml → 7.5 → rounds to 8.
        with _patch_nutrition(
            summary=_FakeSummary(),
            water=_FakeWater(total_ml=1300, norm_ml=1875),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        data = resp.json()
        assert data["water_glasses_eaten"] == 5
        assert data["water_glasses_target"] == 8

    def test_zero_water_target_falls_back_to_default(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            summary=_FakeSummary(),
            water=_FakeWater(total_ml=0, norm_ml=0),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        data = resp.json()
        assert data["water_glasses_eaten"] == 0
        assert data["water_glasses_target"] == 8  # default, not 0


class TestWellnessTodayGracefulDegradation:
    def test_summary_unavailable_keeps_water(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            summary=NutritionUnavailableError("circuit_open"),
            water=_FakeWater(),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 200
        data = resp.json()
        # calories zeroed, pfc omitted, but water survives.
        assert data["calories_eaten"] == 0
        assert data["calories_target"] == 0
        assert "pfc" not in data
        assert data["water_glasses_eaten"] == 4

    def test_water_unavailable_keeps_calories(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            summary=_FakeSummary(),
            water=NutritionUnavailableError("circuit_open"),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["calories_eaten"] == 1240
        assert data["pfc"]["protein_g"] == 65
        # water degraded to 0 eaten + default target.
        assert data["water_glasses_eaten"] == 0
        assert data["water_glasses_target"] == 8

    def test_both_unavailable_returns_200_zeros(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            summary=NutritionUnavailableError("down"),
            water=NutritionUnavailableError("down"),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        # Dashboard resilience: 200 with zeros, never 502.
        assert resp.status_code == 200
        data = resp.json()
        assert data["calories_eaten"] == 0
        assert data["water_glasses_eaten"] == 0
        assert "pfc" not in data
        assert data["active_goals"] == []
