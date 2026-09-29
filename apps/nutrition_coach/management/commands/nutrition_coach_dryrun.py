"""Show who the coach_hint beat would write to, and why (DRF-1464, T5).

Runs the *same* planner the beat task runs --
:func:`apps.nutrition_proactive.coach.plan_coach_hints` -- not a parallel
reimplementation, and prints one line per candidate. Nothing is sent and
nothing is written: the command never calls the delivery path and never
persists a preference update, so it is safe against the live pilot
database.

Usage::

    python manage.py nutrition_coach_dryrun
    python manage.py nutrition_coach_dryrun --at 2026-09-06T10:40:00+03:00
    python manage.py nutrition_coach_dryrun --no-ayla

``--at`` evaluates the plan at an arbitrary instant, which is how the
quiet-hours gate gets checked without waiting for the clock. ``--no-ayla``
substitutes stub reads (a sleep goal, a week with three late dinners, a
clean profile) so the whole ladder through ``due`` can be inspected on an
environment where the Ayla token is not configured -- the stub is
deliberately a FULL-CLEAR person: what is inspected is the gate logic,
and a stub that blocks at its own step would show nothing past it.
"""

from __future__ import annotations

import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.integrations.ayla import ProfileResponse
from apps.nutrition_coach import flags
from apps.nutrition_coach.goals import Goal
from apps.nutrition_coach.history import DayPicture, WeekPicture, WeekStatus
from apps.nutrition_proactive import coach
from apps.orchestrator.food_history import Meal, Status, TodayDiary

_STUB_GOAL = Goal(key="sleep_better", text=None)

_STUB_PROFILE = ProfileResponse(
    gender="female",
    age=31,
    height_cm=168,
    weight_kg=62,
    goal="maintain",
    daily_kcal=1994,
    protein_g=128,
    fat_g=66,
    carbs_g=221,
    water_ml=2400,
    bmr=1400,
    health_flags={},
    disclaimer_acked=None,
    goal_overridden_by=None,
    raw={},
)

_STUB_WEEK = WeekPicture(
    status=WeekStatus.OK,
    days=tuple(
        DayPicture(
            date=f"2026-09-0{i + 1}",
            diary=TodayDiary(
                Status.OK,
                (
                    Meal(
                        dish="Ужин",
                        calories=400,
                        meal_type="dinner",
                        logged_at="2026-09-04T18:30:00Z",
                    ),
                ),
            ),
        )
        for i in range(3)
    ),
)


class Command(BaseCommand):
    help = "Dry-run the coach_hint planner: list recipients, send nothing."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--at",
            default=None,
            help="ISO-8601 instant to evaluate at (default: now). Must be tz-aware.",
        )
        parser.add_argument(
            "--no-ayla",
            action="store_true",
            help="Use stub goal/week/profile reads instead of calling Ayla.",
        )

    def handle(self, *args, **options) -> None:
        now = None
        if options["at"]:
            try:
                now = datetime.fromisoformat(options["at"])
            except ValueError as exc:
                raise CommandError(f"--at is not ISO-8601: {exc}") from exc
            if now.tzinfo is None:
                raise CommandError("--at must carry a timezone offset")

        stubs: dict = {}
        if options["no_ayla"]:
            stubs = {
                "fetch_goal": lambda _bot_user: _STUB_GOAL,
                "fetch_history": lambda _bot_user: _STUB_WEEK,
                "fetch_profile": lambda _bot_user: _STUB_PROFILE,
            }

        decisions = coach.plan_coach_hints(now_utc=now, **stubs)
        would_send = [d for d in decisions if d.send]
        self.stdout.write(
            f"== coach_hint: {len(decisions)} candidates, "
            f"{len(would_send)} would receive a message =="
        )
        for decision in decisions:
            self.stdout.write("  " + json.dumps(decision.as_log(), ensure_ascii=False))
        self.stdout.write(
            f"   flags: NUTRITION_COACH_ENABLED={flags.enabled()} "
            f"NUTRITION_COACH_DRY_RUN={flags.dry_run()}"
        )
