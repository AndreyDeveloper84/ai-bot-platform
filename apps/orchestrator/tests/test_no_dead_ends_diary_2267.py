"""DRF-2267 срез 2 — дневник и вода без тупиков; DRF-2303, DRF-2304.

Правило владельца CD §72: после завершённого шага — 1–2 кнопки следующего
шага и «Меню». Кнопки ведут в ветки, которые уже отвечают (DRF-1492):

* «Записать еду» — ``cb:welcome:food`` (приглашение прислать фото или
  название, с воротами дневника — ``global_onboarding._food_prompt_reply``);
* «Мой дневник» — ``CHIP_DIARY``; только там, где тап дойдёт до дневника
  (:func:`personal_surface.diary_is_reachable`);
* «Меню» — ``cb:menu:help``.

DRF-2303: экран «Дневник питания» давал только итоги и воду — записать еду
или прислать фото оттуда было нельзя. DRF-2304: текст меню перечислял
возможности без дневника питания и фото еды.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import FoodLogResponse, MealDeletion, WaterEntryResponse
from apps.orchestrator.personal_surface import CHIP_ANKETA, CHIP_DIARY, CHIP_WATER
from apps.skills.base import SkillContext
from apps.skills.menu.matching import CALLBACK_MENU_HELP

LOG_FOOD = "cb:welcome:food"
DIARY = CHIP_DIARY["callback"]
DIARY_TAP = "дневник питания"
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
LOG_ID = "log-2267"


def _callbacks(result: Any) -> list[str]:
    return [b["callback"] for b in (result.action_data or {}).get("buttons") or []]


@pytest.fixture
def nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True
    return settings


@pytest.fixture
def diary_open(monkeypatch):
    monkeypatch.setattr(
        "apps.orchestrator.personal_surface.personal_records_consent_open", lambda _u: True
    )
    monkeypatch.setattr("apps.consent.nutrition.diary_is_granted", lambda _u: True)


@pytest.fixture
def reachable(monkeypatch):
    """Глобальный путь: тап «Мой дневник» доходит до дневника."""
    monkeypatch.setattr("apps.orchestrator.personal_surface.diary_is_reachable", lambda: True)


def _ctx(text: str) -> SkillContext:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2267"
    bot_user.pk = 2267
    conversation = SimpleNamespace(id="conv-2267", skill_state={})
    return SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)  # type: ignore[arg-type]


def _food_log(origin: str) -> FoodLogResponse:
    return FoodLogResponse(
        log_id=LOG_ID,
        dish_name="Борщ",
        meal_type="other",
        calories=250.0,
        raw={"entry_origin": origin},
    )


# ── DRF-2303 — экран дневника ────────────────────────────────────────────


@pytest.mark.django_db(transaction=True)
class TestDiaryScreenOffersToLogFood:
    def test_today_offers_food_water_and_menu(self, nutrition_on, monkeypatch) -> None:
        from apps.orchestrator.personal_surface import render_diary
        from apps.orchestrator.tests.test_personal_surface import (
            _bot_user,
            _FakeAyla,
            _install_ayla,
            _profile,
            _summary,
            _water,
        )

        _install_ayla(
            monkeypatch,
            _FakeAyla(
                summary=_summary(),
                water=_water(),
                profile=_profile(targets_source="ayla_calculated"),
            ),
        )
        reply = render_diary(_bot_user("dead-2303"))
        assert reply.text  # положительная пара: итоги дня на месте
        assert _callbacks(reply) == [LOG_FOOD, CHIP_WATER["callback"], CALLBACK_MENU_HELP]

    def test_anketa_stays_beside_them_until_targets_exist(self, nutrition_on, monkeypatch) -> None:
        from apps.orchestrator.personal_surface import render_diary
        from apps.orchestrator.tests.test_personal_surface import (
            _bot_user,
            _FakeAyla,
            _install_ayla,
            _summary,
            _water,
        )

        _install_ayla(monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=None))
        reply = render_diary(_bot_user("dead-2303b"))
        assert _callbacks(reply) == [
            LOG_FOOD,
            CHIP_WATER["callback"],
            CHIP_ANKETA["callback"],
            CALLBACK_MENU_HELP,
        ]


# ── DRF-2304 — текст меню ────────────────────────────────────────────────


class TestMenuTextNamesTheDiary:
    def _user(self) -> Any:
        from apps.skills.menu.tests.test_marketplace import _StubUser

        return _StubUser()

    def test_open_gate_the_text_names_diary_and_photo(self, nutrition_on, monkeypatch) -> None:
        from apps.skills.menu import marketplace

        monkeypatch.setattr("apps.consent.nutrition.diary_or_health_granted", lambda _u: True)
        text, action_data = marketplace.marketplace_menu_reply(bot_user=self._user())
        assert DIARY_TAP in _callbacks(SimpleNamespace(action_data=action_data))
        assert "дневник питания" in text
        assert "фото" in text

    def test_closed_gate_the_text_does_not_promise_it(self, settings) -> None:
        from apps.skills.menu import marketplace

        settings.NUTRITION_ENABLED = False
        text, _ = marketplace.marketplace_menu_reply(bot_user=self._user())
        assert "подобрать услугу" in text  # положительная пара: перечень на месте
        assert "дневник питания" not in text


# ── вода ─────────────────────────────────────────────────────────────────


class TestWaterLoggedHasNextSteps:
    def _run(self) -> Any:
        from apps.skills.water.skill import WaterSkill

        client = Mock()

        async def _add_water(**kwargs):
            return WaterEntryResponse(
                entry_id="e-1",
                ml=250,
                water_ml=250,
                kcal=0,
                milestone_text=None,
                today_total_ml=1500,
                today_norm_ml=None,
                alcohol_recovery_hint=False,
                raw={},
            )

        client.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=client):
            return WaterSkill().handle(_ctx("стакан воды"))

    def test_diary_and_menu_on_the_global_path(self, nutrition_on, diary_open, reachable) -> None:
        result = self._run()
        assert result.action_type == "water_logged"
        assert result.action_data["ml"] == 250  # данные записи на месте
        assert _callbacks(result) == [DIARY, CALLBACK_MENU_HELP]

    def test_no_diary_chip_where_it_would_not_land(self, nutrition_on, diary_open) -> None:
        result = self._run()
        assert _callbacks(result) == [CALLBACK_MENU_HELP]


# ── еда: записано, возвращено, удалено, «Не то» ──────────────────────────


class _Catalogue:
    def __init__(self, *, logged: FoodLogResponse, expires_at: str | None = None) -> None:
        self.logged = logged
        self.expires_at = expires_at

    async def log_meal(self, **kwargs):
        return self.logged

    async def delete_meal(self, **kwargs):
        return MealDeletion(log_id=kwargs["log_id"], restore_window_expires_at=self.expires_at)

    async def restore_meal(self, **kwargs):
        return self.logged


def _clarify_turn(text: str, catalogue: _Catalogue) -> Any:
    from apps.skills.food_clarify.skill import FoodClarifySkill

    ctx = _ctx(text)
    skill = FoodClarifySkill()
    assert skill.matches(ctx), text
    with (
        patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue),
        patch("apps.skills.food_clarify.text_entry._now_utc", return_value=NOW),
    ):
        return skill.handle(ctx)


def _scanner_turn(text: str, catalogue: _Catalogue) -> Any:
    from apps.skills.food_scanner.skill import FoodScannerSkill

    with patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=catalogue):
        return FoodScannerSkill().handle(_ctx(text))


class TestFoodRepliesHaveNextSteps:
    def test_photo_logged(self, nutrition_on, diary_open, reachable) -> None:
        result = _scanner_turn(
            "cb:food:to_diary:scan-2267", _Catalogue(logged=_food_log("photo_estimated_confirmed"))
        )
        assert result.action_type == "food_logged"
        assert _callbacks(result) == [f"cb:food:entry_del:{LOG_ID}", DIARY, CALLBACK_MENU_HELP]

    def test_photo_rejected_offers_to_log_it_another_way(self, nutrition_on, diary_open) -> None:
        from apps.skills.food_scanner.skill import REJECTED_ACK

        result = _scanner_turn(
            "cb:food:reject:scan-2267", _Catalogue(logged=_food_log("photo_estimated_confirmed"))
        )
        assert result.reply_text == REJECTED_ACK
        assert _callbacks(result) == [LOG_FOOD, CALLBACK_MENU_HELP]

    def test_restored_text_entry(self, nutrition_on, diary_open, reachable) -> None:
        result = _clarify_turn(
            f"cb:food:entry_undo:{LOG_ID}", _Catalogue(logged=_food_log("text_estimated_confirmed"))
        )
        assert result.reply_text.startswith("Вернула в дневник")
        assert _callbacks(result) == [
            f"cb:food:entry_fix:{LOG_ID}",
            f"cb:food:entry_del:{LOG_ID}",
            DIARY,
            CALLBACK_MENU_HELP,
        ]

    def test_deleted_with_window(self, nutrition_on, diary_open) -> None:
        result = _clarify_turn(
            f"cb:food:entry_del:{LOG_ID}",
            _Catalogue(
                logged=_food_log("text_estimated_confirmed"),
                expires_at=(NOW + timedelta(minutes=14)).isoformat(),
            ),
        )
        assert result.action_type == "food_entry_deleted"
        assert _callbacks(result) == [f"cb:food:entry_undo:{LOG_ID}", LOG_FOOD, CALLBACK_MENU_HELP]

    def test_deleted_without_window(self, nutrition_on, diary_open) -> None:
        result = _clarify_turn(
            f"cb:food:entry_del:{LOG_ID}", _Catalogue(logged=_food_log("text_estimated_confirmed"))
        )
        assert result.reply_text == "Убрала запись из дневника."
        assert _callbacks(result) == [LOG_FOOD, CALLBACK_MENU_HELP]
