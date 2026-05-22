"""Monitor Redis Streams PEL length + optionally alert (issue #500 Item 1).

Wires the adversarial-pass D-2 "PEL length alert" item from
``docs/runbooks/strict-tenant-refuse-flip.md``. The PEL (Pending Entries
List) grows by one for every consumed-but-unACKed entry; under
``STRICT_TENANT_REFUSE=True`` with a misbehaving ingress, this can grow
unboundedly and exhaust Redis memory.

### Usage

::

    # Print the count, exit 0 (no thresholds).
    python manage.py monitor_pel

    # Check against thresholds; exit 1 = warning, 2 = page.
    python manage.py monitor_pel --warning 1000 --page 5000

    # JSON output for ingestion into Prometheus / Grafana.
    python manage.py monitor_pel --format json

    # Custom stream + group (defaults match the consumer in production).
    python manage.py monitor_pel --stream ingress:max --group consumers

### Exit codes (when --warning / --page set)

- ``0`` — PEL count below ``--warning`` threshold
- ``1`` — at or above ``--warning`` but below ``--page``
- ``2`` — at or above ``--page``
- Non-zero from a healthy script means the monitoring stack should
  alert. Document this in the alert config wired by the operator.

The runbook §«Adversarial-pass D-2 — operational ceilings» calls for
N=1000 (warning) / N=5000 (page) on the ``ingress:max`` stream under
the ``consumers`` group; defaults match.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from django.core.management.base import BaseCommand


# Defaults per runbook §«D-2 operational ceilings».
_DEFAULT_STREAM = "ingress:max"
_DEFAULT_GROUP = "consumers"


class Command(BaseCommand):
    help = "Monitor Redis Streams PEL length + alert when thresholds breached (#500)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--stream",
            default=_DEFAULT_STREAM,
            help=f"Redis stream key. Default: {_DEFAULT_STREAM}",
        )
        parser.add_argument(
            "--group",
            default=_DEFAULT_GROUP,
            help=f"Consumer group name. Default: {_DEFAULT_GROUP}",
        )
        parser.add_argument(
            "--warning",
            type=int,
            default=None,
            help="PEL length threshold for warning (exit 1). Per runbook: 1000.",
        )
        parser.add_argument(
            "--page",
            type=int,
            default=None,
            help="PEL length threshold for paging (exit 2). Per runbook: 5000.",
        )
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format. JSON for monitoring stack ingestion.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        stream = options["stream"]
        group = options["group"]
        warning = options["warning"]
        page = options["page"]
        fmt = options["format"]

        from apps.ingress.streams import _client

        redis = _client()

        # XPENDING returns a dict on redis-py: {"pending": int, "min": id, "max": id, "consumers": [...]}
        # When the group doesn't exist OR the stream doesn't exist, redis-py
        # may raise — treat that as "0 pending" rather than crash, so a
        # cold-start monitor doesn't page on a not-yet-provisioned stream.
        try:
            info = redis.xpending(stream, group)
            pending = int(info.get("pending", 0)) if isinstance(info, dict) else 0
        except Exception as exc:  # noqa: BLE001
            # Distinguish "group doesn't exist" (transient pre-bootstrap)
            # from real Redis trouble. redis-py raises ResponseError with
            # "NOGROUP" in the message for the former.
            if "NOGROUP" in str(exc):
                pending = 0
            else:
                # Real fault — surface for operator. Exit non-zero so
                # monitoring catches it.
                if fmt == "json":
                    self.stdout.write(
                        json.dumps({"error": str(exc), "stream": stream, "group": group})
                    )
                else:
                    self.stderr.write(f"redis error: {exc}")
                sys.exit(3)

        # Pick severity.
        severity = "ok"
        exit_code = 0
        if page is not None and pending >= page:
            severity = "page"
            exit_code = 2
        elif warning is not None and pending >= warning:
            severity = "warning"
            exit_code = 1

        if fmt == "json":
            self.stdout.write(
                json.dumps(
                    {
                        "stream": stream,
                        "group": group,
                        "pending": pending,
                        "severity": severity,
                        "warning_threshold": warning,
                        "page_threshold": page,
                    }
                )
            )
        else:
            line = f"stream={stream} group={group} pending={pending} severity={severity}"
            if warning is not None or page is not None:
                line += f" (warning={warning} page={page})"
            self.stdout.write(line)

        if exit_code:
            sys.exit(exit_code)
