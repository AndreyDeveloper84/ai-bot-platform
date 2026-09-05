"""Tests for GET /customer/wellness/today — nutrition composition.

Covers:
- Happy path: daily_summary + get_water_today compose into WellnessToday
- Water ml→glasses conversion
- Graceful degradation: summary fails / water fails / both fail
- pfc omitted when summary unavailable
- active_goals read from Ayla's goal layer (DRF-1476), with the three
  states kept apart: a goal, no goal, and «could not ask»
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


#: A decision-context document with no goal chosen. This is the DEFAULT
#: for every test in this file, so the pre-existing assertions that
#: `active_goals == []` keep their original meaning — «Ayla was asked,
#: and answered: no goal» — instead of silently becoming «the goal read
#: blew up», which is a different state entirely since DRF-1476.
def _no_goal_doc() -> dict[str, Any]:
    return {
        "version": 1,
        "known": {"goal": None},
        "missing": [],
        "suggestions": [],
        "intents": [],
    }


def _goal_doc(
    *,
    goal_key: str | None = None,
    goal_text: str | None = None,
    selected_at: str = "2026-09-05T09:00:00+00:00",
    suggestions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "known": {
            "goal": {
                "goal_key": goal_key,
                "goal_text": goal_text,
                "selected_at": selected_at,
                "source_channel": "miniapp",
            }
        },
        "missing": [],
        "suggestions": suggestions or [],
        "intents": [],
    }


@pytest.fixture(autouse=True)
def goals_stub():
    """Patch the goal-layer read; default = «asked, no goal».

    Tests that care reassign `.return_value` / `.side_effect`.
    """
    with patch("apps.integrations.ayla.goals_client.fetch_decision_context") as m:
        m.return_value = _no_goal_doc()
        yield m


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


class TestWellnessTodayActiveGoals:
    """The defect DRF-1476 closed, from both sides.

    Owner walkthrough 2026-09-05: «Позаботиться о коже лица» was chosen
    and active on the goal screen, and this dashboard offered to choose
    a goal. `active_goals` was a hardcoded `[]`.

    Every negative assertion below is paired with a positive one on the
    SAME data — «the CTA is gone» is only worth asserting next to «the
    goal is there», or the field could be gone for everyone.
    """

    def test_curated_goal_resolves_its_label_from_the_same_document(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        # Ayla stores a curated pick as goal_key with goal_text=None
        # (goals/api.py writes one or the other), so the human label has
        # to come from `suggestions` — which travels in the same doc.
        goals_stub.return_value = _goal_doc(
            goal_key="face_skin",
            goal_text=None,
            suggestions=[
                {"key": "hair", "label": "Волосы"},
                {"key": "face_skin", "label": "Позаботиться о коже лица"},
            ],
        )
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        goals = resp.json()["active_goals"]
        # POSITIVE: the goal the person chose is actually here, by name.
        assert len(goals) == 1
        assert goals[0]["title"] == "Позаботиться о коже лица"
        # NEGATIVE (paired): so the frontend cannot show «Выбери цель».
        assert goals != []

    def test_free_text_goal_uses_the_persons_own_wording(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        goals_stub.return_value = _goal_doc(goal_text="Спать по восемь часов")
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.json()["active_goals"][0]["title"] == "Спать по восемь часов"

    def test_deactivated_option_falls_back_to_the_key_never_invents(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        # The GoalOption was switched off, so it is not in `suggestions`.
        # A slug is ugly; a made-up title would be a lie.
        goals_stub.return_value = _goal_doc(goal_key="retired_key", suggestions=[])
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        goals = resp.json()["active_goals"]
        assert len(goals) == 1
        assert goals[0]["title"] == "retired_key"

    def test_no_goal_returns_empty_list_so_the_cta_still_shows(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        """The positive guard for the fix — the CTA must survive.

        Without this, «the goal arrives» could be satisfied by a change
        that shows «Моя цель» to everyone, including people who have
        never chosen anything.
        """
        goals_stub.return_value = _no_goal_doc()
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        data = resp.json()
        # Present AND empty: the frontend distinguishes these two facts.
        assert "active_goals" in data
        assert data["active_goals"] == []

    def test_progress_pct_is_never_invented(self, client: Client, bot_user: BotUser, goals_stub):
        """Ayla stores no progress; 0 % under a live goal is the same lie."""
        goals_stub.return_value = _goal_doc(goal_text="Меньше стресса")
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        goal = resp.json()["active_goals"][0]
        assert "progress_pct" not in goal
        # Paired positive: the goal itself did arrive, so the absence
        # above is about progress and not about an empty payload.
        assert goal["title"] == "Меньше стресса"

    @pytest.mark.parametrize(
        ("days_ago", "expected_week"),
        [(0, 1), (6, 1), (7, 2), (15, 3), (70, 11)],
    )
    def test_week_num_counts_real_weeks_since_selection(
        self, client: Client, bot_user: BotUser, goals_stub, days_ago, expected_week
    ):
        from datetime import timedelta

        from django.utils import timezone as dj_tz

        selected = dj_tz.now() - timedelta(days=days_ago)
        goals_stub.return_value = _goal_doc(goal_text="Цель", selected_at=selected.isoformat())
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.json()["active_goals"][0]["week_num"] == expected_week

    def test_unparsable_selected_at_omits_week_rather_than_claiming_week_one(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        goals_stub.return_value = _goal_doc(goal_text="Цель", selected_at="not-a-date")
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        goal = resp.json()["active_goals"][0]
        assert "week_num" not in goal
        assert goal["title"] == "Цель"  # paired positive


class TestWellnessTodayGoalsDegradation:
    """A goal-layer outage must not reprint the defect."""

    @pytest.mark.parametrize("exc_name", ["GoalsUnavailable", "GoalsConfigError"])
    def test_goals_outage_omits_the_key_instead_of_claiming_no_goal(
        self, client: Client, bot_user: BotUser, goals_stub, exc_name
    ):
        from apps.integrations.ayla import goals_client

        exc_cls = getattr(goals_client, exc_name)
        goals_stub.side_effect = exc_cls("down")
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        data = resp.json()
        # NEGATIVE: no `[]`, which the frontend would read as «no goal»
        # and answer with «Выбери цель» — the bug, restored by outage.
        assert "active_goals" not in data
        # POSITIVE (paired, same response): the rest of the dashboard is
        # untouched, so the missing key is a goal-read failure and not a
        # blank payload.
        assert data["calories_eaten"] == 1240
        assert data["water_glasses_eaten"] == 4

    def test_unexpected_goals_error_degrades_and_never_500s(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        goals_stub.side_effect = RuntimeError("boom")
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        assert "active_goals" not in resp.json()
        assert resp.json()["calories_eaten"] == 1240

    def test_nutrition_outage_does_not_take_the_goal_with_it(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        """The three reads degrade independently, in both directions."""
        goals_stub.return_value = _goal_doc(goal_text="Позаботиться о коже лица")
        with _patch_nutrition(
            summary=NutritionUnavailableError("down"),
            water=NutritionUnavailableError("down"),
        ):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        data = resp.json()
        assert data["calories_eaten"] == 0
        assert data["active_goals"][0]["title"] == "Позаботиться о коже лица"
