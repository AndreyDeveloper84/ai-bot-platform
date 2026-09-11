"""Trigger mechanics — counting, never wording (DRF-1464, T5).

Owner decisions pinned here:

* Q-04 — two start triggers: «поздний ужин × цель сна» and
  «завтраки × цель энергии». A sleep goal never fires the breakfast
  trigger and vice versa, however perfect the pattern is.
* Q-10 — the trigger MAY count days and hours; the text may never name
  them. This module is the counting half, so the boundaries are what the
  tests press on: 2 days is a miss, 3 is a hit; 20:59 is not late,
  21:00 is.
* UNAVAILABLE ≠ пустая неделя: a week with a hole proves nothing about
  «3 из последних 7 дней», so no trigger fires off partial data — the
  cost of a missed hint is silence, the cost of a hinted-on-a-guess is
  a message about food somebody cannot recognise as theirs.
"""

from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import pytest

from apps.nutrition_coach.history import DayPicture, WeekPicture, WeekStatus
from apps.nutrition_coach.triggers import (
    GOAL_KEYS_ENERGY,
    GOAL_KEYS_SLEEP,
    Trigger,
    breakfast_trigger,
    late_dinner_trigger,
)
from apps.orchestrator.food_history import Meal, Status, TodayDiary

MSK = ZoneInfo("Europe/Moscow")

SLEEP_GOAL = GOAL_KEYS_SLEEP[0]
ENERGY_GOAL = GOAL_KEYS_ENERGY[0]


def meal(meal_type: str, *, logged_at: str = "") -> Meal:
    return Meal(dish="Блюдо", calories=300, meal_type=meal_type, logged_at=logged_at)


def day(*meals: Meal, offset: int = 0) -> DayPicture:
    return DayPicture(
        date=(date.today() - timedelta(days=offset)).isoformat(),
        diary=TodayDiary(Status.OK, tuple(meals)),
    )


def week(*days: DayPicture, status: WeekStatus = WeekStatus.OK) -> WeekPicture:
    return WeekPicture(status=status, days=tuple(days))


def late_dinner_days(count: int, *, hour_utc: int = 18) -> WeekPicture:
    """``count`` days each holding one dinner logged at ``hour_utc`` UTC.

    18:30 UTC is 21:30 in Moscow — late by the recipient's clock, not by
    the server's: the conversion is the trigger's job, tested below.
    """
    dinner = meal("dinner", logged_at=f"2026-09-04T{hour_utc:02d}:30:00Z")
    return week(*(day(dinner, offset=i) for i in range(count)))


class TestLateDinnerTrigger:
    def test_two_late_dinners_are_a_miss_three_are_a_hit(self) -> None:
        assert late_dinner_trigger(SLEEP_GOAL, late_dinner_days(2), tz=MSK) is None
        trigger = late_dinner_trigger(SLEEP_GOAL, late_dinner_days(3), tz=MSK)
        assert trigger == Trigger(kind="late_dinner", days=3)

    def test_the_boundary_is_2100_local_not_2059(self) -> None:
        # 17:59 UTC = 20:59 MSK — not late. 18:00 UTC = 21:00 MSK — late.
        early = late_dinner_days(3, hour_utc=17)
        assert late_dinner_trigger(SLEEP_GOAL, early, tz=MSK) is None
        on_the_dot = week(
            *(day(meal("dinner", logged_at="2026-09-04T18:00:00Z"), offset=i) for i in range(3))
        )
        assert late_dinner_trigger(SLEEP_GOAL, on_the_dot, tz=MSK) is not None

    def test_the_hour_is_the_recipients_not_the_servers(self) -> None:
        """The same 18:30 UTC dinner is late in Moscow and not late in
        London. Counted in the timezone the planner hands over."""
        days = late_dinner_days(3)
        utc = ZoneInfo("UTC")
        assert late_dinner_trigger(SLEEP_GOAL, days, tz=MSK) is not None
        assert late_dinner_trigger(SLEEP_GOAL, days, tz=utc) is None

    def test_every_sleep_goal_key_counts(self) -> None:
        for key in GOAL_KEYS_SLEEP:
            assert late_dinner_trigger(key, late_dinner_days(3), tz=MSK) is not None

    def test_an_energy_goal_never_fires_the_sleep_trigger(self) -> None:
        assert late_dinner_trigger(ENERGY_GOAL, late_dinner_days(7), tz=MSK) is None

    def test_a_free_text_goal_has_no_trigger(self) -> None:
        """Curated keys only: a template text needs a known (goal × trigger)
        pair, and a free-form goal names no pair."""
        assert late_dinner_trigger("", late_dinner_days(7), tz=MSK) is None
        assert late_dinner_trigger("хочу высыпаться", late_dinner_days(7), tz=MSK) is None

    def test_a_dinner_without_a_time_is_not_counted(self) -> None:
        timeless = week(*(day(meal("dinner"), offset=i) for i in range(7)))
        assert late_dinner_trigger(SLEEP_GOAL, timeless, tz=MSK) is None

    def test_a_junk_timestamp_fails_closed_towards_silence(self) -> None:
        broken = week(*(day(meal("dinner", logged_at="не время"), offset=i) for i in range(7)))
        assert late_dinner_trigger(SLEEP_GOAL, broken, tz=MSK) is None


