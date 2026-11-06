"""DRF-2766 — «Без чисел» в чате: бот сам не называет калории, БЖУ и цели.

Решение владельца 04.10: добровольный режим отображения «на ВСЕХ экранах», и
чат — один из них (подтверждено главным окном 04.10). Записи дневника режим
не трогает: запись ложится с числами, реплика о ней звучит без чисел.

Что заперто — на каждой поверхности пара «по умолчанию с числами / при
выборе без чисел»:

* карточка оценки текстом: блюдо и порция есть, ккал/БЖУ/«Оценка ИИ» нет;
* «Записала в дневник», «Исправила», «Вернула» — без числа, запись легла;
* карточка фото: блюдо и порция есть, ккал/БЖУ нет;
* отчёт за день: число записей и вода как факт; ни ккал, ни БЖУ, ни «из N»,
  ни ремарки, ни комментария ИИ; блюда — без калорий.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.integrations.ayla import DishEstimate, ScanResponse
from apps.nutrition_proactive import render
from apps.nutrition_proactive.tests import test_render as render_fx
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.tests.test_ai_estimate_2761 import (
    LOG_ID,
    _card,
    _MissWithEstimate,
)
from apps.skills.food_clarify.tests.test_text_entry import _turn
from apps.skills.food_scanner.skill import _format_scan_card

HIDDEN = {"numbers_hidden": True}


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


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
def conversation():
    return SimpleNamespace(id="conv-2766", skill_state={})


@pytest.fixture
def hidden():
    with patch("apps.nutrition_proactive.prefs.get_prefs", return_value=dict(HIDDEN)):
        yield


def _no_numbers(text: str) -> None:
    assert "ккал" not in text
    assert text_entry.AI_ESTIMATE_MARK not in text
    for macro in ("Б ", "Ж ", "У "):
        assert f"· {macro}" not in text


# ─── карточка оценки текстом ─────────────────────────────────────────────────


def _estimate(**over) -> DishEstimate:
    base = DishEstimate(
        matched_dish="борщ",
        portion_g=300.0,
        portion_estimated=False,
        kcal=147.0,
        protein_g=5.0,
        fat_g=7.0,
        carbs_g=20.0,
        raw={"portion_source": "provider"},
    )
    return replace(base, **over)


class TestTheTextCard:
    def test_by_default_the_card_names_calories_and_macros(self) -> None:
        card = text_entry.render_estimate_card(_estimate())
        assert "Примерно 147 ккал · Б 5 · Ж 7 · У 20" in card

    def test_numbers_hidden_keeps_dish_and_portion_and_drops_numbers(self) -> None:
        card = text_entry.render_estimate_card(_estimate(), hide_numbers=True)
        assert "Я распознала так: борщ." in card
        assert "Порция — 300 г, по твоим словам." in card
        assert "Записать в дневник?" in card
        _no_numbers(card)

    def test_numbers_hidden_drops_the_ai_estimate_too(self) -> None:
        estimate = _estimate(kcal=None, protein_g=None, fat_g=None, carbs_g=None)
        estimate = replace(estimate, kcal_ai_estimate=750.0)
        assert text_entry.AI_ESTIMATE_MARK in text_entry.render_estimate_card(estimate)

        card = text_entry.render_estimate_card(estimate, hide_numbers=True)
        assert "Я распознала так: борщ." in card
        _no_numbers(card)


class TestTheCardThroughTheSkill:
    """Путь навыка передаёт выбор в карточку (а не только функция умеет)."""

    def test_by_default_the_skill_card_carries_the_ai_mark(self, conversation, consent) -> None:
        card = _card(conversation, "зыбзик 300г", _MissWithEstimate())
        assert text_entry.AI_ESTIMATE_MARK in card

    def test_numbers_hidden_the_skill_card_has_no_numbers(
        self, conversation, consent, hidden
    ) -> None:
        card = _card(conversation, "зыбзик 300г", _MissWithEstimate())
        assert "Я распознала так: зыбзик." in card
        _no_numbers(card)


# ─── реплики о записи ─────────────────────────────────────────────────────────


class TestTheReplies:
    def test_logged_without_the_number(self, conversation, consent, hidden) -> None:
        catalogue = _MissWithEstimate(verified_kcal=150.0)
        _card(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, text_entry.CB_LOG, catalogue)

        assert catalogue.logs, "запись не ушла в каталог"
        assert result.reply_text == "Записала в дневник: зыбзик."

    def test_logged_twin_by_default_the_number_is_said(self, conversation, consent) -> None:
        catalogue = _MissWithEstimate(verified_kcal=150.0)
        _card(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, text_entry.CB_LOG, catalogue)

        assert result.reply_text == "Записала в дневник: зыбзик — 150 ккал."

    def test_an_ai_estimated_entry_is_logged_without_the_mark(
        self, conversation, consent, hidden
    ) -> None:
        catalogue = _MissWithEstimate()
        _card(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, text_entry.CB_LOG, catalogue)

        assert result.reply_text == "Записала в дневник: зыбзик."

    def test_fixed_without_the_number(self, conversation, consent, hidden) -> None:
        catalogue = _MissWithEstimate(verified_kcal=150.0)
        _turn(conversation, f"cb:food:entry_fix:{LOG_ID}", catalogue)

        result = _turn(conversation, "200", catalogue)

        assert catalogue.updates[0]["portion_multiplier"] == 2.0
        assert result.reply_text == "Исправила: зыбзик."

    def test_restored_without_the_number(self, conversation, consent, hidden) -> None:
        catalogue = _MissWithEstimate(verified_kcal=150.0)
        _turn(conversation, f"cb:food:entry_del:{LOG_ID}", catalogue)

        result = _turn(conversation, f"cb:food:entry_undo:{LOG_ID}", catalogue)

        assert catalogue.restores, "возврат не ушёл в каталог"
        assert result.reply_text == "Вернула в дневник: зыбзик."


# ─── карточка фото ────────────────────────────────────────────────────────────


def _scan() -> ScanResponse:
    return ScanResponse(
        scan_id="scan-2766",
        dish_name="Борщ",
        confidence=0.9,
        portion_g=300,
        nutrition={
            "calories": 250,
            "protein_g": 12,
            "fat_g": 8,
            "carbs_g": 32,
            "portion_source": "provider",
        },
        provider="test",
        raw={},
    )


class TestThePhotoCard:
    def test_by_default_the_photo_card_names_calories(self) -> None:
        assert "250 ккал · Б 12 · Ж 8 · У 32" in _format_scan_card(_scan())

    def test_numbers_hidden_keeps_dish_and_portion(self) -> None:
        card = _format_scan_card(_scan(), hide_numbers=True)
        assert "Узнала: Борщ." in card
        assert "Примерно 300 г." in card
        _no_numbers(card)


# ─── отчёт за день ────────────────────────────────────────────────────────────


# Состояние «ориентир ЕСТЬ» — фикстуры ``test_render`` (они в списке
# ``nutrition_target_guard``): здесь проверяется, что режим снимает «из N».
_PROFILE = render_fx.profile()
_SUMMARY = render_fx.summary(
    entries=[
        {"dish_name": "борщ", "calories": 147},
        {"dish_name": "шакшука", "calories": None, "ai_calories": 300},
    ],
    ai_comment="Сегодня 1500 ккал — ровный день.",
)
_WATER = render_fx.water()


class TestTheDailyReport:
    def test_by_default_the_report_has_numbers_targets_remark_and_comment(self) -> None:
        text = render.render_daily_report(_SUMMARY, _WATER, _PROFILE, include_entries=True)
        assert "Калории: 1500 из 1900 ккал." in text
        assert "Вода: 1600 из 2000 мл." in text
        assert "• борщ — 147 ккал" in text
        assert "Сегодня 1500 ккал — ровный день." in text

    def test_numbers_hidden_keeps_the_day_and_drops_every_number_about_food(self) -> None:
        text = render.render_daily_report(
            _SUMMARY, _WATER, _PROFILE, include_entries=True, hide_numbers=True
        )
        assert text.startswith("Итоги дня по питанию.")
        assert "Записей в дневнике сегодня: 2." in text
        assert "Вода: 1600 мл." in text
        assert "• борщ" in text
        assert "• шакшука" in text
        _no_numbers(text)
        for gone in ("Калории", "Белки", "Жиры", "Углеводы", " из ", "ориентир", "1500"):
            assert gone not in text

    def test_numbers_hidden_drops_the_ai_comment_whole(self) -> None:
        text = render.render_daily_report(_SUMMARY, _WATER, _PROFILE, hide_numbers=True)
        assert "Записей в дневнике сегодня: 2." in text
        assert "ровный день" not in text
