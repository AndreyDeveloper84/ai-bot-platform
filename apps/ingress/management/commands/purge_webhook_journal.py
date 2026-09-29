"""Clear the WebhookJournal backlog accumulated before DRF-2242.

The hourly ``sweep_webhook_journal`` only takes the rolling edge — rows that
crossed their term in the last week — so the history recorded before the
retention existed is untouched until someone decides about it. This command
is that decision's instrument:

* default — **dry run**: counts what would go, changes nothing;
* ``--apply`` — blanks bodies past ``INGRESS_RAW_RETENTION_HOURS`` and deletes
  rows past ``WEBHOOK_JOURNAL_ROW_RETENTION_DAYS``. Irreversible; run only on
  the owner's word.

Prints counts only, never payloads.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ingress.retention import backlog, payload_hours, row_days


class Command(BaseCommand):
    help = "Clear the WebhookJournal backlog (dry run by default; --apply on the owner's word)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually blank bodies and delete rows. Without it: dry run.",
        )

    def handle(self, *args, **options) -> None:
        bodies, rows = backlog()
        payloads_n = bodies.count()
        rows_n = rows.count()
        terms = f"body>{payload_hours()}h row>{row_days()}d"
        if not options["apply"]:
            self.stdout.write(
                f"DRY-RUN {terms}: payloads={payloads_n} rows={rows_n} — nothing changed"
            )
            return
        with transaction.atomic():
            blanked = bodies.update(raw_payload={})
            deleted, _ = rows.delete()
        self.stdout.write(f"APPLIED {terms}: payloads={blanked} rows={deleted}")
