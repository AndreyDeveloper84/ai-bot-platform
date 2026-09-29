"""Week-picture adapter over the DRF-1467 daily reader (DRF-1464, T3).

The coach reasons about a week; :func:`apps.orchestrator.food_history.read_today`
answers about a day. This module is the composition of N daily reads into
one picture — and nothing more:

* **Not a cache (ADR-0009).** Nothing here outlives the call: no module
  state, no memoization, no row of somebody's meal history at rest. A
  second call re-reads. The diary lives at Ayla; a copy here — even a
  five-minute one — is the copy the owner ruled against.
* **Statuses stay honest.** «Ayla answered and the day is empty» and
  «Ayla did not answer» are different truths, so they stay different in
  the week too: an UNAVAILABLE day is a hole in the picture, never an
  empty plate, and it never drags the days that WERE read down with it.
* **Consent is per-person, not per-day.** :func:`read_today` gates
  PERSONAL_DATA + HEALTH before any byte leaves Ayla; a NO_CONSENT on
  one day means the gate is closed for all of them, so the adapter stops
  asking and the whole picture reports «нет согласия» — holding partial
  meals next to a closed gate would be a lie about the basis we read on.

TODO(DRF-1467): when food_history grows a native weekly read, switch the
loop below to it — one round-trip instead of N, and Ayla's own day
boundaries instead of ours. Until then the week is what the days say.

Dates are server-local ISO days, oldest first, today last. Day-boundary
skew against Ayla's timezone is a known approximation of composing
daily reads; the native weekly method is the fix, not a local clock
library here.

No frequency counters, no scoring: proactive cadence is DRF-1468's
mechanism (:mod:`apps.nutrition_proactive.antinag`), and «неделя
описывается, а не оценивается» (copy policy R3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Any

from apps.orchestrator.food_history import Status, TodayDiary, read_today


class WeekStatus(str, Enum):
    """Why a week picture holds what it holds. Never raised, always returned."""

    #: Every day was read. Says nothing about whether the week is empty.
    OK = "ok"
    #: Consent gate closed. No day was asked past the first refusal.
    NO_CONSENT = "no_consent"
    #: At least one day could not be read. The rest still carry their data.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DayPicture:
    """One day of the week: the ISO date we asked about and the answer."""

    date: str
    diary: TodayDiary


@dataclass(frozen=True)
class WeekPicture:
    """The answer to «как выглядит неделя» — including «не знаем»."""

    status: WeekStatus
    days: tuple[DayPicture, ...] = ()

    @property
    def ok(self) -> bool:
        """Every day was read. Says nothing about whether anyone logged."""
        return self.status is WeekStatus.OK


def week_picture(
    bot_user: Any,
    *,
    days: int = 7,
    fetch: Callable[..., TodayDiary] | None = None,
) -> WeekPicture:
    """The last ``days`` days of the diary, composed from daily reads.

    ``fetch`` is the test seam (same shape as ``fetch=`` on
    :func:`apps.nutrition_proactive.tasks.plan_daily_reports`): injected
    it replaces :func:`read_today` with the same call signature.
    """
    if days < 1:
        raise ValueError(f"days must be >= 1, got {days}")
    read = read_today if fetch is None else fetch

    today = date.today()
    pictures: list[DayPicture] = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        diary = read(bot_user, date=day)
        if diary.status is Status.NO_CONSENT:
            # The gate is per-person: closed on one day means closed on
            # all. Stop asking and hold nothing.
            return WeekPicture(status=WeekStatus.NO_CONSENT)
        pictures.append(DayPicture(date=day, diary=diary))

    if any(day.diary.status is Status.UNAVAILABLE for day in pictures):
        return WeekPicture(status=WeekStatus.UNAVAILABLE, days=tuple(pictures))
    return WeekPicture(status=WeekStatus.OK, days=tuple(pictures))
