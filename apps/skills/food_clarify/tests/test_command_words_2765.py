"""Командные слова не входят в название блюда (DRF-2765).

Живой smoke владельца 04.10: «запиши в дневник борщ 300гр». Граммы прочитались,
а название ушло в каталог целиком — «запиши в дневник борщ». Такого блюда в
справочнике нет, каталог позвал модель, и в дневник легло «≈ 150 ккал · Оценка
ИИ» вместо проверенного борща (147 ккал на 300 г). Оценка ИИ в дневную сумму не
входит (решение iv DRF-2761), поэтому итог дня показывал «0 из 2588».

``parse_food_text`` снимал только слова о еде («съела», «на обед»), а слова,
которыми человек просит записать («запиши в дневник», «добавь», «внеси»), —
нет. Теперь снимает и их — в начале фразы и «в дневник» в конце. Название,
которое лишь начинается похоже («запеканка»), не трогается.

Тем же разборщиком пользуются ярлык «блюдо + граммы» в чате, тап «📔 В
дневник» и ручной ввод в Mini App — правка одна на все три двери.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import DishEstimate
from apps.orchestrator.nutrition_global import try_handle_structured_nutrition_turn
from apps.skills.food_clarify import text_entry

OWNER_PHRASE = "запиши в дневник борщ 300гр"


class TestCommandWordsAreNotTheDish:
    @pytest.mark.parametrize(
        ("text", "dish", "grams"),
        [
            pytest.param(OWNER_PHRASE, "борщ", 300.0, id="owner-smoke-04-10"),
            pytest.param("Запиши в дневник: борщ, 300 г", "борщ", 300.0, id="capital-and-colon"),
            pytest.param("запиши мне, пожалуйста, борщ 300 г", "борщ", 300.0, id="please"),
            pytest.param("добавь борщ 250", "борщ", 250.0, id="add"),
            pytest.param("добавь в дневник питания плов 200 г", "плов", 200.0, id="food-diary"),
            pytest.param("внеси в дневник плов 200 г", "плов", 200.0, id="enter"),
            pytest.param("занеси борщ 300 г", "борщ", 300.0, id="put-in"),
            pytest.param("запиши что я съела борщ 300 г", "борщ", 300.0, id="what-i-ate"),
            pytest.param("запиши в дневник я съела борщ 300 г", "борщ", 300.0, id="then-filler"),
            pytest.param("борщ 300 г запиши в дневник", "борщ", 300.0, id="command-at-the-end"),
            pytest.param("борщ 300 г в дневник", "борщ", 300.0, id="to-diary-at-the-end"),
            pytest.param("запиши в дневник борщ", "борщ", None, id="no-grams"),
        ],
    )
    def test_the_dish_is_only_the_food(self, text, dish, grams) -> None:
        assert text_entry.parse_food_text(text) == text_entry.ParsedFood(dish=dish, grams=grams)

    @pytest.mark.parametrize(
        ("text", "dish", "grams"),
        [
            pytest.param("борщ 300г", "борщ", 300.0, id="plain"),
            pytest.param("съела борщ 300 г", "борщ", 300.0, id="eating-filler-as-before"),
            pytest.param("шакшука 200 г", "шакшука", 200.0, id="unknown-dish-as-before"),
            pytest.param("запеканка 200 г", "запеканка", 200.0, id="starts-like-a-command"),
            pytest.param("добавка к супу 100 г", "добавка к супу", 100.0, id="добавка-is-food"),
            pytest.param("2 яйца", "2 яйца", None, id="count-as-before"),
        ],
    )
    def test_food_names_stay_whole(self, text, dish, grams) -> None:
        assert text_entry.parse_food_text(text) == text_entry.ParsedFood(dish=dish, grams=grams)

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param("запиши в дневник 300 г", id="only-command-and-grams"),
            pytest.param("добавь в дневник", id="only-command"),
        ],
    )
    def test_a_command_without_food_is_not_a_dish(self, text) -> None:
        assert text_entry.parse_food_text(text) is None
        # Положительная пара: та же команда с едой — разбирается.
        assert text_entry.parse_food_text(f"{text} борщ") is not None


# ─── путь целиком: ярлык «блюдо + граммы» в чате ──────────────────────────


class _Catalogue:
    """Двойник каталога: справочник знает только «борщ» (49 ккал на 100 г)."""

    def __init__(self) -> None:
        self.estimates: list[dict[str, Any]] = []

    async def estimate_dish(self, *, external_user_id, dish_name, portion_g=None):
        self.estimates.append({"dish_name": dish_name, "portion_g": portion_g})
        grams = 100.0 if portion_g is None else float(portion_g)
        known = dish_name == "борщ"
        return DishEstimate(
            matched_dish=dish_name,
            portion_g=grams,
            portion_estimated=portion_g is None,
            kcal=round(49.0 * grams / 100.0, 1) if known else None,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            raw={"source": "seed_ru" if known else "ai_estimate"},
            kcal_ai_estimate=None if known else 150.0,
        )


@pytest.fixture
def catalogue(settings):
    settings.NUTRITION_ENABLED = True
    fake = _Catalogue()
    with (
        patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake),
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield fake


def _structured(text: str):
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2765"
    return try_handle_structured_nutrition_turn(
        text=text,
        attachments=None,
        bot_user=bot_user,
        conversation=SimpleNamespace(id="conv-2765", skill_state={}),
        trace_id="t-2765",
    )


@pytest.mark.django_db(transaction=True)
class TestTheOwnerPhraseReachesTheReference:
    def test_the_catalogue_is_asked_for_borscht_and_answers_verified(self, catalogue) -> None:
        result = _structured(OWNER_PHRASE)

        assert result is not None, "ярлык не сработал — ход ушёл бы модели"
        assert catalogue.estimates == [{"dish_name": "борщ", "portion_g": 300.0}]
        assert result.action_type == "food_text_estimate_card"
        assert "147" in result.reply_text
        assert text_entry.AI_ESTIMATE_MARK not in result.reply_text

    def test_plain_borscht_is_the_same_card(self, catalogue) -> None:
        result = _structured("борщ 300гр")

        assert result is not None
        assert catalogue.estimates == [{"dish_name": "борщ", "portion_g": 300.0}]
        assert "147" in result.reply_text

    def test_an_unknown_dish_still_gets_the_ai_estimate(self, catalogue) -> None:
        result = _structured("запиши в дневник шакшука 200 г")

        assert result is not None
        assert catalogue.estimates == [{"dish_name": "шакшука", "portion_g": 200.0}]
        assert text_entry.AI_ESTIMATE_MARK in result.reply_text
