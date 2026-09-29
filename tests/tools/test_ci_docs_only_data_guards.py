"""A docs-only PR still runs the data guards (DRF-2657).

``ci.yml`` classifies a PR as docs-only when every changed path is under
``docs/`` or ends in ``.md``, and skips the engineering jobs. Until DRF-2657
that also skipped the two guards that catch leaked data — ``detect-secrets``
(job ``secret-scan``) and ``pii_guard`` (job ``checks``) — and the aggregate
printed "OK — docs-only PR". #2204 was exactly such a PR: it brought six full
identifiers of real MAX accounts into ``dev`` of a public repository while
every guard reported "skipping". Docs are where personal data arrives.

Two halves, because a node reading the YAML alone would pass on a verdict
that ignores the job:

* structure — ``secret-scan`` is not gated by the change set, runs
  ``pii_guard --all``, and the aggregate depends on it;
* behaviour — the aggregate's real ``Verdict`` script, taken from ``ci.yml``
  and run in bash: a docs-only PR is RED when the data guards did not pass
  and GREEN when they did; a code PR keeps its old rules.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]


def _verdict_script() -> str:
    steps = _jobs()["test"]["steps"]
    (step,) = [s for s in steps if s.get("name") == "Verdict"]
    return str(step["run"])


def _run_verdict(**results: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover — CI runners always have bash
        pytest.skip("bash is not available")
    env = {**os.environ, **results}
    return subprocess.run(
        [bash, "-c", _verdict_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


# ── structure ────────────────────────────────────────────────────────────────


def test_secret_scan_is_not_gated_by_the_change_set() -> None:
    job = _jobs()["secret-scan"]
    # Presence first: this is the real job, still wired to the change set and
    # running detect-secrets — not an empty mapping that has no gate by accident.
    assert job["needs"] == "changes"
    assert any("detect-secrets-hook" in str(s.get("run", "")) for s in job["steps"])
    assert "changes.outputs.code" not in str(job.get("if", ""))


def test_secret_scan_runs_the_pii_guard_over_all_files() -> None:
    runs = [str(s.get("run", "")) for s in _jobs()["secret-scan"]["steps"]]
    assert any("tools/lint/pii_guard.py --all" in r for r in runs)
    assert any("detect-secrets-hook" in r for r in runs)


def test_the_aggregate_depends_on_secret_scan() -> None:
    assert "secret-scan" in _jobs()["test"]["needs"]


# ── behaviour: the real Verdict script ──────────────────────────────────────

_DOCS_ONLY = {
    "R_CHANGES": "success",
    "CODE": "false",
    "R_CHECKS": "skipped",
    "R_APPS_SHARD": "skipped",
}
_CODE = {"R_CHANGES": "success", "CODE": "true"}


def test_docs_only_with_failed_data_guards_is_red() -> None:
    out = _run_verdict(**_DOCS_ONLY, R_SECRET_SCAN="failure")
    assert out.returncode == 1, out.stdout
    assert "data guards" in out.stdout


def test_docs_only_with_passed_data_guards_is_green() -> None:
    out = _run_verdict(**_DOCS_ONLY, R_SECRET_SCAN="success")
    assert out.returncode == 0, out.stdout


@pytest.mark.parametrize("result", ["skipped", "cancelled"])
def test_docs_only_with_data_guards_not_run_is_red(result: str) -> None:
    """Skipped or cancelled is not passed — the shortcut that let #2204 through."""
    out = _run_verdict(**_DOCS_ONLY, R_SECRET_SCAN=result)
    assert out.returncode == 1, out.stdout


def test_code_pr_keeps_its_rules() -> None:
    green = _run_verdict(
        **_CODE,
        # R_SECRET_SCAN — имя переменной окружения CI (результат джоба secret-scan),
        # значение — статус джоба; не секрет и не его имитация.
        R_SECRET_SCAN="success",  # pragma: allowlist secret
        R_CHECKS="success",
        R_APPS_SHARD="success",
    )
    red = _run_verdict(**_CODE, R_SECRET_SCAN="success", R_CHECKS="failure", R_APPS_SHARD="success")
    assert (green.returncode, red.returncode) == (0, 1)
