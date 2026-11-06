"""DRF-2761 — оценка калорий ИИ в текстовом вводе еды: число с пометкой.

Решение владельца 02.10.2026 (пересмотр §40): при промахе справочника
калории можно оценить ИИ и показать с пометкой — дословно **«Оценка ИИ»**.
Каталог отдаёт оценку своим ключом (``kcal_ai_estimate`` у оценки,
``ai_calories`` у записи), а проверенное ``kcal`` / ``calories`` при ней
остаётся пустым. Бот обязан:

* a1 — показать число с пометкой в карточке; красное «до»: при промахе
  числа в карточке не было вовсе;
* a2 — показать его и тогда, когда граммов человек не называл (довод
  владельца: вес и так показан «примерно»);
* a3 — не выдать оценку за проверенное: пометка и «≈» есть только у оценки,
  БЖУ у оценки нет; проверенное число оценку бьёт;
* a4 — сказать «Оценка ИИ» и в подтверждении записи, и после правки
  граммов, и после возврата удалённой записи;
* a5 — не положить оценку в поле проверенных калорий действия;
* a6 — без оценки всё как раньше.

Каталог подменён записывающим двойником (как в ``test_text_entry``); сам
расчёт и правила «кому можно» держит каталог
(``nutrition/tests/test_ai_calorie_estimate_2761.py``).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.integrations.ayla import DishEstimate, FoodLogResponse
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.tests.test_text_entry import _Catalogue, _turn


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True


@pytest.fixture
def consent():
    """Согласия открыты — как в ``test_text_entry``."""
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ) as opened,
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield opened


@pytest.fixture
def conversation():
    return SimpleNamespace(id="conv-2761", skill_state={})


LOG_ID = "0b0b0b0b-1837-4a4a-8c8c-000000002761"

#: Оценка модели: 250 ккал на 100 г.
AI_KCAL_PER_100G = 250.0


class _MissWithEstimate(_Catalogue):
    """Каталог, который блюда не знает, а оценку ИИ отдаёт (или нет)."""

    log_id = LOG_ID

    def __init__(self, *, ai: bool = True, verified_kcal: float | None = None) -> None:
        super().__init__()
        self.ai = ai
        self.verified_kcal = verified_kcal

    async def estimate_dish(
        self, *, external_user_id, dish_name, portion_g=None, ai_estimate_allowed=None
    ):
        self.estimates.append({"dish_name": dish_name, "portion_g": portion_g})
        grams = 100.0 if portion_g is None else float(portion_g)
        ai = AI_KCAL_PER_100G * grams / 100.0 if self.ai else None
        named = portion_g is not None
        return DishEstimate(
            matched_dish=dish_name,
            portion_g=grams,
            portion_estimated=not named,
            kcal=self.verified_kcal,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            raw={"portion_source": "provider" if named else "unknown"},
            kcal_ai_estimate=ai,
        )

    def _entry(self, dish: str, multiplier: float) -> FoodLogResponse:
        return FoodLogResponse(
            log_id=self.log_id,
            dish_name=dish,
            meal_type="other",
            calories=self.verified_kcal,
            raw={"entry_origin": "text_estimated_confirmed"},
            ai_calories=AI_KCAL_PER_100G * multiplier if self.ai else None,
        )

    async def log_meal(self, **kwargs):
        self.logs.append(kwargs)
        return self._entry(kwargs["dish_name"], kwargs["portion_multiplier"])

    async def update_meal(self, **kwargs):
        self.updates.append(kwargs)
        return self._entry("зыбзик", kwargs["portion_multiplier"])

    async def restore_meal(self, **kwargs):
        self.restores.append(kwargs)
        return self._entry("зыбзик", 3.0)


def _card(conversation, phrase: str, catalogue: _Catalogue) -> str:
    _turn(conversation, phrase, catalogue)
    return _turn(conversation, "cb:food:diary", catalogue).reply_text


class TestTheCard:
    def test_a1_red_before_a_miss_without_an_estimate_carries_no_number(
        self, conversation, consent
    ) -> None:
        card = _card(conversation, "зыбзик 300г", _MissWithEstimate(ai=False))

        # DRF-2768: ни справочника, ни оценки — карточка «без расчёта» (решение
        # владельца 06.10). Суть узла та же: числа не выдумываются.
        assert card == (
            "Калорийность не рассчитана. Записать без расчёта?\nЗапишу как есть: «зыбзик» (300 г)."
        )

    def test_a1_a_miss_with_an_estimate_carries_the_number_and_the_mark(
        self, conversation, consent
    ) -> None:
        card = _card(conversation, "зыбзик 300г", _MissWithEstimate())

        # 250 ккал на 100 г × 300 г. Пометка — слова владельца дословно.
        assert card == (
            "Я распознала так: зыбзик.\n"
            "Порция — 300 г, по твоим словам.\n"
            "≈ 750 ккал · Оценка ИИ.\n"
            "Записать в дневник?"
        )

    def test_a2_no_grams_named_the_estimate_is_still_shown(self, conversation, consent) -> None:
        # Незнакомое слово без граммов детектор «это про еду» сам не узнаёт —
        # сюда приходят через «В дневник» и ответ на вопрос «что было».
        catalogue = _MissWithEstimate()
        _turn(conversation, "cb:food:diary", catalogue)
        card = _turn(conversation, "зыбзик", catalogue).reply_text

        assert card == (
            "Я распознала так: зыбзик.\n"
            "Порция — примерно 100 г, это оценка: граммов в сообщении не было.\n"
            "≈ 250 ккал · Оценка ИИ.\n"
            "Записать в дневник?"
        )

    def test_a3_the_reference_number_beats_the_estimate(self, conversation, consent) -> None:
        """Каталог такого не отдаёт; если отдаст оба — говорит справочник."""
        card = _card(conversation, "зыбзик 300г", _MissWithEstimate(verified_kcal=150.0))

        assert "Примерно 150 ккал — оценка по справочнику блюд." in card
        assert "Оценка ИИ" not in card
        assert "≈" not in card

    def test_a3_a_reference_card_never_carries_the_mark(self, conversation, consent) -> None:
        card = _card(conversation, "борщ 300г", _Catalogue())

        # Положительная пара: проверенное число на месте, со своими БЖУ.
        assert "Примерно 150 ккал · Б 6 · Ж 9 · У 12 — оценка по справочнику блюд." in card
        assert "Оценка ИИ" not in card
        assert "≈" not in card

    def test_a3_the_estimate_line_carries_calories_only(self, conversation, consent) -> None:
        card = _card(conversation, "зыбзик 300г", _MissWithEstimate())

        line = card.splitlines()[2]
        assert line == "≈ 750 ккал · Оценка ИИ."
        for macro in ("Б ", "Ж ", "У "):
            assert macro not in line

    def test_the_mark_is_the_owners_words(self) -> None:
        assert text_entry.AI_ESTIMATE_MARK == "Оценка ИИ"
        assert text_entry.ai_kcal_phrase(319.6) == "≈ 320 ккал · Оценка ИИ"


class TestTheConfirmations:
    def test_a4_the_logged_reply_says_the_mark(self, conversation, consent) -> None:
        catalogue = _MissWithEstimate()
        _card(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, "cb:food:text_log", catalogue)

        assert catalogue.logs[0]["dish_name"] == "зыбзик"
        assert catalogue.logs[0]["portion_multiplier"] == 3.0
        assert result.reply_text == "Записала в дневник: зыбзик — ≈ 750 ккал · Оценка ИИ."

    def test_a5_the_estimate_is_not_in_the_verified_calories_of_the_action(
        self, conversation, consent
    ) -> None:
        catalogue = _MissWithEstimate()
        _card(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, "cb:food:text_log", catalogue)

        assert result.action_data["calories"] is None
        assert result.action_data["ai_calories"] == 750.0

    def test_a4_fixing_the_grams_keeps_the_mark(self, conversation, consent) -> None:
        catalogue = _MissWithEstimate()
        _turn(conversation, f"cb:food:entry_fix:{LOG_ID}", catalogue)

        result = _turn(conversation, "200", catalogue)

        assert catalogue.updates[0]["portion_multiplier"] == 2.0
        assert result.reply_text == "Исправила: зыбзик — теперь ≈ 500 ккал · Оценка ИИ."

    def test_a4_restoring_a_deleted_entry_keeps_the_mark(self, conversation, consent) -> None:
        catalogue = _MissWithEstimate()
        _turn(conversation, f"cb:food:entry_del:{LOG_ID}", catalogue)

        result = _turn(conversation, f"cb:food:entry_undo:{LOG_ID}", catalogue)

        assert result.reply_text == "Вернула в дневник: зыбзик — ≈ 750 ккал · Оценка ИИ."

    def test_a3_a_verified_entry_beats_the_estimate_in_every_reply(
        self, conversation, consent
    ) -> None:
        catalogue = _MissWithEstimate(verified_kcal=150.0)
        _card(conversation, "зыбзик 300г", catalogue)

        logged = _turn(conversation, "cb:food:text_log", catalogue)

        assert logged.reply_text == "Записала в дневник: зыбзик — 150 ккал."
        assert "ai_calories" not in logged.action_data
        assert logged.action_data["calories"] == 150.0


class TestWithoutAnEstimateNothingChanges:
    def test_a6_the_logged_reply_stays_numberless(self, conversation, consent) -> None:
        catalogue = _MissWithEstimate(ai=False)
        _card(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, "cb:food:text_log", catalogue)

        assert result.reply_text == "Записала в дневник: зыбзик."
        assert result.action_data["calories"] is None
        assert "ai_calories" not in result.action_data

    def test_a6_the_reference_path_is_untouched(self, conversation, consent) -> None:
        catalogue = _Catalogue()
        _card(conversation, "борщ 300г", catalogue)

        result = _turn(conversation, "cb:food:text_log", catalogue)

        assert result.reply_text == "Записала в дневник: борщ — 150 ккал."
        assert "ai_calories" not in result.action_data

    @pytest.mark.parametrize("junk", [True, "750", None])
    def test_a6_a_non_number_in_the_estimate_field_is_not_shown(
        self, conversation, consent, junk
    ) -> None:
        """Поле с мусором — оценки нет; ``True`` не «1 ккал»."""

        class _Junk(_MissWithEstimate):
            def _entry(self, dish, multiplier):
                return FoodLogResponse(
                    log_id=LOG_ID,
                    dish_name=dish,
                    meal_type="other",
                    calories=None,
                    raw={},
                    ai_calories=junk,
                )

        catalogue = _Junk()
        _card(conversation, "зыбзик 300г", catalogue)

        result = _turn(conversation, "cb:food:text_log", catalogue)

        assert result.reply_text == "Записала в дневник: зыбзик."
