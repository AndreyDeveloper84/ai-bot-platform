"""Запись без чисел не превращается в «0 ккал» и не роняет ответ (DRF-2371).

После DRF-2371 каталог сохраняет запись и тогда, когда числа вывести
неоткуда (порция неизвестна, блюда нет в справочнике): на месте калорий
``null``. Сканер брал это число как данность — ``int(log.calories)`` — и
по этому пути:

* ``None`` роняет ответ целиком (``TypeError``), человек не видит ничего,
  хотя запись в дневнике уже лежит;
* прикрытый нулём (``float(... or 0.0)`` в клиенте) — читается «Записала:
  Борщ — 0 ккал», то есть Ayla утверждает посчитанное там, где не считала.

Узлы:
* k1 — запись без числа: ответ есть, числа калорий в нём нет, слово
  «Записала» остаётся (запись-то легла);
* k2 — положительная пара: посчитанная запись говорит число как прежде.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.nutrition_client import FoodLogResponse
from apps.skills.food_scanner.skill import FoodScannerSkill
from apps.skills.food_scanner.tests.test_skill import (  # переиспользуем стенд
    _context,
    _diary_consent_granted,  # noqa: F401 — autouse
    _enable_nutrition,  # noqa: F401 — autouse
    _personal_data_granted,  # noqa: F401 — autouse
)

pytestmark = pytest.mark.django_db


def _to_diary(*, calories: float | None, portion_source: str | None = None):
    """Тап «📔 В дневник» по скану, на который каталог ответил ``calories``."""
    ctx = _context("cb:food:to_diary:scan-1")
    ctx.conversation.skill_state = {"food_scan": {"scan_id": "scan-1", "dish": "Борщ"}}
    client = Mock()
    raw: dict = {}
    if portion_source is not None:
        raw = {"nutrition": {"portion_source": portion_source}}

    async def _log(**_kwargs):
        return FoodLogResponse(
            log_id="log-1", dish_name="Борщ", meal_type="other", calories=calories, raw=raw
        )

    client.log_meal = _log
    with (
        patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=client),
        patch("apps.conversations.services.write_skill_state", side_effect=lambda *a: None),
    ):
        return FoodScannerSkill().handle(ctx)


class TestK1EntryWithoutNumbers:
    def test_the_answer_exists_and_names_no_calories(self) -> None:
        result = _to_diary(calories=None)

        # Утверждение о наличии — раньше утверждения об отсутствии: ответ есть,
        # и он подтверждает записанное блюдо.
        assert "Записала" in result.reply_text
        assert "Борщ" in result.reply_text
        # А числа калорий — нет: ни нуля, ни любого другого. Проверка по
        # слову «ккал», а не по цифре: цифра в названии блюда покраснила бы
        # узел от постороннего изменения стенда, а не от дефекта.
        assert "ккал" not in result.reply_text


class TestK2CountedEntryUnchanged:
    def test_a_counted_entry_still_names_the_number(self) -> None:
        result = _to_diary(calories=250.0)

        assert result.reply_text == "Записала: Борщ — 250 ккал."


class TestK3TheAnswerAsksTheProvenanceToo:
    """Число записи тоже бывает не названным никем.

    Каталог считает по своей константе, когда веса не назвал никто, — и
    тогда `calories` в ответе есть, а названным числом не является.
    Без этой проверки правило держалось бы один ход: карточка о числе
    молчит, а ответ после тапа говорит «250 ккал».
    """

    def test_a_weight_nobody_named_is_not_spoken_as_a_number(self) -> None:
        result = _to_diary(calories=250.0, portion_source="unknown")

        assert "Записала" in result.reply_text
        assert "ккал" not in result.reply_text

    def test_a_named_weight_is_spoken_as_before(self) -> None:
        result = _to_diary(calories=250.0, portion_source="provider")

        assert result.reply_text == "Записала: Борщ — 250 ккал."

    def test_an_unknown_provenance_value_is_read_carefully(self) -> None:
        result = _to_diary(calories=250.0, portion_source="confirmed")

        assert "ккал" not in result.reply_text
