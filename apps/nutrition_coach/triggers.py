"""Proactive hint triggers — the counting half (DRF-1464, T5).

Owner decision Q-04: exactly two triggers open the coach_hint surface —
«поздний ужин × цель сна» and «завтраки × цель энергии». This module is
where each one is *detected*; what the person then reads is
:mod:`apps.nutrition_coach.copy`, a separate file on purpose (Q-10: the
trigger may count days and hours, the text may never name them — keeping
the two in different modules is what makes that reviewable).

### The rules both detectors share

* **Goal first.** A trigger exists only as a (goal × pattern) pair
  (copy policy R7: proactive only onto an explicit goal). A sleep goal
  with seven perfect breakfast weeks fires nothing, and a free-text goal
  fires nothing either: the copy is templated per pair, and a goal whose
  key names no pair names no template.
* **Only a fully-read week counts.** ``WeekStatus.UNAVAILABLE`` means at
  least one day was never read, and «3 из последних 7 дней» cannot be
  proven about a week with a hole in it. UNAVAILABLE is not an empty
  week and is not a full one either — it is a week we do not know, so
  both detectors answer ``None``. Silence is the cheap error here.
* **Presence, never absence.** Days are counted when a meal IS in the
  diary. There is no «пропуск» concept to count: an empty day adds
  nothing, and nothing here could ever produce «ты пропустила» (R3).

No frequency state of any kind: how often a hint may fire is DRF-1468's
question (``apps.nutrition_proactive.antinag``), asked by the planner
before these detectors are even called.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Final

from apps.nutrition_coach.history import WeekPicture

logger = logging.getLogger(__name__)

#: «Поздний ужин» — a dinner logged at this local hour or later.
LATE_DINNER_HOUR: Final[int] = 21

#: How many matching days of the last 7 a pattern needs. 2 is a
#: coincidence, 3 is a pattern worth one weekly hint.
TRIGGER_MIN_DAYS: Final[int] = 3

#: The curated goal keys each trigger works onto. The energy/sleep
#: template keys come from the goal catalogue
#: (``docs/design/policies/customer-wellness-goal-setting-ux.md`` §
#: TEMPLATE_CHOICES); ``more_energy`` is the key the live goal layer
#: uses today (pinned in ``tests/test_goals.py``).
GOAL_KEYS_SLEEP: Final[tuple[str, ...]] = (
    "sleep_better",
    "sleep_rested_wake",
    "sleep_weekend_recovery",
)
GOAL_KEYS_ENERGY: Final[tuple[str, ...]] = (
    "energy_more_day",
    "energy_less_fatigue",
    "energy_morning_vigor",
    "more_energy",
)


@dataclass(frozen=True)
class Trigger:
    """A fired pattern: which one, and across how many days.

    Carries no wording and no timestamps (Q-10) — ``days`` exists for
    the planner's log detail, not for any sentence.
    """

    kind: str
    days: int


def late_dinner_trigger(
    goal_key: str,
    week: WeekPicture,
    *,
    tz: tzinfo,
) -> Trigger | None:
    """Dinner logged at/after 21:00 local in ≥3 of the last 7 days.

    «Late» is by the RECIPIENT's clock: ``logged_at`` arrives as a UTC
    ISO string and is converted with ``tz`` — the timezone the planner
    already resolved for the quiet-hours question, so the hint and the
    silence window cannot disagree about whose evening it is.
    """
    if goal_key not in GOAL_KEYS_SLEEP or not week.ok:
        return None
    days = sum(
        1
        for day in week.days
        if any(
            meal.meal_type == "dinner" and _logged_hour(meal.logged_at, tz) >= LATE_DINNER_HOUR
            for meal in day.diary.meals
        )
    )
    if days < TRIGGER_MIN_DAYS:
        return None
    return Trigger(kind="late_dinner", days=days)


def breakfast_trigger(goal_key: str, week: WeekPicture) -> Trigger | None:
    """A breakfast present in the diary in ≥3 of the last 7 days.

    Pure presence: no clock involved (a breakfast is a breakfast at any
    hour), no absence counted.
    """
    if goal_key not in GOAL_KEYS_ENERGY or not week.ok:
        return None
    days = sum(
        1 for day in week.days if any(meal.meal_type == "breakfast" for meal in day.diary.meals)
    )
    if days < TRIGGER_MIN_DAYS:
        return None
    return Trigger(kind="breakfasts", days=days)


def _logged_hour(logged_at: str, tz: tzinfo) -> int:
    """The local hour a meal was logged, or -1 when unknowable.

    Fail-closed towards silence: a timestamp we cannot read is a meal
    we cannot call late, never one we call late by default.
    """
    try:
        parsed = datetime.fromisoformat(logged_at.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return -1
    try:
        return parsed.astimezone(tz).hour
    except Exception:  # noqa: BLE001 — a broken tz must not break the tick
        logger.exception("nutrition_coach.triggers.tz_failed")
        return -1


def any_trigger(goal_key: str, week: WeekPicture, *, tz: tzinfo) -> Trigger | None:
    """The first matching trigger for this (goal, week), or None.

    The pairs are disjoint by goal family, so at most one can fire;
    the order is documentation, not priority.
    """
    return late_dinner_trigger(goal_key, week, tz=tz) or breakfast_trigger(goal_key, week)


__all__ = [
    "GOAL_KEYS_ENERGY",
    "GOAL_KEYS_SLEEP",
    "LATE_DINNER_HOUR",
    "TRIGGER_MIN_DAYS",
    "Trigger",
    "any_trigger",
    "breakfast_trigger",
    "late_dinner_trigger",
]
