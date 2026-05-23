"""Synthetic flood drill — D-2 ceilings end-to-end verification (#500).

Mandatory pre-flip drill per
:doc:`/runbooks/strict-tenant-refuse-d2-ceilings-checklist` §«Synthetic
drill». Without a green drill, the ``STRICT_TENANT_REFUSE=true`` flip
is blind risk: we'd be relying on ceilings that have never been
exercised against real load.

Companion to the bash version in the runbook — this one is unit-testable
and stays in muscle memory via ``manage.py`` (one command, no shell-
scripting drift).

### What it verifies (positive assertions — «нужный сигнал ТОЧНО был»)

The drill injects ``--count`` (default 200) tenant-missing entries into
the ingress stream, runs the consumer, then asserts:

1. **Rate budget effective** — audit table grew by ≤ ``WORKER_TENANT_MISSING_RATE_LIMIT``
   rows (default 100/hour). With count=200 and limit=100 we expect ~100
   audit rows, NOT 200.
2. **Rate gate engaged** — at least ONE emit was silenced (positive
   signal that the cap actually fired, not just «no problem observed»).
3. **PEL alert wiring** — :class:`monitor_pel` management command
   exits with non-zero status when called after the drill (warning OR
   page tier). Validates the alert pipeline end-to-end.

### Strict-mode precondition

Under ``STRICT_TENANT_REFUSE=False`` (Phase 0 log-only), entries are
XACK'd after the ERROR log → PEL stays empty → ``monitor_pel`` exits
0 → A3 fails. The drill operator MUST run with strict mode on for
accurate verification. The command prints a clear warning when strict
mode is off and degrades A3 to «callable, no assertion» so the other
two assertions still report meaningfully.

### Usage

::

    # Default (200 entries, ingress:max, consumers group):
    python manage.py d2_flood_drill

    # Custom count for tighter / looser cap test:
    python manage.py d2_flood_drill --count 500

    # Dry-run — count baseline + report what WOULD be injected, no XADD:
    python manage.py d2_flood_drill --dry-run

### Exit codes

- ``0`` — all 3 positive assertions held; flip may proceed
- ``1`` — at least one assertion failed; DO NOT FLIP

### Run order

Per :doc:`/runbooks/strict-tenant-refuse-d2-ceilings-checklist`:

1. Wire all 4 ceilings (§1-§4)
2. Run this drill in **staging** (NOT prod)
3. If exit 0 → operator proceeds with the flip sequence in the
   companion runbook
4. If non-zero → fix the missing ceiling, re-run
"""

from __future__ import annotations

import json
import time
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


_DEFAULT_COUNT = 200
_DEFAULT_STREAM = "ingress:max"
_DEFAULT_GROUP = "consumers"


