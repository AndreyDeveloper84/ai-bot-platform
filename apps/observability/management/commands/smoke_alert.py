"""Smoke test for the alerting pipeline (Sprint 10 / O3 / DRF-864).

Sends a synthetic page through :func:`apps.observability.alerting.page`
to confirm the Telegram channel + Sentry hookup work end-to-end.

Operator workflow:

* Run before promoting any alerting code → prod.
* Run after rotating ``TELEGRAM_BOT_TOKEN`` or moving alert channel.
* Run when on-call rotation changes — proves the new operator gets
  the page on their phone with sound.

Usage::

    python manage.py smoke_alert --severity=critical --title="O3 smoke test"
    python manage.py smoke_alert --severity=warning --title="..." --body="..."
    python manage.py smoke_alert --severity=error --dry-run

By default the smoke page is content-tagged with timestamp + hostname
so repeated runs don't dedup against each other.

Exit codes:
  * 0 — page returned True (at least one sink succeeded)
  * 1 — page returned False (no sink reachable; see logs)
  * 2 — bad args (CommandError)
"""

from __future__ import annotations

import datetime as _dt
import socket
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.observability.alerting import page

_VALID_SEVERITIES = ("critical", "error", "warning")


class Command(BaseCommand):
    help = "Send a synthetic alert through apps.observability.alerting.page (Sprint 10 / O3)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--severity",
            choices=_VALID_SEVERITIES,
            default="warning",
            help=(
                "Alert severity. Default warning so accidental runs don't wake "
                "the on-call. Use critical/error only intentionally."
            ),
        )
        parser.add_argument(
            "--title",
            default="alerting smoke test",
            help="Headline string. Goes into Telegram bold line + Sentry message.",
        )
        parser.add_argument(
            "--body",
            default="",
            help="Body text. Default = host/time stamp so reruns don't dedup.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be sent, no network calls.",
        )

    def handle(self, *args: Any, **opts: Any) -> None:
        severity = opts["severity"]
        if severity not in _VALID_SEVERITIES:
            raise CommandError(f"Invalid severity {severity}")

        title = str(opts["title"]).strip()
        if not title:
            raise CommandError("--title must be non-empty")

        body = str(opts["body"]).strip()
        if not body:
            body = _default_body()

        if opts["dry_run"]:
            self.stdout.write(
                self.style.NOTICE(f"[dry-run] severity={severity}\ntitle={title}\nbody={body}\n")
            )
            return

        # Use a per-run dedup_key so repeated smokes within the dedup
        # window still page (operators expect to see every run).
        dedup_key = f"smoke:{_dt.datetime.now(tz=_dt.timezone.utc).isoformat()}"
        ok = page(severity, title, body, dedup_key=dedup_key)

        if ok:
            self.stdout.write(
                self.style.SUCCESS(
                    "page() returned True — at least one sink accepted the "
                    "event. Check your Telegram channel + Sentry within 60s."
                )
            )
            return

        self.stdout.write(
            self.style.ERROR(
                "page() returned False — no sink delivered. Likely causes:\n"
                "  * TELEGRAM_BOT_TOKEN or ALERTS_TELEGRAM_CHAT_ID unset\n"
                "  * SENTRY_DSN unset AND Telegram unset\n"
                "  * api.telegram.org unreachable (RU prod needs TELEGRAM_PROXY/OPENAI_PROXY)\n"
                "  * Telegram bot not added to the alert channel as admin\n"
                "Check apps/observability/alerting.py logs for the underlying error."
            )
        )
        sys.exit(1)


def _default_body() -> str:
    host = socket.gethostname()
    ts = _dt.datetime.now(tz=_dt.timezone.utc).isoformat()
    return (
        f"Smoke test from {host} at {ts}.\n"
        "If you see this in Telegram, the alerting pipeline works "
        "end-to-end. If you also see this in Sentry, the dual-sink "
        "fan-out is fine."
    )
