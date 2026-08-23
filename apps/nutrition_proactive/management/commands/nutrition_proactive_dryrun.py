"""Show who the proactive nutrition tasks would write to, and why (DRF-1285).

Runs the *same* planners the beat tasks run -- not a parallel reimplementation
-- and prints one line per candidate. Nothing is sent and nothing is written:
the command never calls the delivery path and never persists a preference
update, so it is safe against the live pilot database.

Usage::

    python manage.py nutrition_proactive_dryrun
    python manage.py nutrition_proactive_dryrun --at 2026-08-23T12:00:00+03:00
    python manage.py nutrition_proactive_dryrun --task water --no-ayla

``--at`` evaluates the plan at an arbitrary instant, which is how the quiet
hours and the proportional threshold get checked without waiting for the
clock. ``--no-ayla`` substitutes a stub reader so the selection and time
logic can be inspected on an environment where the nutrition service token
is not configured -- which, as of 2026-08-23, is the pilot bot host.
"""

from __future__ import annotations

import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from apps.integrations.ayla import SummaryResponse, WaterTodayResponse
from apps.nutrition_proactive import tasks

_STUB_WATER = WaterTodayResponse(total_ml=0, norm_ml=0, entries=[])
_STUB_SUMMARY = SummaryResponse(
    date="",
    calories_total=0.0,
    calories_goal=0,
    protein_g=0.0,
    fat_g=0.0,
    carbs_g=0.0,
    entries=[],
    raw={},
)


class Command(BaseCommand):
    help = "Dry-run the proactive nutrition tasks: list recipients, send nothing."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--task",
            choices=["report", "water", "both"],
            default="both",
        )
        parser.add_argument(
            "--at",
            default=None,
            help="ISO-8601 instant to evaluate at (default: now). Must be tz-aware.",
        )
        parser.add_argument(
            "--no-ayla",
            action="store_true",
            help="Use a stub nutrition reader instead of calling Ayla.",
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

        which = options["task"]
        if which in ("report", "both"):
            self._report(
                "daily_report",
                tasks.plan_daily_reports(
                    now_utc=now,
                    fetch=(lambda _ext: (_STUB_SUMMARY, _STUB_WATER, None))
                    if options["no_ayla"]
                    else None,
                ),
            )
        if which in ("water", "both"):
            self._report(
                "water_reminder",
                tasks.plan_water_reminders(
                    now_utc=now,
                    fetch=(lambda _ext: _STUB_WATER) if options["no_ayla"] else None,
                ),
            )

    def _report(self, label: str, decisions) -> None:
        would_send = [d for d in decisions if d.send]
        self.stdout.write(
            f"== {label}: {len(decisions)} candidates, {len(would_send)} would receive a message =="
        )
        for decision in decisions:
            self.stdout.write("  " + json.dumps(decision.as_log(), ensure_ascii=False))
        self.stdout.write(
            f"   flags: NUTRITION_PROACTIVE_ENABLED={tasks.enabled()} "
            f"NUTRITION_PROACTIVE_DRY_RUN={tasks.dry_run()}"
        )
