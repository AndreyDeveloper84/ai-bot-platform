"""Show who the post-visit follow-up would write to, and why (DRF-1301).

Runs the *same* planner the beat runs -- not a parallel reimplementation --
and prints one line per candidate. Nothing is sent and nothing is written:
the command never calls the delivery path and never bumps an idempotency
key, so it is safe against the live pilot database.

Usage::

    python manage.py post_visit_followup_dryrun
    python manage.py post_visit_followup_dryrun --at 2026-08-23T19:00:00+03:00
    python manage.py post_visit_followup_dryrun --explain-empty

``--at`` evaluates the plan at an arbitrary instant. The task's window is
"yesterday, Moscow-local", so without it a dry run on a quiet day is
indistinguishable from a broken selection -- which is the trap this
command's ``--explain-empty`` mode exists to close.

### Why ``--explain-empty``

A dry run that lists zero recipients means one of two very different
things: the gate is working, or the feature is dead. Reporting the first
when it was the second is the specific dishonesty this ticket was told to
avoid. ``--explain-empty`` separates them by counting backwards through
the funnel -- reminders in the window at all, then how many survive each
filter -- so "0 recipients" arrives with the reason attached.
"""

from __future__ import annotations

import json
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.bookings import followups


class Command(BaseCommand):
    help = "Dry-run the post-visit follow-up: list recipients, send nothing."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--at",
            default=None,
            help="ISO-8601 instant to evaluate at (default: now). Must be tz-aware.",
        )
        parser.add_argument(
            "--explain-empty",
            action="store_true",
            help="Break the selection funnel down so an empty result says why.",
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

        effective = now or timezone.now()
        window_start, window_end, today_msk = followups._moscow_day_window(effective)

        self.stdout.write(
            f"== post_visit_followup @ {effective.isoformat()} "
            f"(window {window_start.isoformat()} .. {window_end.isoformat()}, "
            f"idempotency date {today_msk.isoformat()}) =="
        )

        decisions = followups.plan_post_visit_followups(now_utc=effective)
        would_send = [d for d in decisions if d.send]
        self.stdout.write(
            f"   {len(decisions)} candidates, {len(would_send)} would receive a message"
        )
        for decision in decisions:
            self.stdout.write("  " + json.dumps(decision.as_log(), ensure_ascii=False))

        if options["explain_empty"]:
            self._explain(window_start, window_end)

        self.stdout.write(
            f"   flags: POST_VISIT_FOLLOWUP_ENABLED={followups.enabled()} "
            f"POST_VISIT_FOLLOWUP_DRY_RUN={followups.dry_run()}"
        )
        if not followups.enabled():
            self.stdout.write(
                "   NOTE: the beat is disabled, so it would send nothing regardless "
                "of the plan above."
            )

    def _explain(self, window_start, window_end) -> None:
        """Count the funnel so an empty plan carries its own diagnosis."""
        from apps.booking.models import BookingReminder
        from apps.identity.models import BotUser

        in_window = BookingReminder.all_tenants.filter(
            visit_at__gte=window_start,
            visit_at__lt=window_end,
        )
        not_cancelled = in_window.exclude(status=BookingReminder.Status.CANCELLED)

        self.stdout.write("   -- selection funnel --")
        self.stdout.write(f"   reminders with visit_at in window: {in_window.count()}")
        self.stdout.write(f"   ... after excluding CANCELLED:     {not_cancelled.count()}")
        for label, kwargs in (
            ("... with an opted-in BotUser", {"bot_user__proactive_messages_opt_out": False}),
            ("... not soft-deleted", {"bot_user__deleted_at__isnull": True}),
            ("... with consent_at set", {"bot_user__consent_at__isnull": False}),
        ):
            self.stdout.write(f"   {label}: {not_cancelled.filter(**kwargs).count()}")
        surviving = len(followups._eligible_reminders(window_start, window_end))
        self.stdout.write(f"   ... entering the plan (opt-out + erasure applied): {surviving}")

        # The population-level numbers, so "nobody in the window" is not
        # mistaken for "nobody could ever qualify".
        self.stdout.write("   -- population --")
        self.stdout.write(f"   BotUsers total: {BotUser.all_tenants.count()}")
        self.stdout.write(
            "   ... opted out of proactive: "
            f"{BotUser.all_tenants.filter(proactive_messages_opt_out=True).count()}"
        )
        self.stdout.write(
            "   ... with consent_at set: "
            f"{BotUser.all_tenants.filter(consent_at__isnull=False).count()}"
        )
        self.stdout.write(
            "   ... reachable (non-empty chat_id): "
            f"{BotUser.all_tenants.exclude(chat_id='').exclude(chat_id__isnull=True).count()}"
        )
