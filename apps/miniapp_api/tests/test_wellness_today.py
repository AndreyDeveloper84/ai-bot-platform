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


@dataclass
class _FakeProfile:
    """Только то, что читает ручка. Остальные поля профиля ей не нужны."""

    health_flags: dict = None  # type: ignore[assignment]


_NO_PROFILE = object()


def _patch_nutrition(*, summary, water, profile=_NO_PROFILE):
    """Patch get_nutrition_client to return an async-method stub.

    `summary` / `water` / `profile` — либо значение (вернётся), либо
    экземпляр исключения (бросится), чтобы гонять ветки деградации.
    По умолчанию профиль — «анкеты нет» (`None`): так ведёт себя
    большинство людей на пилоте, и числа при этом показываются.
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
    resolved = None if profile is _NO_PROFILE else profile
    if isinstance(resolved, Exception):
        client.get_profile = AsyncMock(side_effect=resolved)
    else:
        client.get_profile = AsyncMock(return_value=resolved)
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

    def test_zero_water_norm_omits_the_target(self, client: Client, bot_user: BotUser):
        """`norm_ml=0` — нормы нет, и выдумывать её нечем.

        Здесь стояла проверка ровно обратного: что вместо нуля уйдёт
        константа 8 («default, not 0»). Восемь стаканов — число ниоткуда:
        ни принятого плана, ни расчёта, ни ответа Ayla за ним не стоит, а
        человеку оно показывалось как ЕГО дневная цель, с процентом
        выполнения и шкалой.

        Стража парная (``negative_assert_guard``, DRF-1411): рядом с
        отрицательной проверкой — положительная на тех же данных.
        Выпитое (``water_glasses_eaten``) настоящее и уходит всегда;
        потерять правду заодно с выдумкой было бы вторым дефектом, а не
        починкой. А ``test_full_composition`` выше держит вторую половину
        пары: с НАСТОЯЩЕЙ нормой ключ на месте.
        """
        with _patch_nutrition(
            summary=_FakeSummary(),
            water=_FakeWater(total_ml=750, norm_ml=0),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        data = resp.json()
        # POSITIVE ВПЕРЕДИ: тело действительно содержит срез воды и срез
        # питания, поэтому «ключа цели нет» ниже — утверждение про ЭТОТ
        # ответ, а не про пустой.
        assert data["water_glasses_eaten"] == 3  # 750 / 250
        assert data["calories_eaten"] == 1240
        assert data["calories_target"] == 2100
        # NEGATIVE: цели нет — ключа нет. Ни 8, ни 0.
        assert "water_glasses_target" not in data


class TestWellnessTodayGracefulDegradation:
    """A read that FAILED omits its keys — it does not send zeros.

    DRF-1546. Zeros used to stand in for a failed read, and the dashboard
    had no way to tell «0 / 8 стаканов» (nothing logged) from «we could
    not ask Ayla». A person who had drunk four glasses was shown an empty
    row every time the nutrition service hiccuped.

    Same treatment as ``active_goals`` (DRF-1476) and ``weekly_progress``:
    absence cannot be misread the way a zero can.

    Every «the key is gone» assertion below is paired with a «the key is
    there, with real numbers» case in
    :class:`TestWellnessTodayHappyPath` and with the neighbouring slice
    here — a change that dropped the fields for everyone would pass the
    first half and fail the second.
    """

    def test_summary_unavailable_omits_calories_and_keeps_water(
        self, client: Client, bot_user: BotUser
    ):
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
        # Presence first (DRF-1411): the body really has a hydration
        # slice, so «the nutrition keys are gone» below is a statement
        # about this response and not about an empty one.
        assert data["water_glasses_eaten"] == 4
        assert data["water_glasses_target"] == 8
        # The nutrition slice is ABSENT — not zeroed.
        assert "calories_eaten" not in data
        assert "calories_target" not in data
        assert "pfc" not in data

    def test_water_unavailable_omits_hydration_and_keeps_calories(
        self, client: Client, bot_user: BotUser
    ):
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
        # Paired positive: calories survive the hydration outage.
        assert data["calories_eaten"] == 1240
        assert data["pfc"]["protein_g"] == 65
        # Гидратация ОТСУТСТВУЕТ целиком: ноль стаканов — это «сегодня
        # ещё не пил», а не «чтение упало», и подменять одно другим
        # нельзя ни в какую сторону.
        assert "water_glasses_eaten" not in data
        assert "water_glasses_target" not in data

    def test_both_unavailable_returns_200_with_neither_slice(
        self, client: Client, bot_user: BotUser
    ):
        with _patch_nutrition(
            summary=NutritionUnavailableError("down"),
            water=NutritionUnavailableError("down"),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        # Dashboard resilience: 200, never 502 — but with nothing invented.
        assert resp.status_code == 200
        data = resp.json()
        # Presence first (DRF-1411). A 200 is not a guard — an empty body
        # is a perfectly good 200 — so prove the response has content the
        # view built: the greeting name and the independent goal read.
        assert data["display_name"] == "Анна К."
        assert data["active_goals"] == []
        assert "calories_eaten" not in data
        assert "water_glasses_eaten" not in data
        assert "pfc" not in data

    def test_a_genuinely_empty_day_still_reports_its_zeros(self, client: Client, bot_user: BotUser):
        """The guard on the fix.

        Zero is a real value and must keep arriving — otherwise «omit on
        failure» would have quietly become «never send», and the
        dashboard would say «Не удалось загрузить» to every new customer.
        """
        with _patch_nutrition(
            summary=_FakeSummary(calories_total=0, protein_g=0, fat_g=0, carbs_g=0),
            water=_FakeWater(total_ml=0),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["calories_eaten"] == 0
        assert data["calories_target"] == 2100
        assert data["water_glasses_eaten"] == 0
        assert data["water_glasses_target"] == 8
        assert data["pfc"] == {"protein_g": 0, "fat_g": 0, "carbs_g": 0}


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
        # Paired positive FIRST: the goal itself arrived, so the absence
        # below is about progress and not about an empty payload. Starve
        # this test of data and it fails here, by name.
        assert goal["title"] == "Меньше стресса"
        assert "progress_pct" not in goal

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
        assert goal["title"] == "Цель"  # paired positive, ahead of the absence
        assert "week_num" not in goal


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
        # POSITIVE (paired, same response, ahead of the absence): the
        # rest of the dashboard is untouched, so the missing key below is
        # a goal-read failure and not a blank payload.
        assert data["calories_eaten"] == 1240
        assert data["water_glasses_eaten"] == 4
        # NEGATIVE: no `[]`, which the frontend would read as «no goal»
        # and answer with «Выбери цель» — the bug, restored by outage.
        assert "active_goals" not in data

    def test_unexpected_goals_error_degrades_and_never_500s(
        self, client: Client, bot_user: BotUser, goals_stub
    ):
        goals_stub.side_effect = RuntimeError("boom")
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        data = resp.json()
        # Presence ahead of absence — a blank payload must fail here.
        assert data["calories_eaten"] == 1240
        assert "active_goals" not in data

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
        # Presence first (DRF-1411): the goal really arrived, so «the
        # nutrition key is gone» is about this response.
        assert data["active_goals"][0]["title"] == "Позаботиться о коже лица"
        assert "calories_eaten" not in data


class TestWellnessTodayInventedCalorieGoal:
    """Цель калорий, которой у человека нет, не подставляется — как у воды.

    Водную половину этого вылечили раньше (``test_zero_water_norm_omits_
    the_target`` выше): подставленная константа «8 стаканов» показывалась
    человеку как ЕГО норма. Калории носили ту же болезнь дальше и в более
    заметном месте: ``calories_goal`` приезжает из
    ``NUTRITION_DEFAULT_CALORIES_GOAL`` — плоской константы на всех, — и
    человеку без анкеты питания рисовались «1240 / 2000 ккал · 62 %», где
    знаменатель не имеет к нему отношения, а процент выполнения считается
    от чужого числа.

    Ноль с той стороны означает «цели нет». Тогда не уходит ни цель, ни
    строка БЖУ — по клиентскому контракту §11.1 она ЦЕЛЕВАЯ («pfc
    undefined — анкета не пройдена, строка скрыта»), и гаснуть они обязаны
    вместе: строка БЖУ рядом с отсутствующей целью читается как «анкета
    есть, цели нет», то есть как третье состояние, которого не бывает.

    Съеденное при этом не теряется ни в одном случае — потерять правду
    заодно с выдумкой было бы вторым дефектом, а не починкой.
    """

    def test_zero_calorie_goal_omits_the_target_and_the_pfc_row(
        self, client: Client, bot_user: BotUser
    ):
        with _patch_nutrition(
            summary=_FakeSummary(calories_goal=0),
            water=_FakeWater(),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        assert resp.status_code == 200
        data = resp.json()
        # POSITIVE ВПЕРЕДИ (DRF-1411): срез питания в ответе ЕСТЬ, и вода
        # со своей настоящей нормой тоже — значит отрицания ниже про этот
        # ответ, а не про пустой и не про упавшее чтение.
        assert data["calories_eaten"] == 1240
        assert data["water_glasses_target"] == 8
        # NEGATIVE: цели нет — нет ни ключа цели, ни целевой строки БЖУ.
        # Ни 2000, ни 0.
        assert "calories_target" not in data
        assert "pfc" not in data

    def test_a_real_goal_keeps_the_target_and_the_pfc_row(self, client: Client, bot_user: BotUser):
        """Вторая половина пары: с настоящей целью оба ключа на месте.

        Без неё «ключей нет» выше прошло бы и в мире, где ручка перестала
        отдавать питание вовсе.
        """
        with _patch_nutrition(
            summary=_FakeSummary(calories_goal=1900),
            water=_FakeWater(),
        ):
            resp = client.get(
                _url(),
                HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
            )

        data = resp.json()
        assert data["calories_target"] == 1900
        assert data["pfc"] == {"protein_g": 65, "fat_g": 40, "carbs_g": 121}


class TestWellnessTodayDiaryEntries:
    """Записи дня — четвёртый ключ той же ручки (DRF-1329, план дневника).

    Три различимых состояния вместо двух, и различие несёт КЛЮЧ:

    * ключа нет            → «прочитать не удалось»;
    * ключ есть, список пуст → «за день ничего не записано»;
    * ключ есть, список полон → записи.

    Свести первые два — то же самое, что показать «0 из 0 ккал» при
    отказе чтения: человек видит «сегодня ты ничего не ел» там, где
    правда — «мы не смогли спросить».
    """

    ENTRY = {
        "id": "fl-1",
        "dish_name": "Овсянка с ягодами",
        "calories": 320,
        "protein_g": 11.2,
        "fat_g": 6.4,
        "carbs_g": 54.0,
        "meal_type": "breakfast",
        "logged_at": "2026-09-08T05:31:00Z",
    }

    def test_entries_travel_verbatim(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(summary=_FakeSummary(entries=[self.ENTRY]), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        data = resp.json()
        # Дословно: ни переименования, ни пересчёта. БЖУ записи —
        # настоящее и приезжает вместе с ней, поэтому считать его
        # заново не из чего и незачем.
        assert data["entries"] == [self.ENTRY]

    def test_an_empty_day_sends_an_empty_list_not_a_missing_key(
        self, client: Client, bot_user: BotUser
    ):
        with _patch_nutrition(summary=_FakeSummary(entries=[]), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        data = resp.json()
        # Положительная стража: ключ ЕСТЬ…
        assert "entries" in data
        # …и он пуст. Это ответ «за день ничего не записано».
        assert data["entries"] == []
        # Соседние ключи живы — половина не деградировала.
        assert data["calories_eaten"] == 1240

    def test_a_failed_read_omits_the_key_instead_of_claiming_an_empty_day(
        self, client: Client, bot_user: BotUser
    ):
        from apps.integrations.ayla.nutrition_client import NutritionUnavailableError

        with _patch_nutrition(summary=NutritionUnavailableError("boom"), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        data = resp.json()
        # Отсутствие: пустого списка тут быть НЕ должно — он означал бы
        # «мы спросили, и за день пусто».
        assert "entries" not in data
        assert "calories_eaten" not in data
        # Положительная стража к тому же ответу: вода не пострадала.
        assert data["water_glasses_eaten"] == 4

    def test_the_three_states_are_pairwise_distinguishable(self, client: Client, bot_user: BotUser):
        """Сведение любых двух состояний обязано краснить этот тест."""
        from apps.integrations.ayla.nutrition_client import NutritionUnavailableError

        seen = []
        for summary in (
            _FakeSummary(entries=[self.ENTRY]),
            _FakeSummary(entries=[]),
            NutritionUnavailableError("boom"),
        ):
            with _patch_nutrition(summary=summary, water=_FakeWater()):
                resp = client.get(
                    _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
                )
            data = resp.json()
            seen.append("absent" if "entries" not in data else f"list:{len(data['entries'])}")

        assert seen == ["list:1", "list:0", "absent"]
        assert len(set(seen)) == 3

    def test_no_entries_field_from_the_source_is_not_a_crash(
        self, client: Client, bot_user: BotUser
    ):
        # `_FakeSummary.entries` по умолчанию None — так же ведёт себя
        # клиент, если источник поля не прислал вовсе. Ключ уходит
        # пустым списком: чтение УДАЛОСЬ, просто записей нет.
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater()):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        data = resp.json()
        assert data["entries"] == []


class TestWellnessTodayNumbersHidden:
    """Прятать ли числа — производный признак, а не диагноз (§10 ED Mode).

    Наружу уходит ОДИН булев. Сырых `health_flags` в ответе нет и быть
    не должно: клиенту нужно знать «прятать ли цифру», а не «что с
    человеком». Раз поля нет, его нельзя ни залогировать, ни отправить
    дальше, ни прочитать в консоли браузера.
    """

    def test_ed_flag_hides_the_numbers(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            summary=_FakeSummary(),
            water=_FakeWater(),
            profile=_FakeProfile(health_flags={"eating_disorder": True}),
        ):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        data = resp.json()
        assert data["nutrition_numbers_hidden"] is True

    def test_a_person_without_the_flag_sees_numbers(self, client: Client, bot_user: BotUser):
        with _patch_nutrition(
            summary=_FakeSummary(),
            water=_FakeWater(),
            profile=_FakeProfile(health_flags={"pregnancy": True}),
        ):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        data = resp.json()
        # Другой флаг — не этот. Спрятать числа беременной спека не просит.
        assert data["nutrition_numbers_hidden"] is False

    def test_no_profile_at_all_is_an_answer_not_a_failure(self, client: Client, bot_user: BotUser):
        # `get_profile` отдаёт None, когда анкеты нет вовсе: спросили и
        # узнали, что профиля нет. Это НЕ отказ чтения.
        with _patch_nutrition(summary=_FakeSummary(), water=_FakeWater(), profile=None):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        data = resp.json()
        assert data["nutrition_numbers_hidden"] is False

    def test_a_failed_profile_read_omits_the_key_so_the_screen_fails_closed(
        self, client: Client, bot_user: BotUser
    ):
        from apps.integrations.ayla.nutrition_client import NutritionUnavailableError

        with _patch_nutrition(
            summary=_FakeSummary(),
            water=_FakeWater(),
            profile=NutritionUnavailableError("boom"),
        ):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        data = resp.json()
        # Отсутствие: `False` тут быть НЕ должно — это превратило бы
        # «не смогли спросить» в разрешение показать калории тому, кому
        # спека их показывать запрещает.
        assert "nutrition_numbers_hidden" not in data
        # Положительная стража к тому же ответу: остальные половины живы,
        # то есть чтение профиля деградирует само по себе.
        assert data["calories_eaten"] == 1240
        assert data["water_glasses_eaten"] == 4

    def test_an_unexpected_profile_error_degrades_and_never_500s(
        self, client: Client, bot_user: BotUser
    ):
        with _patch_nutrition(
            summary=_FakeSummary(), water=_FakeWater(), profile=RuntimeError("kaboom")
        ):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        assert resp.status_code == 200
        assert "nutrition_numbers_hidden" not in resp.json()

    def test_the_raw_diagnosis_never_crosses_the_boundary(self, client: Client, bot_user: BotUser):
        """152-ФЗ: наружу идёт следствие, а не специальная категория."""
        with _patch_nutrition(
            summary=_FakeSummary(),
            water=_FakeWater(),
            profile=_FakeProfile(health_flags={"eating_disorder": True, "pregnancy": True}),
        ):
            resp = client.get(
                _url(), HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id)
            )
        body = resp.content.decode()
        # Присутствие: производный признак есть…
        assert '"nutrition_numbers_hidden": true' in body.replace("'", '"')
        # …отсутствие: ни имени флага, ни контейнера, ни соседних диагнозов.
        assert "eating_disorder" not in body
        assert "health_flags" not in body
        assert "pregnancy" not in body
