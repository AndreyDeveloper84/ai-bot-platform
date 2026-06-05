"""Snapshot apps_audit_event table size + row count (issue #500 Item 3).

Wires the adversarial-pass D-2 "audit-table size baseline" item from
``docs/runbooks/strict-tenant-refuse-flip.md``. The operator captures
this snapshot pre-flip and sets a monitoring alert at 2× the baseline
growth rate for the 24h post-flip window.

### Usage

::

    # Print the snapshot in text format.
    python manage.py audit_table_baseline

    # JSON output for ingestion into the post-flip monitoring config.
    python manage.py audit_table_baseline --format json

    # Snapshot a different table (e.g. events).
    python manage.py audit_table_baseline --table apps_events_event

### What it reports

For the target table:

- ``row_count`` — exact ``COUNT(*)``. Slow on big tables; this is a
  one-shot pre-flip snapshot, not a hot-path query.
- ``total_size_bytes`` — ``pg_total_relation_size`` (heap + indexes +
  TOAST).
- ``table_size_bytes`` — heap only.
- ``indexes_size_bytes`` — sum of all indexes.

These numbers go into the operator's monitoring config as the
baseline; the alert fires when 24h post-flip growth exceeds 2× the
expected steady-state delta.

### Postgres-only

Uses ``pg_total_relation_size`` etc — won't work on SQLite. The dev /
prod targets all run Postgres 16 per ``settings/dev.py``; SQLite is
test-only and unaffected by audit-table growth (different table).
"""

from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


# Default target — the table that catches the post-flip emit fanout.
_DEFAULT_TABLE = "apps_audit_event"


class Command(BaseCommand):
    help = "Snapshot apps_audit_event table size + row count for pre-flip baseline (#500)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--table",
            default=_DEFAULT_TABLE,
            help=f"Target table. Default: {_DEFAULT_TABLE}",
        )
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        table = options["table"]
        fmt = options["format"]

        if connection.vendor != "postgresql":
            raise CommandError(
                f"audit_table_baseline requires Postgres (current vendor: "
                f"{connection.vendor}). Sizes via pg_total_relation_size "
                "are PG-specific; SQLite test runs don't need a baseline."
            )

        # Quote the identifier defensively (we already control the value,
        # but the SQL fragment goes through pg_size_pretty's argument).
        # The standard Python adapter does NOT support identifier
        # placeholders, so we whitelist-check the table name shape first.
        if not table.replace("_", "").isalnum():
            raise CommandError(
                f"refusing to query unsafe table name {table!r}; expected snake_case identifier"
            )

        with connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            row_count = cursor.fetchone()[0]

            cursor.execute(
                "SELECT pg_total_relation_size(%s), pg_relation_size(%s), pg_indexes_size(%s)",
                [table, table, table],
            )
            total, heap, indexes = cursor.fetchone()

        snapshot = {
            "table": table,
            "row_count": int(row_count),
            "total_size_bytes": int(total),
            "table_size_bytes": int(heap),
            "indexes_size_bytes": int(indexes),
        }

        if fmt == "json":
            self.stdout.write(json.dumps(snapshot, sort_keys=True))
        else:
            self.stdout.write(f"table:            {snapshot['table']}")
            self.stdout.write(f"row_count:        {snapshot['row_count']:,}")
            self.stdout.write(f"total_size:       {_human_bytes(snapshot['total_size_bytes'])}")
            self.stdout.write(f"table_size:       {_human_bytes(snapshot['table_size_bytes'])}")
            self.stdout.write(f"indexes_size:     {_human_bytes(snapshot['indexes_size_bytes'])}")


def _human_bytes(n: int) -> str:
    """Render a byte count in MB / GB for the text-format output."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"
