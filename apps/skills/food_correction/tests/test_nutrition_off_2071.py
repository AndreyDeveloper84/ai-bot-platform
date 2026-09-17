"""DRF-2071 — правка под карточкой сканера закрыта единым выключателем.

Перепись входов к пробе T-E2E-13 (17.09) нашла вход контура питания, не
читающий ``NUTRITION_ENABLED``: кнопка «поправить граммы / название / БЖУ»
под карточкой сканера, отрисованной ещё при включённом контуре. При OFF
бот спрашивал «введи вес в граммах» и писал ответ в ``MemoryEntry`` —
диалог о еде через кнопку прошлого сообщения. Решение владельца 17.09:
при OFF закрыты UI, команда, callback, deep link и API.

Талия — ``handle`` (как у анкеты, дневника и воды), не хендлеры: сюда
сходятся и кнопка, и ответ на уже заданный вопрос. Каждое отрицание —
рядом с положительным контролем на том же входе при включённом флаге.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.skills.food_correction.skill import _PROMPTS, FoodCorrectionSkill
from apps.skills.food_correction.tests.test_skill import _context, _pending_context
from apps.skills.menu.marketplace import NUTRITION_UNAVAILABLE_TEXT

REMEMBER = "apps.orchestrator.memory.food.remember_correction"


@pytest.fixture
def nutrition_off(settings):
    # Перекрывает autouse ``_nutrition_contour_on`` из conftest: фикстура
    # теста применяется после autouse-фикстур пакета.
    settings.NUTRITION_ENABLED = False


class TestCorrectionButtonAnswersTheStubWhenOff:
    @pytest.mark.parametrize("field", sorted(_PROMPTS))
    def test_the_button_is_still_claimed_and_gets_the_stub(self, nutrition_off, field):
        ctx = _context(f"cb:food:correct:{field}:scan-1")
        skill = FoodCorrectionSkill()

        # ``matches`` флаг не читает — иначе ход уехал бы модели.
        assert skill.matches(ctx)
        result = skill.handle(ctx)

        assert result.reply_text == NUTRITION_UNAVAILABLE_TEXT
        assert result.meta["reply_kind"] == "food_correction_nutrition_off"
        # Вопрос не задан → ожидания ответа в состоянии разговора нет.
        assert "food_correction" not in ctx.conversation.skill_state

    def test_positive_control_the_button_asks_its_question_when_on(self):
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:grams:scan-1"))
        assert result.reply_text == _PROMPTS["grams"]
        assert result.reply_text != NUTRITION_UNAVAILABLE_TEXT


class TestAnswerToAnEarlierQuestionIsNotRememberedWhenOff:
    """Вопрос задан при ON, флаг выключили, человек отвечает «плов»."""

    def test_the_answer_gets_the_stub_and_memory_is_not_written(self, nutrition_off):
        ctx = _pending_context("плов", field="name")
        skill = FoodCorrectionSkill()
        assert skill.matches(ctx)

        with patch(REMEMBER) as remember:
            result = skill.handle(ctx)

        assert result.reply_text == NUTRITION_UNAVAILABLE_TEXT
        remember.assert_not_called()

    def test_positive_control_the_answer_is_remembered_when_on(self):
        from apps.orchestrator.memory import food as food_memory

        with patch(REMEMBER, return_value=food_memory.Outcome.WRITTEN) as remember:
            result = FoodCorrectionSkill().handle(_pending_context("плов", field="name"))

        remember.assert_called_once()
        assert result.reply_text != NUTRITION_UNAVAILABLE_TEXT
