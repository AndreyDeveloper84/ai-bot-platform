"""«борщ 250» доезжает до дневника с 250 г, а не со 100 г по умолчанию (DRF-2078).

Диалог владельца: «борщ 250» → «Это про еду?» → «В дневник» → запись на
100 г. Число терялось трижды, и каждое место — своё:

1. ``parse_food_text`` читал граммы только с единицей («300г»); «борщ 250»
   давал ``dish="борщ 250", grams=None`` — 100 г по умолчанию даже на
   детерминированном пути;
2. ``execute_nutrition_tool`` исполнял ``clarify_food_entry`` на фразе
   МОДЕЛИ и её же запоминал как ``source`` для тапа «В дневник» —
   пересказ «борщ» без числа → 100 г. Докстринг защищал это как
   «нормализация помогает парсеру»; парсер и так снимает наполнители, а
   число пересказ теряет;
3. свободный текст «борщ 250» всегда уходил в модель → карточка «Это про
   еду?» → тап → оценка → подтверждение: два подтверждения вместо одного.

Красное до правки объявлено поимённо ДО прогона: 6 красных / 4 зелёных
контроля. Контроли зелены и до, и после — они стерегут, что правка не
пережала: «борщ 300г» и «2 яйца» читаются как раньше, напиток с «мл» и
блюдо без числа по-прежнему уходят модели.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import DishEstimate, FoodLogResponse
from apps.orchestrator.nutrition_global import (
    execute_nutrition_tool,
    try_handle_structured_nutrition_turn,
)
from apps.skills.base import SkillContext
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.skill import FoodClarifySkill

pytestmark = pytest.mark.django_db(transaction=True)


class _Catalogue:
    """Двойник каталога: 100 г = 50 ккал, порция без граммов — оценка."""

    def __init__(self) -> None:
        self.estimates: list[dict[str, Any]] = []
        self.logs: list[dict[str, Any]] = []

    async def estimate_dish(self, *, external_user_id, dish_name, portion_g=None):
        self.estimates.append({"dish_name": dish_name, "portion_g": portion_g})
        grams = 100.0 if portion_g is None else float(portion_g)
        factor = grams / 100.0
        return DishEstimate(
            matched_dish=dish_name,
            portion_g=grams,
            portion_estimated=portion_g is None,
            kcal=50.0 * factor,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            raw={},
        )

    async def log_meal(self, **kwargs):
        self.logs.append(kwargs)
        return FoodLogResponse(
            log_id="log-2078",
            dish_name=kwargs["dish_name"],
            meal_type=kwargs["meal_type"],
            calories=50.0 * kwargs["portion_multiplier"],
            raw={},
        )


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


@pytest.fixture(autouse=True)
def _consent():
    with patch("apps.skills.food_clarify.text_entry._consent_open", return_value=True):
        yield


@pytest.fixture
def catalogue():
    fake = _Catalogue()
    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
        yield fake


def _bot_user() -> Mock:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2078"
    return bot_user


def _conversation():
    return SimpleNamespace(id="conv-2078", skill_state={})


def _structured(text: str, conversation):
    return try_handle_structured_nutrition_turn(
        text=text,
        attachments=None,
        bot_user=_bot_user(),
        conversation=conversation,
        trace_id="t-2078",
    )


def _skill_named(name: str):
    from apps.orchestrator.nutrition_global import _skill_by_name

    skill = _skill_by_name(name)
    assert skill is not None, name
    return skill


def _skill_turn(text: str, conversation):
    skill = FoodClarifySkill()
    ctx = SkillContext(conversation=conversation, bot_user=_bot_user(), message_text=text)
    assert skill.matches(ctx), text
    return skill.handle(ctx)


# ─── 1. парсер: голое число в конце фразы — граммы ───────────────────────────


class TestParse:
    @pytest.mark.parametrize(
        ("text", "dish", "grams"),
        [
            pytest.param("борщ 250", "борщ", 250.0, id="bare-trailing-number"),
            pytest.param("суп 300 мл", "суп", 300.0, id="ml-is-a-unit"),
            pytest.param("борщ 300г", "борщ", 300.0, id="control-named-grams"),
            pytest.param("2 яйца", "2 яйца", None, id="control-leading-number-is-a-count"),
        ],
    )
    def test_grams(self, text, dish, grams) -> None:
        assert text_entry.parse_food_text(text) == text_entry.ParsedFood(dish=dish, grams=grams)


# ─── 2. инструмент модели: фраза — текст хода человека ───────────────────────


class TestClarifyToolSource:
    def test_clarify_tool_remembers_the_human_phrase_not_the_model_paraphrase(self) -> None:
        """Сторож «фраза ⊆ текст хода»: модель отдала «борщ», человек написал
        «борщ 250» — запоминается то, что написал человек."""
        conversation = _conversation()
        result = execute_nutrition_tool(
            "clarify_food_entry",
            {"food_text": "борщ"},
            bot_user=_bot_user(),
            conversation=conversation,
            trace_id="t-2078",
            message_text="борщ 250",
        )
        assert result is not None and result.action_type == "food_clarify_card"
        assert conversation.skill_state["food_text"]["source"] == "борщ 250"

    def test_diary_tap_after_clarify_keeps_the_grams(self, catalogue) -> None:
        conversation = _conversation()
        execute_nutrition_tool(
            "clarify_food_entry",
            {"food_text": "борщ"},
            bot_user=_bot_user(),
            conversation=conversation,
            trace_id="t-2078",
            message_text="борщ 250",
        )
        card = _skill_turn("cb:food:diary", conversation)

        assert catalogue.estimates == [{"dish_name": "борщ", "portion_g": 250.0}]
        assert card.action_type == "food_text_estimate_card"
        assert card.action_data["portion_g"] == 250.0
        assert card.action_data["portion_estimated"] is False
        assert "Порция — 250 г, по твоим словам." in card.reply_text


# ─── 3. ярлык до модели: «блюдо + число» → сразу карточка оценки ─────────────


class TestDishWithGramsShortcut:
    def test_dish_with_grams_skips_the_card_and_shows_the_estimate(self, catalogue) -> None:
        conversation = _conversation()
        result = _structured("борщ 250", conversation)

        assert result is not None, "ярлык не сработал — ход ушёл бы модели"
        assert result.action_type == "food_text_estimate_card"
        assert catalogue.estimates == [{"dish_name": "борщ", "portion_g": 250.0}]
        assert "Порция — 250 г, по твоим словам." in result.reply_text
        # §109 шаг 6: до подтверждения — ни одной записи.
        assert catalogue.logs == []

    def test_shortcut_estimate_is_confirmed_once(self, catalogue) -> None:
        """Одно подтверждение: карточка оценки → «✅ В дневник» → запись 250 г."""
        conversation = _conversation()
        _structured("борщ 250", conversation)
        result = _structured("cb:food:text_log", conversation)

        assert len(catalogue.logs) == 1
        assert catalogue.logs[0]["dish_name"] == "борщ"
        assert catalogue.logs[0]["portion_multiplier"] == 2.5
        assert catalogue.logs[0]["entry_origin"] == "text_estimated_confirmed"
        assert result.reply_text.startswith("Записала в дневник: борщ")

    def test_drink_with_ml_is_not_claimed_here(self, catalogue) -> None:
        """DRF-819: напиток — только log_water; ярлык еды его не перехватывает."""
        assert _structured("кофе 200 мл", _conversation()) is None
        assert catalogue.estimates == []

    def test_drink_with_ml_goes_to_log_water_instead(self) -> None:
        """Положительная стража к предыдущему: «кофе 200 мл» не просто не
        наш — его ЕСТЬ кому взять. Грамматика напитков читает фразу, и
        навык воды за инструментом ``log_water`` её примет (``matches``),
        то есть путь «модель → log_water» для неё открыт, как до правки."""
        from apps.skills.water.parser import BeverageMatch, parse_beverage

        assert isinstance(parse_beverage("кофе 200 мл"), BeverageMatch)
        water = _skill_named("water")
        assert water.matches(
            SkillContext(
                conversation=_conversation(), bot_user=_bot_user(), message_text="кофе 200 мл"
            )
        )

    def test_dish_without_a_number_is_not_claimed_here(self, catalogue) -> None:
        """Без числа — как раньше: модель, карточка «Это про еду?»."""
        assert _structured("борщ", _conversation()) is None
        assert catalogue.estimates == []
