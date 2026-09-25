"""Типовая порция не попадает в дневник без подтверждения (DRF-2444).

## Почему подтверждение стоит ДО записи, а не пометкой после

Число из дневника идёт дальше: «за день», «за неделю», подсказка модели,
озвучка, главный экран. Там оно **растворяется в сумме**, и места для
оговорки «обычно 300 г» не существует **по построению** — пометить его вниз
по течению нечем. Замер носителей: провенанс читают четыре места (два
экрана мини-аппа и два навыка бота, через общий разборщик), а поверхностей
уже записанного — одиннадцать, и **ни одна** его не читает.

Значит правило ставится **на входе**: в дневник попадает только то, чей вес
кто-то назвал.

## Чего этот лист НЕ делает

**Не показывает число по типовой порции с оговоркой.** Сегодня при `typical`
число не показывается вовсе (решение DRF-2371 — строже требования и никого
не обманывает), и включить показ можно только словами владельца («обычно
300 г — так?»). Пока их нет, «типовая порция работает» неправда, и
подтверждение — не лишний шаг, а единственное, что держит правдивость.

## Почему узлы есть, а поведения сегодня не видно

Каталог `typical` ещё **не выдаёт**: половина A (DRF-2402) оставила значение
зарезервированным. Правило написано раньше значения намеренно — «читатели
раньше поведения» и есть содержание разделения половин. Узлы ниже держат
все четыре исхода провода, включая «поля нет».
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.nutrition_client import FoodLogResponse
from apps.skills.food_scanner.skill import CLARIFY_PROMPT, FoodScannerSkill
from apps.skills.food_scanner.tests.test_skill import (  # переиспользуем стенд
    _context,
    _diary_consent_granted,  # noqa: F401 — autouse
    _enable_nutrition,  # noqa: F401 — autouse
    _personal_data_granted,  # noqa: F401 — autouse
)

pytestmark = pytest.mark.django_db


def _tap_to_diary(
    *,
    stashed_source: str | None,
    grams_named: int | None = None,
    calories: float | None = 147.0,
):
    """Тап «📔 В дневник» по скану, у которого в заначке карточки лежит признак.

    Возвращает ``(result, written)``: ``written`` — список вызовов записи, то
    есть факт, который может напечатать **только** проверяемый код. Ожидание
    «записи не было» на значении, положенном тестом, не проверялось бы ничем.
    """
    ctx = _context("cb:food:to_diary:scan-1")
    card: dict = {"scan_id": "scan-1", "dish": "Борщ", "portion_g": None}
    if stashed_source is not None:
        card["portion_source"] = stashed_source
    state: dict = {"food_scan": card}
    if grams_named is not None:
        state["food_scan_grams"] = {"scan-1": {"grams": grams_named, "portion_g": 300}}
    ctx.conversation.skill_state = state

    written: list[dict] = []
    client = Mock()

    async def _log(**kwargs):
        written.append(kwargs)
        return FoodLogResponse(
            log_id="log-1",
            dish_name="Борщ",
            meal_type="other",
            calories=calories,
            raw={"nutrition": {"portion_source": stashed_source}} if stashed_source else {},
        )

    client.log_meal = _log
    with (
        patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=client),
        patch("apps.conversations.services.write_skill_state", side_effect=lambda *a: None),
    ):
        return FoodScannerSkill().handle(ctx), written


class TestTheTypicalPortionAsksBeforeItWrites:
    def test_nothing_is_written_and_the_weight_is_asked(self) -> None:
        """Главный узел: записи НЕ БЫЛО, и спрошено существующими словами."""
        result, written = _tap_to_diary(stashed_source="typical")

        assert written == [], written
        assert result.reply_text == CLARIFY_PROMPT
        assert result.meta["reply_kind"] == "food_scanner_log_needs_weight"

    def test_a_named_weight_lets_the_record_through(self) -> None:
        """Обратная сторона: подтверждение дано — запись идёт.

        Без этого узла «не пишем при typical» можно было бы выполнить,
        перестав писать вообще.
        """
        result, written = _tap_to_diary(stashed_source="typical", grams_named=250)

        assert len(written) == 1, written
        assert result.meta["reply_kind"] != "food_scanner_log_needs_weight"


class TestTheOtherThreeOutcomesAreUntouched:
    """Правило узкое: три остальных исхода ведут себя как до листа."""

    def test_a_provider_named_portion_is_written(self) -> None:
        _, written = _tap_to_diary(stashed_source="provider")

        assert len(written) == 1, written

    def test_an_unknown_portion_is_still_written(self) -> None:
        """Сегодняшнее поведение владельцем согласовано: запись ложится, число не называется.

        Менять его этим листом нельзя — человек нажал «в дневник» и вправе
        получить запись; число при этом не выдумывается (DRF-2371).
        """
        _, written = _tap_to_diary(stashed_source="unknown", calories=None)

        assert len(written) == 1, written

    def test_an_absent_field_is_written(self) -> None:
        """Старый ответ каталога признака не несёт — и это не повод не писать."""
        _, written = _tap_to_diary(stashed_source=None)

        assert len(written) == 1, written

    def test_an_unknown_fourth_value_is_written_not_blocked(self) -> None:
        """Незнакомое значение читается как «не названо», а не как typical.

        Fail-closed здесь означает «не подтверждать число», а не «не
        записывать»: блокировать запись на значении, которого мы не знаем,
        значило бы терять то, что человек просил сохранить.
        """
        _, written = _tap_to_diary(stashed_source="confirmed")

        assert len(written) == 1, written
