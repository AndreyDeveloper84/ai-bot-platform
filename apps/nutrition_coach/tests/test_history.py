"""Week-picture adapter — statuses stay honest, nothing persists (DRF-1464, T3).

«Нет данных» (Ayla answered, the day is empty) and «не смогли прочитать»
(Ayla did not answer) are two different truths, and the week keeps them
apart day by day, same as :mod:`apps.orchestrator.food_history` keeps
them apart for one day.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from apps.nutrition_coach.history import WeekPicture, WeekStatus, week_picture
from apps.orchestrator.food_history import Meal, Status, TodayDiary

USER = SimpleNamespace(channel="telegram", channel_user_id="123")

MEAL = Meal(dish="Борщ", calories=350, meal_type="lunch")


class FetchStub:
    """A read_today-shaped stub keyed by ISO date. Records its calls."""

    def __init__(self, days: dict[str, TodayDiary], *, fallback: TodayDiary | None = None) -> None:
        self._days = days
        self._fallback = fallback
        self.calls: list[str | None] = []

    def __call__(self, bot_user: Any, *, date: str | None = None) -> TodayDiary:
        self.calls.append(date)
        if date in self._days:
            return self._days[date]
        if self._fallback is not None:
            return self._fallback
        raise AssertionError(f"unexpected fetch for date={date}")


def fetch_mapping(days: dict[str, TodayDiary], *, fallback: TodayDiary | None = None) -> FetchStub:
    return FetchStub(days, fallback=fallback)


def iso(d: date) -> str:
    return d.isoformat()


class TestComposition:
    def test_seven_days_chronological_today_last(self) -> None:
        ok = TodayDiary(Status.OK, (MEAL,))
        fetch = fetch_mapping({}, fallback=ok)

        week = week_picture(USER, fetch=fetch)

        assert week.status is WeekStatus.OK
        assert len(week.days) == 7
        assert len(fetch.calls) == 7
        today = date.today()
        expected = [iso(today - timedelta(days=offset)) for offset in range(6, -1, -1)]
        assert fetch.calls == expected
        assert [day.date for day in week.days] == expected

    def test_days_carries_each_days_diary_verbatim(self) -> None:
        today = date.today()
        full = TodayDiary(Status.OK, (MEAL,))
        empty = TodayDiary(Status.OK)
        fetch = fetch_mapping({iso(today): full}, fallback=empty)

        week = week_picture(USER, fetch=fetch)

        assert week.status is WeekStatus.OK
        assert week.days[-1].diary == full
        assert week.days[-1].diary.dish_names() == ("Борщ",)
        assert all(day.diary.is_empty for day in week.days[:-1])

    def test_days_parameter_respected(self) -> None:
        ok = TodayDiary(Status.OK, (MEAL,))
        fetch = fetch_mapping({}, fallback=ok)

        week = week_picture(USER, days=3, fetch=fetch)

        assert len(week.days) == 3
        assert len(fetch.calls) == 3

    def test_days_must_be_at_least_one(self) -> None:
        fetch = fetch_mapping({}, fallback=TodayDiary(Status.OK))
        with pytest.raises(ValueError, match="days"):
            week_picture(USER, days=0, fetch=fetch)


class TestStatusesStayHonest:
    def test_empty_week_is_ok_not_unavailable(self) -> None:
        """Ayla answered every day and nobody logged anything — that is
        «нет данных», a valid OK picture, not a read failure."""
        fetch = fetch_mapping({}, fallback=TodayDiary(Status.OK))

        week = week_picture(USER, fetch=fetch)

        assert week.status is WeekStatus.OK
        assert all(day.diary.ok for day in week.days)
        assert all(day.diary.is_empty for day in week.days)

    def test_one_unavailable_day_marks_the_week_but_keeps_the_rest(self) -> None:
        """UNAVAILABLE must not flatten into an empty week: the days that
        WERE read keep their meals, the hole stays a hole."""
        today = date.today()
        hole = iso(today - timedelta(days=3))
        ok = TodayDiary(Status.OK, (MEAL,))
        fetch = fetch_mapping({hole: TodayDiary(Status.UNAVAILABLE)}, fallback=ok)

        week = week_picture(USER, fetch=fetch)

        assert week.status is WeekStatus.UNAVAILABLE
        by_date = {day.date: day.diary for day in week.days}
        assert by_date[hole].status is Status.UNAVAILABLE
        readable = [d for d, diary in by_date.items() if d != hole]
        assert len(readable) == 6
        assert all(by_date[d].dish_names() == ("Борщ",) for d in readable)

    def test_no_consent_any_day_fails_the_whole_picture_closed(self) -> None:
        """Любой день NO_CONSENT → вся картина «нет согласия». Consent is
        per-person, not per-day: a closed gate on one day is a closed
        gate on all of them, so the adapter stops asking."""
        today = date.today()
        consent_gone = iso(today - timedelta(days=5))
        ok = TodayDiary(Status.OK, (MEAL,))
        fetch = fetch_mapping({consent_gone: TodayDiary(Status.NO_CONSENT)}, fallback=ok)

        week = week_picture(USER, fetch=fetch)

        assert week.status is WeekStatus.NO_CONSENT
        assert len(week.days) == 0  # empty-assert-ok: согласия нет — мы и не держим ничего
        assert fetch.calls.index(consent_gone) == len(fetch.calls) - 1

    def test_no_consent_wins_over_unavailable(self) -> None:
        no_consent = TodayDiary(Status.NO_CONSENT)
        fetch = fetch_mapping({}, fallback=no_consent)

        week = week_picture(USER, fetch=fetch)

        assert week.status is WeekStatus.NO_CONSENT
        assert len(fetch.calls) == 1


class TestNothingPersists:
    def test_second_call_reads_fresh(self) -> None:
        """ADR-0009: the adapter is a composition of reads, not a cache.
        Two calls with different answers give two different pictures —
        nothing from the first survives into the second."""
        full = TodayDiary(Status.OK, (MEAL,))
        first = week_picture(USER, fetch=fetch_mapping({}, fallback=full))
        second = week_picture(USER, fetch=fetch_mapping({}, fallback=TodayDiary(Status.OK)))

        assert first.days[-1].diary.dish_names() == ("Борщ",)
        assert second.status is WeekStatus.OK
        assert all(day.diary.is_empty for day in second.days)

    def test_calls_are_independent_objects(self) -> None:
        ok = TodayDiary(Status.OK, (MEAL,))
        first = week_picture(USER, fetch=fetch_mapping({}, fallback=ok))
        second = week_picture(USER, fetch=fetch_mapping({}, fallback=ok))

        assert first is not second
        assert first.days is not second.days
        assert first == WeekPicture(status=WeekStatus.OK, days=second.days)
