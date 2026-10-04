"""DRF-2766 — «Без чисел» и ответы модели: чисел ей не дают и называть не велят.

Решение владельца 04.10: человек сам выбирает не видеть калории, БЖУ и
числовые цели «на всех экранах». Детерминированные реплики бота это
соблюдают сами; модель же может пересказать число из картины дня. Оборона в
два слоя (подтверждено главным окном 04.10, вариант 1):

* картина дня для модели — без ккал у блюд, без сравнения белка с
  ориентиром и без свободной подсказки сервиса (в ней может быть число);
  дни с записями и названия блюд остаются;
* рядом с заголовком блока — наше указание не называть калории, БЖУ и
  числовые цели. Указание стоит вне блока данных: заголовок говорит модели,
  что внутри блока — данные, а не инструкция.

Каждое «нет» — в паре с тем же блоком без выбора, где число есть.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.orchestrator import food_history, nutrition_context
from apps.orchestrator.nutrition_context import (
    NUMBERS_HIDDEN_INSTRUCTION,
    build_nutrition_context_block,
)


@pytest.fixture(autouse=True)
def _surface(settings, monkeypatch):
    from apps.nutrition_proactive.tests.test_remarks_suppressed_2222 import profile

    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = True
    monkeypatch.setattr(nutrition_context, "_consent_open", lambda bot_user: True)
    monkeypatch.setattr(nutrition_context, "_fetch_profile", Mock(return_value=profile()))
    monkeypatch.setattr(nutrition_context, "_fetch_goal", Mock(return_value=None))
    monkeypatch.setattr(
        nutrition_context,
        "_fetch_deficits",
        Mock(
            return_value=SimpleNamespace(
                days_observed=5,
                protein_avg_pct_goal=62.4,
                protein_low_streak_days=4,
                hint="белка стабильно мало, 40 г в день",
                fired_keys=["protein_low"],
                raw={},
            )
        ),
    )
    monkeypatch.setattr(
        food_history,
        "read_today",
        Mock(
            return_value=food_history.TodayDiary(
                food_history.Status.OK,
                meals=(food_history.Meal(dish="борщ", calories=147, meal_type="lunch"),),
            )
        ),
    )


def _block(*, hidden: bool) -> str:
    prefs = {"numbers_hidden": True} if hidden else {}
    with patch("apps.nutrition_proactive.prefs.get_prefs", return_value=prefs):
        return build_nutrition_context_block(Mock())


class TestTheModelGetsNoNumbers:
    def test_by_default_the_block_carries_the_numbers(self) -> None:
        block = _block(hidden=False)
        assert "борщ (147 ккал)" in block
        assert "62%" in block
        assert "40 г" in block
        assert NUMBERS_HIDDEN_INSTRUCTION not in block

    def test_numbers_hidden_keeps_the_day_and_drops_every_number_about_food(self) -> None:
        block = _block(hidden=True)
        assert "Сегодня в дневнике: борщ." in block
        assert "Дней с записями за неделю: 5." in block
        for gone in ("ккал", "%", "40 г", "ориентир", "не хватает подряд"):
            assert gone not in block.split(NUMBERS_HIDDEN_INSTRUCTION, 1)[1]

    def test_numbers_hidden_tells_the_model_outside_the_data(self) -> None:
        block = _block(hidden=True)
        header, rest = block.split("\n", 1)
        assert header == nutrition_context._HEADER
        assert rest.startswith(NUMBERS_HIDDEN_INSTRUCTION)
        assert "<<<UNTRUSTED_CONTEXT>>>" in rest.split(NUMBERS_HIDDEN_INSTRUCTION, 1)[1]
        assert "<<<UNTRUSTED_CONTEXT>>>" not in NUMBERS_HIDDEN_INSTRUCTION
