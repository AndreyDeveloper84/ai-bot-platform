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


def _build_default_pipeline_fn() -> Any:
    """Default pipeline = thin wrapper around Sprint 3 skill dispatch.

    Sprint 6 replaces this with ``apps.orchestrator.pipeline.turn``.
    Used by ``run`` when no custom pipeline_fn is provided.
    """

    from apps.skills.base import SkillContext
    from apps.skills.registry import dispatch as skill_dispatch
    from apps.tenancy.context import current_tenant

    def pipeline_fn(input_dict: dict[str, Any]) -> dict[str, Any]:
        # Build a minimal SkillContext from the fixture input. The
        # dispatcher needs a Conversation + BotUser; for fixture runs
        # we lazily build synthetic ones (or skip if the test wants
        # pure assertion checks).
        text = str(input_dict.get("text", ""))
        tenant = current_tenant()
        if tenant is None:
            # CLI runs always wrap in tenant_scope; this is defensive.
            raise RuntimeError("pipeline_fn called outside tenant_scope")
        # Build synthetic conversation/bot_user lazily — only what
        # the registry walk needs.
        from apps.conversations.models import Conversation
        from apps.identity.models import BotUser

        bot_user = BotUser.all_tenants.filter(tenant=tenant).first()
        if bot_user is None:
            bot_user = BotUser.all_tenants.create(
                tenant=tenant, channel="max", channel_user_id="replay-fixture"
            )
        conversation = Conversation.all_tenants.filter(bot_user=bot_user).first()
        if conversation is None:
            conversation = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)

        ctx = SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text=text,
        )
        result = skill_dispatch(ctx)
        if result is None:
            return {
                "intent": "",
                "skill_used": "",
                "safety_decision": "allow",
                "response_text": "",
                "tool_calls": [],
            }
        return {
            "intent": (result.meta or {}).get("intent", ""),
            "skill_used": (result.meta or {}).get("skill", ""),
            "safety_decision": "allow",
            "response_text": result.reply_text,
            "tool_calls": [],
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
    pipeline_fn = _build_default_pipeline_fn()
    reports = []
    with tenant_scope(tenant):
        for fixture in fixtures:
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