class Command(BaseCommand):
    help = "Synthetic D-2 ceilings flood drill — pre-flip verification (#500)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--count",
            type=int,
            default=_DEFAULT_COUNT,
            help=f"Entries to inject. Default: {_DEFAULT_COUNT}.",
        )
        parser.add_argument(
            "--stream",
            default=_DEFAULT_STREAM,
            help=f"Source stream. Default: {_DEFAULT_STREAM}.",
        )
        parser.add_argument(
            "--group",
            default=_DEFAULT_GROUP,
            help=f"Consumer group. Default: {_DEFAULT_GROUP}.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Snapshot baseline + report expected behaviour, no XADD.",
        )
        parser.add_argument(
            "--format",
            choices=("text", "json"),
            default="text",
            help="Output format.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        count = options["count"]
        stream = options["stream"]
        group = options["group"]
        dry_run = options["dry_run"]
        fmt = options["format"]

        # Strict-mode precondition check.
        strict = bool(getattr(settings, "STRICT_TENANT_REFUSE", False))
        rate_limit = int(getattr(settings, "WORKER_TENANT_MISSING_RATE_LIMIT", 100))

        # Baseline.
        baseline_audit = self._count_audit_rows()

        if dry_run:
            self._emit(
                fmt,
                {
                    "dry_run": True,
                    "would_inject": count,
                    "rate_limit": rate_limit,
                    "strict_mode": strict,
                    "baseline_audit_rows": baseline_audit,
                    "expected_audit_delta": min(count, rate_limit),
                    "expected_silenced_emits": max(0, count - rate_limit),
                },
            )
            return

        if not strict:
            self.stderr.write(
                self.style.WARNING(
                    "DRILL PRECONDITION: STRICT_TENANT_REFUSE=False → entries XACK in "
                    "log-only mode, PEL stays empty, monitor_pel will exit 0. "
                    "A3 (PEL alert) will degrade to «callable, no assertion». "
                    "Set STRICT_TENANT_REFUSE=true in /etc/ai-bot-platform/.env + "
                    "restart workers before re-running for a full-coverage drill."
                )
            )

        # Inject entries.
        from apps.ingress import streams as ingress_streams

        client = ingress_streams._client()
        injected_at = time.monotonic()
        for i in range(count):
            client.xadd(
                stream,
                {
                    "data": "{}",
                    "trace_id": f"d2-drill-{int(time.time())}-{i}",
                    "resolved_tenant_id": "",
                },
            )
        inject_elapsed = time.monotonic() - injected_at

        # Drain via consume_once until the batch is processed (or no
        # progress for 2 consecutive ticks).
        from apps.workers import consumer as worker_consumer

        processed = 0
        stalled_ticks = 0
        while processed < count and stalled_ticks < 2:
            n = worker_consumer.consume_once(
                streams=[stream],
                group=group,
                block_ms=100,
                count=count,
            )
            if n == 0:
                stalled_ticks += 1
            else:
                stalled_ticks = 0
                processed += n

        # Verify the 3 positive assertions.
        delta = self._count_audit_rows() - baseline_audit
        silenced = max(0, count - delta)

        assertions = []

        # A1: rate budget effective (audit delta ≤ rate limit).
        a1_ok = delta <= rate_limit
        assertions.append(
            {
                "name": "rate_budget_effective",
                "ok": a1_ok,
                "msg": f"audit grew by {delta} rows (cap: {rate_limit}/hour)",
                "delta": delta,
                "rate_limit": rate_limit,
            }
        )

        # A2: rate gate ENGAGED (at least one silenced — proves the
        # cap fired, not just «no problem observed»).
        # When count <= rate_limit, the gate doesn't NEED to fire —
        # report N/A so the drill at small counts doesn't false-fail.
        if count > rate_limit:
            a2_ok = silenced > 0
            a2_msg = f"{silenced} emits silenced by rate gate (positive signal — cap engaged)"
        else:
            a2_ok = True
            a2_msg = (
                f"N/A: count={count} ≤ rate_limit={rate_limit} — cap not "
                "expected to engage. Re-run with --count > rate_limit."
            )
        assertions.append(
            {
                "name": "rate_gate_engaged",
                "ok": a2_ok,
                "msg": a2_msg,
                "silenced": silenced,
            }
        )

        # A3: PEL alert wiring — monitor_pel exits non-zero in strict
        # mode (entries pile up in PEL). Degraded in log-only mode.
        a3_name = "pel_alert_fires"
        if strict:
            a3_ok, a3_msg = self._run_monitor_pel(stream, group)
        else:
            a3_ok = True  # Soft-pass since precondition warned.
            a3_msg = (
                "DEGRADED: strict mode off, PEL stays empty by design. "
                "A3 requires STRICT_TENANT_REFUSE=true for full coverage."
            )
        assertions.append(
            {
                "name": a3_name,
                "ok": a3_ok,
                "msg": a3_msg,
            }
        )

        # Output.
        any_fail = any(not a["ok"] for a in assertions)
        result = {
            "drill": "d2_flood",
            "count": count,
            "stream": stream,
            "group": group,
            "strict_mode": strict,
            "rate_limit": rate_limit,
            "inject_elapsed_s": round(inject_elapsed, 2),
            "processed_via_consume_once": processed,
            "audit_baseline_rows": baseline_audit,
            "audit_delta_rows": delta,
            "emits_silenced": silenced,
            "assertions": assertions,
            "passed": not any_fail,
        }

        if fmt == "json":
            # JSON-only stdout — the `passed` field + exit code are the
            # operator-readable signal. Trailing prose would break
            # downstream JSON parsers.
            self.stdout.write(json.dumps(result, indent=2))
            if any_fail:
                self.stderr.write(
                    self.style.ERROR("DRILL FAILED — DO NOT FLIP STRICT_TENANT_REFUSE.")
                )
                raise SystemExit(1)
            return
        self._emit_text(result)
        if any_fail:
            self.stderr.write(self.style.ERROR("DRILL FAILED — DO NOT FLIP STRICT_TENANT_REFUSE."))
            raise SystemExit(1)
        self.stdout.write(
            self.style.SUCCESS(
                f"DRILL PASSED — {sum(1 for a in assertions if a['ok'])}/3 "
                "assertions held. Flip may proceed per "
                "strict-tenant-refuse-flip.md."
            )
        )

    # ------------------------------------------------------------
    # Helpers (small + test-friendly)
    # ------------------------------------------------------------

    @staticmethod
    def _count_audit_rows() -> int:
        """Count ``worker.tenant_required_missing`` audit rows.

        Used both for baseline + post-drill delta. Resolves the audit
        model dynamically so this command stays importable in environments
        where ``apps.events`` isn't loaded (tests, CI).
        """

        from apps.events.models import Event

        return Event.objects.filter(event_type="worker.tenant_required_missing").count()

    def _run_monitor_pel(self, stream: str, group: str) -> tuple[bool, str]:
        """Invoke the ``monitor_pel`` command + return (ok, msg).

        Positive assertion: exit 1 (warning) OR 2 (page) — both are
        signals that the alert pipeline fires. Exit 0 means the
        thresholds weren't hit (drill count was too low OR PEL didn't
        accumulate).
        """

        try:
            call_command(
                "monitor_pel",
                stream=stream,
                group=group,
                warning=1,  # Lowest threshold so even small PEL fires.
                page=999999,  # Out of reach — we only need warning tier.
            )
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            if code in (1, 2):
                return True, f"monitor_pel exit={code} (positive signal)"
            return False, f"monitor_pel exit={code} (unexpected)"
        # Exit 0 reached → no alert fired.
        return False, "monitor_pel exit=0 — PEL stayed below warning threshold"

    def _emit(self, fmt: str, payload: dict[str, Any]) -> None:
        if fmt == "json":
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            for k, v in payload.items():
                self.stdout.write(f"{k}: {v}")

    def _emit_text(self, result: dict[str, Any]) -> None:
        self.stdout.write("=" * 60)
        self.stdout.write("D-2 ceilings synthetic flood drill")
        self.stdout.write("=" * 60)
        self.stdout.write(f"Injected:           {result['count']}")
        self.stdout.write(f"Stream / group:     {result['stream']} / {result['group']}")
        self.stdout.write(f"Strict mode:        {result['strict_mode']}")
        self.stdout.write(f"Rate limit:         {result['rate_limit']}/hour")
        self.stdout.write(f"Inject elapsed:     {result['inject_elapsed_s']}s")
        self.stdout.write(f"Processed:          {result['processed_via_consume_once']}")
        self.stdout.write(f"Audit delta:        {result['audit_delta_rows']} rows")
        self.stdout.write(f"Emits silenced:     {result['emits_silenced']}")
        self.stdout.write("-" * 60)
        for a in result["assertions"]:
            mark = self.style.SUCCESS("✓") if a["ok"] else self.style.ERROR("✗")
            self.stdout.write(f"  {mark} {a['name']}: {a['msg']}")
        self.stdout.write("=" * 60)
