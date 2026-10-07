"""DRF-2822 — карточка «без расчёта» называет причину, когда каталог её назвал.

Решение владельца 06.10.2026 (handoff Q4): «Сейчас не удалось…» — только для
настоящего сбоя, «…выключена» — когда ИИ-оценка выключена. Причину знает один
каталог и отдаёт её полем ``kcal_ai_status`` в ``food-estimate``; бот сам её
не выводит. Узлы:

* r1 — на каждый статус свой текст: ``unavailable`` и ``disabled`` — слова
  владельца, остальные — нейтральный v1;
* r2 — каталог без поля и незнакомое значение — нейтральный v1;
* r3 — «Без чисел»: о калориях ни слова при любом статусе;
* r4 — одно блюдо, живой путь навыка: статус доходит до карточки;
* r5 — несколько позиций: причина названа, только когда она у всех одна;
* r6 — число есть — статус карточку не меняет.

Статусы здесь СИНТЕТИЧЕСКИЕ: каталог поле ещё не выложил. Живая фикстура
провода (``food_estimate_ai_live_2761.json``) снимается с настоящего ответа
после выкладки каталога — отдельным шагом.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.integrations.ayla import DishEstimate, FoodNotRecognizedError
from apps.integrations.ayla.nutrition_client import KCAL_AI_STATUSES
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.tests.test_text_entry import _Catalogue, _ctx, _turn
from apps.skills.food_clarify.text_entry import ParsedFood

NEUTRAL = "Калорийность не рассчитана. Записать без расчёта?"
FAILED = "Сейчас не удалось рассчитать калорийность. Записать без расчёта?"
SWITCHED_OFF = "ИИ-оценка калорийности выключена. Записать без расчёта?"

#: Какой вопрос задаёт карточка на каждый статус каталога.
QUESTION_BY_STATUS = {
    "unavailable": FAILED,
    "disabled": SWITCHED_OFF,
    "not_attempted": NEUTRAL,
    "not_permitted": NEUTRAL,
    "not_applicable": NEUTRAL,
    "declined": NEUTRAL,
    "estimated": NEUTRAL,
}


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


@pytest.fixture
def consent():
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ) as opened,
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield opened


@pytest.fixture
def conversation():
    return SimpleNamespace(id="conv-2822", skill_state={})


class _Unpriced(_Catalogue):
    """Каталог без чисел: у каждого блюда — свой статус оценки ИИ.

    ``statuses`` — блюдо → статус; блюда вне словаря каталог «не узнаёт».
    ``ai`` — число ИИ для всех блюд (по умолчанию его нет).
    """

    def __init__(self, statuses: dict[str, str | None], *, ai: float | None = None) -> None:
        super().__init__()
        self.statuses = statuses
        self.ai = ai

    async def estimate_dish(
        self, *, external_user_id, dish_name, portion_g=None, ai_estimate_allowed=None
    ):
        self.estimates.append({"dish_name": dish_name, "portion_g": portion_g})
        if dish_name not in self.statuses:
            raise FoodNotRecognizedError("dish_not_found")
        return DishEstimate(
            matched_dish=dish_name,
            portion_g=100.0 if portion_g is None else float(portion_g),
            portion_estimated=portion_g is None,
            kcal=None,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            raw={"portion_source": "unknown" if portion_g is None else "provider"},
            kcal_ai_estimate=self.ai,
            kcal_ai_status=self.statuses[dish_name],
        )


def _items_card(conversation, catalogue: _Catalogue, *dishes: str) -> str:
    context = _ctx(conversation, "")
    positions = [ParsedFood(dish=dish, grams=None) for dish in dishes]
    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue):
        return text_entry.show_items(context, positions).reply_text


class TestTheQuestion:
    def test_r1_the_table_covers_every_status_of_the_contract(self) -> None:
        assert set(QUESTION_BY_STATUS) == KCAL_AI_STATUSES

    @pytest.mark.parametrize(("status", "question"), sorted(QUESTION_BY_STATUS.items()))
    def test_r1_each_status_asks_its_own_question(self, status: str, question: str) -> None:
        card = text_entry.render_unpriced_card([("зыбзик", 300.0)], status=status)

        assert card == f"{question}\nЗапишу как есть: «зыбзик» (300 г)."

    @pytest.mark.parametrize("status", [None, "", "unparsed", "UNAVAILABLE", "no_consent"])
    def test_r2_no_status_or_an_unknown_one_stays_neutral(self, status) -> None:
        card = text_entry.render_unpriced_card([("зыбзик", None)], status=status)

        assert card == f"{NEUTRAL}\nЗапишу как есть: «зыбзик»."

    @pytest.mark.parametrize("status", sorted(QUESTION_BY_STATUS))
    def test_r3_numbers_hidden_never_talks_about_calories(self, status: str) -> None:
        card = text_entry.render_unpriced_card([("зыбзик", None)], hide_numbers=True, status=status)

        assert card == "Записать в дневник?\nЗапишу как есть: «зыбзик»."


class TestOneDish:
    @pytest.mark.parametrize(
        ("status", "question"),
        # При «estimated» число есть — это r6.
        sorted((s, q) for s, q in QUESTION_BY_STATUS.items() if s != "estimated"),
    )
    def test_r4_the_status_reaches_the_card(
        self, conversation, consent, status: str, question: str
    ) -> None:
        catalogue = _Unpriced({"зыбзик": status})
        _turn(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, "cb:food:diary", catalogue)

        assert result.meta["reply_kind"] == "food_text_unpriced_card"
        assert result.reply_text == f"{question}\nЗапишу как есть: «зыбзик» (300 г)."

    def test_r4_a_catalog_without_the_field_stays_neutral(self, conversation, consent) -> None:
        catalogue = _Unpriced({"зыбзик": None})
        _turn(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, "cb:food:diary", catalogue)

        assert result.reply_text == f"{NEUTRAL}\nЗапишу как есть: «зыбзик» (300 г)."

    def test_r6_a_number_from_the_model_keeps_the_estimate_card(
        self, conversation, consent
    ) -> None:
        catalogue = _Unpriced({"зыбзик": "estimated"}, ai=750.0)
        _turn(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, "cb:food:diary", catalogue)

        assert result.meta["reply_kind"] == "food_text_estimate_card"
        assert "Оценка ИИ" in result.reply_text
        assert FAILED not in result.reply_text
        assert SWITCHED_OFF not in result.reply_text


class TestSeveralPositions:
    @pytest.mark.parametrize(
        ("status", "question"), [("unavailable", FAILED), ("disabled", SWITCHED_OFF)]
    )
    def test_r5_one_reason_for_all_is_named(
        self, conversation, consent, status: str, question: str
    ) -> None:
        catalogue = _Unpriced({"зыбзик": status, "кофе": status})

        card = _items_card(conversation, catalogue, "зыбзик", "кофе")

        assert card == f"{question}\nЗапишу как есть: «зыбзик», «кофе»."

    @pytest.mark.parametrize(
        "statuses",
        [
            {"зыбзик": "unavailable", "кофе": "disabled"},
            {"зыбзик": "unavailable", "кофе": "declined"},
            {"зыбзик": "disabled", "кофе": None},
            # «кофе» каталог не узнал вовсе — причины у этой позиции нет.
            {"зыбзик": "unavailable"},
        ],
    )
    def test_r5_different_reasons_stay_neutral(
        self, conversation, consent, statuses: dict[str, str | None]
    ) -> None:
        card = _items_card(conversation, _Unpriced(statuses), "зыбзик", "кофе")

        assert card == f"{NEUTRAL}\nЗапишу как есть: «зыбзик», «кофе»."
