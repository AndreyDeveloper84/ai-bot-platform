# ruff: noqa: F811 — фикстуры обвязки DRF-2328 импортируются и принимаются параметрами
"""Составная фраза еды и запись без расчёта — оба пути (DRF-2768, handoff §5).

Живой проход владельца 22.09: после нераспознанного фото — «Лепешка роти с
творогом и сыром , кофе». Фраза двух позиций шла одним блюдом, справочник её
не знал, ход уходил модели бота, а при лёгшем прокси — C01.

Решение владельца 06.10:

1. составная фраза разбирается на позиции, у числа каждой — своё
   происхождение (справочник / «Оценка ИИ» / без расчёта);
2. если чисел нет ни у одной — «Калорийность не рассчитана. Записать без
   расчёта?» (v1-текст, Q4), действия «Сохранить / Изменить / Отменить».

Карточка подтверждения — в обоих путях; запись только по тапу; нет калорий ≠
0; повторный тап не задваивает; «спасибо» и прочая не-еда к карточке не
приводят. Узлы идут через тот же ярлык ответа после фото, что живой ход.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apps.integrations.ayla import FoodLogResponse, NutritionUnavailableError
from apps.orchestrator.tests.test_typed_answer_after_scan_2328 import (  # noqa: F401 — фикстуры
    KNOWN,
    _bot_user,
    _Catalogue,
    _consent,
    _conversation,
    _mark,
    _nutrition_on,
    _turn,
    catalogue,
)
from apps.skills.base import SkillContext
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.text_entry import ParsedFood, parse_food_positions

pytestmark = pytest.mark.django_db(transaction=True)

OWNER_PHRASE = "Лепешка роти с творогом и сыром , кофе"


class _Diary(_Catalogue):
    """Каталог, который ещё и пишет: ключ идемпотентности возвращает ту же запись."""

    def __init__(self, *, fail_at: int | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_at = fail_at
        self.rows: dict[str, FoodLogResponse] = {}
        self.calls: list[str] = []

    async def log_meal(
        self,
        *,
        external_user_id,
        dish_name,
        meal_type,
        portion_multiplier,
        idempotency_key=None,
        entry_origin=None,
        scan_id=None,
    ):
        self.calls.append(idempotency_key)
        if idempotency_key in self.rows:
            return self.rows[idempotency_key]
        if self.fail_at is not None and len(self.rows) == self.fail_at:
            self.fail_at = None
            raise NutritionUnavailableError("down mid-way")
        known = dish_name in KNOWN
        row = FoodLogResponse(
            log_id=f"0b6f3c2e-9d1a-4c55-8e2f-{len(self.rows):012d}",
            dish_name=dish_name,
            meal_type=meal_type,
            calories=round(120.0 * portion_multiplier, 1) if known else None,
            raw={"entry_origin": entry_origin},
        )
        self.rows[idempotency_key] = row
        return row


def _context(conversation) -> SkillContext:
    return SkillContext(conversation=conversation, bot_user=_bot_user(), message_text="")


# ─── разбор на позиции ───────────────────────────────────────────────────────


class TestPositions:
    def test_the_owners_phrase_is_two_positions(self) -> None:
        assert parse_food_positions(OWNER_PHRASE) == [
            ParsedFood(dish="лепешка роти с творогом и сыром", grams=None),
            ParsedFood(dish="кофе", grams=None),
        ]

    def test_and_and_with_stay_inside_one_dish(self) -> None:
        assert parse_food_positions("творог с мёдом и орехами") == [
            ParsedFood(dish="творог с мёдом и орехами", grams=None)
        ]

    @pytest.mark.parametrize(
        ("text", "dishes"),
        [
            ("борщ 300 г; хлеб 50 г", [("борщ", 300.0), ("хлеб", 50.0)]),
            ("гречка 200\nкотлета", [("гречка", 200.0), ("котлета", None)]),
            ("сырники + сметана", [("сырники", None), ("сметана", None)]),
        ],
    )
    def test_the_named_separators_split(self, text, dishes) -> None:
        parsed = parse_food_positions(text)
        assert parsed is not None
        assert [(p.dish, p.grams) for p in parsed] == dishes

    def test_a_decimal_comma_is_part_of_the_number(self) -> None:
        parsed = parse_food_positions("кефир 200,5 г")
        assert parsed == [ParsedFood(dish="кефир", grams=200.5)]

    def test_too_many_positions_is_a_story_not_a_list(self) -> None:
        assert parse_food_positions("а, б, в, г, д, е, ж") is None


class TestNotFood:
    @pytest.mark.parametrize(
        "text", ["спасибо", "Спасибо!", "не помню", "запиши меня к мастеру", "а почему?"]
    )
    def test_the_closed_list_and_a_question_are_not_food(self, text) -> None:
        assert text_entry.looks_like_not_food(text)

    @pytest.mark.parametrize("text", [OWNER_PHRASE, "гречка", "борщ 300 г"])
    def test_food_is_not_caught_by_the_list(self, text) -> None:
        # Положительная пара: на той же функции не-еда ловится — пропуск ниже
        # значит «это еда», а не слепоту судьи.
        assert text_entry.looks_like_not_food("спасибо")
        assert not text_entry.looks_like_not_food(text)


# ─── путь 1: несколько позиций, у каждого числа — происхождение ─────────────


class TestTheItemsCard:
    def test_the_owners_phrase_gets_one_card_and_never_reaches_the_model(self) -> None:
        conversation = _conversation()
        _mark(conversation)
        fake = _Diary(model_estimates=True)

        with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
            result = _turn(OWNER_PHRASE, conversation)

        assert result is not None
        assert result.meta["reply_kind"] == "food_text_items_card"
        assert fake.estimates == ["лепешка роти с творогом и сыром", "кофе"]
        assert result.reply_text == (
            "Я распознала так:\n"
            "• лепешка роти с творогом и сыром — примерно 100 г (оценка) — ≈ 50 ккал · Оценка ИИ\n"
            "• кофе — примерно 100 г (оценка) — ≈ 50 ккал · Оценка ИИ\n"
            "Записать в дневник?"
        )
        assert fake.calls == []  # до тапа — ни строки

    def test_each_number_names_where_it_came_from(self, catalogue) -> None:
        context = _context(_conversation())
        result = text_entry.show_items(
            context, [ParsedFood("гречка", 200.0), ParsedFood("зыбзик", 150.0)]
        )

        assert result.reply_text == (
            "Я распознала так:\n"
            "• гречка — 200 г — примерно 240 ккал по справочнику\n"
            "• зыбзик — 150 г — без расчёта\n"
            "Не всё посчитано — итог дня будет неполным.\n"
            "Записать в дневник?"
        )

    def test_numbers_hidden_hides_every_number_and_the_count_line(self, catalogue) -> None:
        context = _context(_conversation())
        with patch("apps.nutrition_proactive.prefs.numbers_hidden_for", return_value=True):
            result = text_entry.show_items(
                context, [ParsedFood("гречка", 200.0), ParsedFood("зыбзик", 150.0)]
            )

        assert result.reply_text == (
            "Я распознала так:\n• гречка — 200 г\n• зыбзик — 150 г\nЗаписать в дневник?"
        )
        assert "ккал" not in result.reply_text


# ─── путь 2: ни у одной позиции нет числа ────────────────────────────────────


class TestWithoutACalculation:
    def test_no_numbers_anywhere_offers_saving_without_a_calculation(self) -> None:
        """Справочник не знает, ИИ молчит (лёг прокси) — карточка, не модель."""
        conversation = _conversation()
        _mark(conversation)
        fake = _Diary(model_estimates=False)

        with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
            result = _turn(OWNER_PHRASE, conversation)

        assert result is not None
        assert result.meta["reply_kind"] == "food_text_unpriced_card"
        assert result.reply_text == (
            "Калорийность не рассчитана. Записать без расчёта?\n"
            "Запишу как есть: «лепешка роти с творогом и сыром», «кофе»."
        )
        assert [b["label"] for b in result.action_data["buttons"]] == [
            "💾 Сохранить",
            "✏️ Изменить",
            "❌ Отменить",
        ]

    def test_a_single_unknown_dish_gets_the_same_card(self, catalogue) -> None:
        context = _context(_conversation())

        result = text_entry.show_estimate(context, "зыбзик", None, corrected=False)

        assert result.meta["reply_kind"] == "food_text_unpriced_card"
        assert result.reply_text == (
            "Калорийность не рассчитана. Записать без расчёта?\nЗапишу как есть: «зыбзик»."
        )

    def test_numbers_hidden_does_not_talk_about_calories(self, catalogue) -> None:
        context = _context(_conversation())
        with patch("apps.nutrition_proactive.prefs.numbers_hidden_for", return_value=True):
            result = text_entry.show_estimate(context, "зыбзик", None, corrected=False)

        assert result.reply_text == "Записать в дневник?\nЗапишу как есть: «зыбзик»."

    def test_save_writes_without_numbers_not_zero(self) -> None:
        conversation = _conversation()
        fake = _Diary()
        with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
            text_entry.show_items(
                _context(conversation), [ParsedFood("зыбзик", None), ParsedFood("бублик", None)]
            )
            result = text_entry.on_callback(_context(conversation), text_entry.CB_SAVE)

        assert result.meta["reply_kind"] == "food_text_items_logged"
        assert [row.calories for row in fake.rows.values()] == [None, None]
        assert result.reply_text == "Записала в дневник: зыбзик; бублик."
        assert "0 ккал" not in result.reply_text

    def test_change_asks_to_write_again_and_cancel_writes_nothing(self, catalogue) -> None:
        conversation = _conversation()
        text_entry.show_estimate(_context(conversation), "зыбзик", None, corrected=False)

        changed = text_entry.on_callback(_context(conversation), text_entry.CB_EDIT)

        assert changed.reply_text == "Напиши, что было, ещё раз — посчитаю заново."
        assert conversation.skill_state["food_text"]["expect_food"] is True
        text_entry.show_estimate(_context(conversation), "зыбзик", None, corrected=False)
        cancelled = text_entry.on_callback(_context(conversation), text_entry.CB_CANCEL)
        assert cancelled.reply_text == "Поняла, не записываю."
        assert "food_text" not in conversation.skill_state


# ─── запись: без дублей, без скрытой правки ─────────────────────────────────


class TestLogging:
    def test_each_position_is_written_once_with_its_own_key(self) -> None:
        conversation = _conversation()
        fake = _Diary()
        with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
            text_entry.show_items(
                _context(conversation), [ParsedFood("гречка", 200.0), ParsedFood("зыбзик", 150.0)]
            )
            token = conversation.skill_state["food_text"]["token"]
            result = text_entry.on_callback(_context(conversation), text_entry.CB_LOG)

        assert result.meta["reply_kind"] == "food_text_items_logged"
        assert fake.calls == [
            f"food-text:bot:max:2328:{token}:0",
            f"food-text:bot:max:2328:{token}:1",
        ]
        assert result.reply_text == "Записала в дневник: гречка — 240 ккал; зыбзик."

    def test_a_failure_mid_way_keeps_the_card_and_a_retry_does_not_duplicate(self) -> None:
        conversation = _conversation()
        fake = _Diary(fail_at=1)
        with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
            text_entry.show_items(
                _context(conversation), [ParsedFood("гречка", 200.0), ParsedFood("зыбзик", 150.0)]
            )
            first = text_entry.on_callback(_context(conversation), text_entry.CB_LOG)
            assert first.meta["reply_kind"] == "food_text_partial"
            assert "food_text" in conversation.skill_state
            second = text_entry.on_callback(_context(conversation), text_entry.CB_LOG)

        assert second.meta["reply_kind"] == "food_text_items_logged"
        assert len(fake.rows) == 2
        assert len(set(fake.calls)) == 2  # три вызова, два ключа — ни одного дубля

    def test_a_repeated_tap_after_success_writes_nothing_new(self) -> None:
        conversation = _conversation()
        fake = _Diary()
        with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
            text_entry.show_items(
                _context(conversation), [ParsedFood("гречка", 200.0), ParsedFood("зыбзик", 150.0)]
            )
            text_entry.on_callback(_context(conversation), text_entry.CB_LOG)
            again = text_entry.on_callback(_context(conversation), text_entry.CB_LOG)

        assert len(fake.rows) == 2
        assert again.meta["reply_kind"] == "food_text_stale"


# ─── не-еда не застревает ───────────────────────────────────────────────────


@pytest.mark.parametrize("text", ["спасибо", "не помню", "запиши меня к мастеру"])
def test_not_food_after_the_scan_still_goes_to_the_model(text) -> None:
    conversation = _conversation()
    _mark(conversation)
    fake = _Diary(model_estimates=False)

    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
        assert _turn(text, conversation) is None
    assert fake.estimates == []


def test_not_food_while_the_bot_waits_for_food_is_not_claimed() -> None:
    conversation = _conversation()
    text_entry.on_callback(_context(conversation), text_entry.CB_EDIT)
    assert text_entry.claims_text(conversation, "гречка 200 г")

    assert not text_entry.claims_text(conversation, "спасибо")