class TestBreakfastTrigger:
    def breakfast_days(self, count: int) -> WeekPicture:
        return week(*(day(meal("breakfast"), offset=i) for i in range(count)))

    def test_two_breakfast_days_are_a_miss_three_are_a_hit(self) -> None:
        assert breakfast_trigger(ENERGY_GOAL, self.breakfast_days(2)) is None
        trigger = breakfast_trigger(ENERGY_GOAL, self.breakfast_days(3))
        assert trigger == Trigger(kind="breakfasts", days=3)

    def test_every_energy_goal_key_counts(self) -> None:
        for key in GOAL_KEYS_ENERGY:
            assert breakfast_trigger(key, self.breakfast_days(3)) is not None

    def test_more_energy_is_an_energy_goal(self) -> None:
        """The key the goal reader's own tests pin (``test_goals.py``)."""
        assert "more_energy" in GOAL_KEYS_ENERGY
        assert breakfast_trigger("more_energy", self.breakfast_days(3)) is not None

    def test_a_sleep_goal_never_fires_the_energy_trigger(self) -> None:
        assert breakfast_trigger(SLEEP_GOAL, self.breakfast_days(7)) is None

    def test_presence_is_what_counts_not_absence(self) -> None:
        """The mechanic is «завтраки есть в записях» — an empty day simply
        does not add to the count. «Пропуск» is not a concept here (R3)."""
        mixed = week(
            day(meal("breakfast"), offset=0),
            day(offset=1),  # пустой день — день без записей, не «пропуск»
            day(meal("breakfast"), offset=2),
            day(meal("lunch"), offset=3),
            day(meal("breakfast"), offset=4),
        )
        trigger = breakfast_trigger(ENERGY_GOAL, mixed)
        assert trigger == Trigger(kind="breakfasts", days=3)


class TestAWeekWithAHoleProvesNothing:
    """UNAVAILABLE ≠ пустая неделя — и ≠ полная тоже.

    Six readable days with a late dinner in every one of them still do not
    prove «3 из последних 7»: the seventh day was never read. The trigger
    stays silent rather than pattern-match on a guess.
    """

    def test_unavailable_week_never_fires_even_with_perfect_readable_days(self) -> None:
        days = [day(meal("dinner", logged_at="2026-09-04T18:30:00Z"), offset=i) for i in range(6)]
        days.append(
            DayPicture(
                date=(date.today() - timedelta(days=6)).isoformat(),
                diary=TodayDiary(Status.UNAVAILABLE),
            )
        )
        partial = WeekPicture(status=WeekStatus.UNAVAILABLE, days=tuple(days))
        assert late_dinner_trigger(SLEEP_GOAL, partial, tz=MSK) is None
        assert breakfast_trigger(ENERGY_GOAL, partial) is None

    def test_no_consent_week_fires_nothing(self) -> None:
        refused = WeekPicture(status=WeekStatus.NO_CONSENT)
        assert late_dinner_trigger(SLEEP_GOAL, refused, tz=MSK) is None
        assert breakfast_trigger(ENERGY_GOAL, refused) is None

    def test_an_empty_ok_week_is_a_readable_zero(self) -> None:
        """Ayla answered every day and nobody logged — a true zero, and
        zero is below three."""
        empty = week(*(day(offset=i) for i in range(7)))
        assert empty.ok
        assert late_dinner_trigger(SLEEP_GOAL, empty, tz=MSK) is None
        assert breakfast_trigger(ENERGY_GOAL, empty) is None


class TestTriggerCarriesNoWording:
    def test_the_trigger_is_mechanics_only(self) -> None:
        """Q-10: counting lives here, wording lives in copy.py. The Trigger
        holds a kind and a day count — no sentence, no template, no hour."""
        trigger = late_dinner_trigger(SLEEP_GOAL, late_dinner_days(4), tz=MSK)
        assert trigger is not None
        assert trigger.kind == "late_dinner"
        assert trigger.days == 4
        assert not hasattr(trigger, "text")


@pytest.mark.parametrize("goal_key", ["relax", "face_skin", "body_feel_fit"])
def test_unrelated_goals_fire_nothing(goal_key: str) -> None:
    days = late_dinner_days(7)
    assert late_dinner_trigger(goal_key, days, tz=MSK) is None
    assert breakfast_trigger(goal_key, days) is None
