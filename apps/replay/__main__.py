"""Replay CLI (DRF-520 / Sprint 5 / D7).

Usage:

    python -m apps.replay run \\
        --tenant formula-tela \\
        --fixture-set apps/replay/fixtures/golden \\
        [--strict-must-pass] [--strict-forbidden] \\
        [--report json | text]

    python -m apps.replay diff \\
        --baseline traces/baseline.json \\
        --candidate traces/candidate.json \\
        [--threshold-similarity 0.85]

### Exit codes

- 0 = all fixtures passed
- 1 = at least one fixture failed (or diff over threshold)
- 2 = usage error (bad args / missing fixture set)

### Pipeline_fn for `run`

The CLI builds a default pipeline_fn that wraps the existing Sprint 3
skill dispatch through a synthetic SkillContext. Sprint 6 will swap
this for the real ``pipeline.turn``. The runner doesn't care which.

### Tenant resolution

`--tenant <slug>` looks up the Tenant by slug + enters tenant_scope
for the duration of the run. Recorder reads `current_tenant()` so
ReplayTrace rows land under the right tenant.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _setup_django() -> None:
    """Bootstrap Django so `apps.*` imports work outside `manage.py`."""

    import os

    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()


def _build_default_pipeline_fn(*, tenant_slug: str) -> Any:
    """Default pipeline = thin wrapper around ``apps.orchestrator.pipeline.turn``.

    Sprint 6 / I3 (DRF-547) closes the Sprint 5 carry-over (DRF-525) —
    the prior synthetic SkillContext wiring lived against Sprint 3
    destructive skills (PrivacyConsentSkill.data_delete deleted the
    BotUser between fixtures, HumanHandoffSkill flipped Conversation
    state to HUMAN_HANDOFF and silenced everything afterwards). Sprint 6
    O1 (DRF-535) shipped a real ``turn()`` with per-turn isolation, so
    the CLI now drives REAL pipeline output for every fixture.

    The CLI's ``--isolated`` flag (I4 / DRF-548) wraps each fixture in
    a savepoint so per-fixture destructive side-effects don't leak —
    use it when running against Sprint 3 stub skills.

    ### Async ↔ sync bridging

    ``turn()`` is async; the Sprint 5 runner protocol expects a sync
    callable returning a trace dict. We bridge via ``asyncio.run`` —
    each fixture spins a fresh event loop. Safe because the runner
    invokes pipeline_fn serially.
    """

    def pipeline_fn(input_dict: dict[str, Any]) -> dict[str, Any]:
        import asyncio
        import uuid

        from apps.orchestrator.pipeline import ChannelMessage, turn

        text = str(input_dict.get("text", ""))
        channel = str(input_dict.get("channel", "max"))

        message = ChannelMessage(
            tenant_slug=tenant_slug,
            channel=channel,
            channel_user_id="replay-fixture-uid",
            chat_id="replay-fixture-chat",
            text=text,
            display_name="Replay Fixture",
            trace_id=str(uuid.uuid4()),
        )

        result = asyncio.run(turn(message))

        intent = result.intent
        reply = result.reply
        return {
            "intent": intent.intent if intent is not None else "",
            "skill_used": intent.skill if intent is not None else "",
            "safety_decision": result.pre_check_verdict or "allow",
            "response_text": reply.text if reply is not None else "",
            "tool_calls": [],
            "trace_id": result.trace_id,
            "ok": result.ok,
            "short_circuited_at_step": result.short_circuited_at_step,
        }

    return pipeline_fn


# --- run subcommand ----------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    from apps.replay.fixtures.loader import load_fixture_set
    from apps.replay.runner import Runner
    from apps.tenancy.context import tenant_scope
    from apps.tenancy.models import Tenant

    fixture_dir = Path(args.fixture_set)
    if not fixture_dir.is_dir():
        print(f"ERROR: fixture set {fixture_dir} is not a directory", file=sys.stderr)
        return 2

    try:
        tenant = Tenant.objects.get(slug=args.tenant)
    except Tenant.DoesNotExist:
        print(f"ERROR: tenant {args.tenant!r} not found", file=sys.stderr)
        return 2

    try:
        fixtures = load_fixture_set(fixture_dir)
    except ValueError as exc:
        print(f"ERROR: fixture load failed — {exc}", file=sys.stderr)
        return 2

    if not fixtures:
        print(f"WARN: no fixtures found in {fixture_dir}", file=sys.stderr)
        return 0

    runner = Runner()
    pipeline_fn = _build_default_pipeline_fn(tenant_slug=args.tenant)
    reports = []
    with tenant_scope(tenant):
        for fixture in fixtures:
            if args.isolated:
                # I4 / DRF-548 — per-fixture savepoint isolation.
                # Sprint 3 stub skills have destructive side-effects
                # (PrivacyConsentSkill.data_delete, HumanHandoffSkill
                # state flip); savepoint rollback after each fixture
                # keeps the next one starting from a clean slate.
                from django.db import transaction

                sid = transaction.savepoint()
                try:
                    report = runner.run(fixture, pipeline_fn=pipeline_fn)
                finally:
                    transaction.savepoint_rollback(sid)
                reports.append(report)
            else:
                report = runner.run(fixture, pipeline_fn=pipeline_fn)
                reports.append(report)

    return _render_reports(reports, args)


def _render_reports(reports: list[Any], args: argparse.Namespace) -> int:
    """Render reports to stdout in the requested format. Return exit code."""

    total = len(reports)
    passed = sum(1 for r in reports if r.passed)
    failed = total - passed

    if args.report == "json":
        payload = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "fixtures": [
                {
                    "name": r.fixture_name,
                    "passed": r.passed,
                    "trace_id": r.trace_id,
                    "failures": r.failures,
                    "voice_check_failures": r.voice_check_failures,
                }
                for r in reports
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        # text mode (default)
        for r in reports:
            status = "PASS" if r.passed else "FAIL"
            print(f"[{status}] {r.fixture_name}")
            for f in r.failures:
                print(f"    - {f}")
            for f in r.voice_check_failures:
                print(f"    - voice: {f}")
        print(f"\nSummary: {passed}/{total} passed, {failed} failed")

    # Exit-code logic per --strict flags.
    if args.strict_must_pass and any(r.failures for r in reports):
        return 1
    if args.strict_forbidden and any(any("forbidden" in f for f in r.failures) for r in reports):
        return 1
    if not args.strict_must_pass and not args.strict_forbidden:
        # Default: any failure → exit 1.
        return 0 if failed == 0 else 1
    return 0


# --- diff subcommand ---------------------------------------------------------


def _cmd_diff(args: argparse.Namespace) -> int:
    from apps.replay.differ import metrics_delta, text_similarity, tool_calls_diff

    baseline_path = Path(args.baseline)
    candidate_path = Path(args.candidate)
    if not baseline_path.is_file() or not candidate_path.is_file():
        print("ERROR: baseline or candidate path missing", file=sys.stderr)
        return 2

    with baseline_path.open("r", encoding="utf-8") as f:
        baseline = json.load(f)
    with candidate_path.open("r", encoding="utf-8") as f:
        candidate = json.load(f)

    similarity = text_similarity(
        str(baseline.get("response_text", "")), str(candidate.get("response_text", ""))
    )
    tc_diff = tool_calls_diff(
        list(baseline.get("tool_calls", []) or []),
        list(candidate.get("tool_calls", []) or []),
    )
    metrics = metrics_delta(baseline, candidate)

    print(
        json.dumps(
            {
                "text_similarity": similarity,
                "tool_calls_diff": tc_diff,
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if similarity < args.threshold_similarity:
        print(
            f"\nFAIL: similarity {similarity:.3f} < threshold {args.threshold_similarity}",
            file=sys.stderr,
        )
        return 1
    return 0


# --- main --------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m apps.replay")
    sub = p.add_subparsers(dest="subcommand", required=True)

    pr = sub.add_parser("run", help="Run a fixture set through the pipeline")
    pr.add_argument("--tenant", required=True, help="Tenant slug")
    pr.add_argument("--fixture-set", required=True, help="Path to fixture directory")
    pr.add_argument("--variant", default=None)
    pr.add_argument("--strict-must-pass", action="store_true")
    pr.add_argument("--strict-forbidden", action="store_true")
    pr.add_argument("--report", choices=["text", "json"], default="text")
    pr.add_argument(
        "--isolated",
        action="store_true",
        help=(
            "Wrap each fixture in a DB savepoint that rolls back after the "
            "run. Use when fixtures touch skills with destructive side "
            "effects (privacy data_delete, handoff state flip) so per-"
            "fixture state doesn't leak. Sprint 6 / I4."
        ),
    )
    pr.set_defaults(func=_cmd_run)

    pd = sub.add_parser("diff", help="Diff two captured traces")
    pd.add_argument("--baseline", required=True)
    pd.add_argument("--candidate", required=True)
    pd.add_argument("--threshold-similarity", type=float, default=0.85)
    pd.set_defaults(func=_cmd_diff)

    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    _setup_django()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
