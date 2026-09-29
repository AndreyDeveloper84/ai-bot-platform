"""DRF-2124 — «в ориентире N» в карточке плана в чате.

К факту «дневник 4 из 5» каталог (#515) добавляет второй факт — дни ведра с
суммой ≤ подтверждённого ориентира. Карточка показывает его ТОЛЬКО когда
он есть (``within_target_count != None``); без ориентира строка ровно
прежняя. Это факт, не оценка: ни «отлично», ни ✓, ни процента (В-5).

* c1 — целое → «дневник 4 из 5, в ориентире 3»; присутствие раньше
  отсутствия;
* c2 — ``None`` → строка как до этого листа, слова «ориентир» нет;
* c3 — 0 — тоже факт: «в ориентире 0» печатается, не прячется;
* c4 — ``per_day`` — «дневник 1 из 1 (сегодня), в ориентире 1»;
* c5 — слов достижения нет ни при полном совпадении (5 из 5, в ориентире
  5), ни при нуле; ✓ остаётся только у брони «1 из 1».
* c6 — у воды и брони «в ориентире» не бывает, даже если DTO его несёт.
"""

from __future__ import annotations

import pytest

from apps.integrations.ayla.wellness_context_client import PlanLite, PlanLiteAction
from apps.orchestrator.plan_lite_card import render_plan_lite_card

pytestmark = pytest.mark.django_db(transaction=True)

_BUCKET = ("2026-09-14", "2026-09-21")
_DAY = ("2026-09-18", "2026-09-19")


def _plan(*actions: PlanLiteAction) -> PlanLite:
    return PlanLite(plan_id="p-2124", goal_key="tone_up", actions=actions)


def _food(
    target: int, done: int, within: int | None, *, cadence: str = "per_week"
) -> PlanLiteAction:
    bucket = _DAY if cadence == "per_day" else _BUCKET
    return PlanLiteAction("log_food", cadence, target, done, *bucket, within_target_count=within)


_NO_ACHIEVEMENT = ("%", "достиг", "отлично", "молодец", "прогресс", "пропуст")


class TestC1TheFactIsShown:
    def test_within_target_after_n_of_m(self) -> None:
        text = render_plan_lite_card(_plan(_food(5, 4, 3)))
        assert "дневник 4 из 5, в ориентире 3" in text


class TestC2NoneIsTheOldLine:
    def test_no_target_no_words(self) -> None:
        text = render_plan_lite_card(_plan(_food(5, 4, None)))
        assert "дневник 4 из 5" in text
        assert "ориентир" not in text.lower()

    def test_the_line_is_byte_identical_to_the_default_dto(self) -> None:
        old = render_plan_lite_card(_plan(PlanLiteAction("log_food", "per_week", 5, 4, *_BUCKET)))
        assert old == render_plan_lite_card(_plan(_food(5, 4, None)))


class TestC3ZeroIsAFact:
    def test_zero_is_printed(self) -> None:
        text = render_plan_lite_card(_plan(_food(5, 4, 0)))
        assert "дневник 4 из 5, в ориентире 0" in text


class TestC4PerDay:
    def test_today_suffix_then_within(self) -> None:
        text = render_plan_lite_card(_plan(_food(1, 1, 1, cadence="per_day")))
        assert "дневник 1 из 1 (сегодня), в ориентире 1" in text


class TestC5NoAchievementWording:
    @pytest.mark.parametrize("within", [5, 0, None], ids=["full", "zero", "none"])
    def test_facts_only(self, within: int | None) -> None:
        text = render_plan_lite_card(
            _plan(
                PlanLiteAction("book_service", "per_week", 1, 1, *_BUCKET),
                _food(5, 5, within),
            )
        )
        low = text.lower()
        assert "5 из 5" in low
        assert all(word not in low for word in _NO_ACHIEVEMENT)
        # ✓ — только у брони «1 из 1», не у дневника.
        assert text.count("✓") == 1 and "услугу ✓" in text


class TestC6OnlyFood:
    def test_water_and_booking_never_say_within(self) -> None:
        text = render_plan_lite_card(
            _plan(
                PlanLiteAction("log_water", "per_day", 7, 4, *_DAY, within_target_count=3),
                PlanLiteAction("book_service", "per_week", 2, 1, *_BUCKET, within_target_count=1),
            )
        )
        # Присутствие раньше отсутствия: обе строки на месте.
        assert "вода 4 из 7 (сегодня)" in text and "услугу 1 из 2" in text
        assert "ориентир" not in text.lower()
