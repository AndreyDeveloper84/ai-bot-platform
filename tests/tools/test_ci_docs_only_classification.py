"""Which pull request ``ci.yml`` calls «docs-only» — and skips the engineering jobs for (DRF-2660).

The classification is one line of bash inside the workflow. Nothing executed
it before this file: the sister node (``test_ci_docs_only_data_guards.py``)
runs the aggregate's ``Verdict`` script, which starts from an answer the
detector has already given.

### What was wrong

A path counted as a document when it was under ``docs/`` or ended in ``.md``.
``docs/`` holds 21 Python files (measurement scripts), JSON and SQL — all of
them «documents» by that rule. #2111 was such a PR: it added
``docs/measurements/DRF-2552_census.py`` with ``ruff`` switched off by the
docs-only route, ``dev`` went red on ``ruff format`` for the next PR that had
nothing to do with it, and the fix (#2116) was docs-only again — repaired
with the linter still off.

So the rule is the other way round, and fails closed: under ``docs/`` a path
is a document only if its type is one a person reads — Markdown, an image, a
PDF. Everything else there is code and gets the engineering jobs.

### How this is tested

The real script of the step ``Detect docs-only vs code`` is taken from the
workflow and run in bash against a throwaway repository with an ``origin`` and
a ``dev`` branch — the same ``git fetch`` and three-dot diff the runner
performs. The answer is read from ``$GITHUB_OUTPUT``, as the jobs read it.

### Two copies, one rule

``replay.yml`` carries its own copy of the detector (it gates the replay
job), under a comment that says «same logic as ci.yml». It was not: it had
kept the shallow fetch and the swallowed diff failure that ``ci.yml`` had
already fixed, so a change set it could not compute read as «docs-only» and
the replay gate was skipped green. Every node here runs against BOTH copies —
that is what keeps «same logic» true the next time one of them is edited.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
#: Every workflow that classifies a pull request as docs-only.
DETECTORS = ("ci.yml", "replay.yml")

DOCS_ONLY = "false"  # the value of the ``code`` output
CODE = "true"

# The Markdown paths under ``docs/`` used below are names of documents that
# exist: ``doc_refs_guard`` reads every such path written in code as a
# reference to a basis and reddens on one that leads nowhere.


def _detector_script(workflow: str) -> str:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    jobs: dict[str, Any] = yaml.safe_load(text)["jobs"]
    (step,) = [s for s in jobs["changes"]["steps"] if s.get("name") == "Detect docs-only vs code"]
    return str(step["run"])


def test_no_third_copy_of_the_detector_exists() -> None:
    """A workflow that grows its own classifier must join ``DETECTORS``."""
    carrying = sorted(
        path.name
        for path in WORKFLOWS.glob("*.yml")
        if "Detect docs-only vs code" in path.read_text(encoding="utf-8")
    )
    assert carrying == sorted(DETECTORS)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


@pytest.fixture(params=DETECTORS)
def pull_request(request: pytest.FixtureRequest, tmp_path: Path):
    """A clone with ``origin/dev`` and a branch on top of it; returns a classifier.

    ``classify(paths)`` commits those paths on the branch and runs the
    workflow's own detector, as the ``changes`` job would for that PR.
    """
    bash = shutil.which("bash")
    if bash is None:  # pragma: no cover — CI runners always have bash
        pytest.skip("bash is not available")

    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    (work / "README.md").write_text("base\n", encoding="utf-8")
    _git(work, "checkout", "-q", "-b", "dev")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "base")
    _git(work, "push", "-q", "origin", "dev")
    _git(work, "checkout", "-q", "-b", "feature")

    workflow: str = request.param

    def classify(paths: list[str], *, event: str = "pull_request", base: str = "dev") -> str:
        if base != "dev":
            # A base branch with no history in common with the PR: the
            # three-dot diff has no merge base and fails.
            _git(work, "checkout", "-q", "--orphan", base)
            _git(work, "rm", "-rfq", ".")
            (work / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
            _git(work, "add", "-A")
            _git(work, "commit", "-q", "-m", "unrelated base")
            _git(work, "push", "-q", "origin", base)
            _git(work, "checkout", "-q", "feature")
        for rel in paths:
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("changed\n", encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "change")
        output = tmp_path / "github_output"
        output.write_text("", encoding="utf-8")
        result = subprocess.run(
            [bash, "-c", _detector_script(workflow)],
            cwd=work,
            env={**os.environ, "EVENT": event, "BASE_REF": base, "GITHUB_OUTPUT": str(output)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        values = [
            line.split("=", 1)[1]
            for line in output.read_text(encoding="utf-8").splitlines()
            if line.startswith("code=")
        ]
        assert len(values) == 1, (values, result.stdout)
        return values[0]

    return classify


class TestWhatIsADocument:
    @pytest.mark.parametrize(
        "paths",
        [
            ["docs/OPEN_DECISIONS.md"],
            ["docs/screens/SHOT.PNG"],
            ["README.md"],
            ["apps/miniapp/README.md"],
            ["docs/design/mockup.png", "docs/design/mockup.jpg"],
            ["docs/specs/contract.pdf"],
            ["docs/diagrams/flow.svg"],
            ["docs/Свод решений владельца.md"],
            ["docs/OPEN_DECISIONS.md", "docs/runbooks/on-call.md", "CLAUDE.md"],
        ],
    )
    def test_prose_and_pictures_are_docs_only(self, pull_request, paths: list[str]) -> None:
        assert pull_request(paths) == DOCS_ONLY

    @pytest.mark.parametrize(
        "paths",
        [
            # #2111 — the case that turned `dev` red.
            ["docs/measurements/DRF-2552_census.py"],
            ["docs/measurements/result.json"],
            ["docs/runbooks/cleanup.sql"],
            ["docs/patches/fix.patch"],
            ["docs/mockups/screen.html"],
            ["docs/scripts/run.sh"],
            ["docs/config/example.yml"],
            ["docs/noextension"],
        ],
    )
    def test_anything_else_under_docs_is_code(self, pull_request, paths: list[str]) -> None:
        assert pull_request(paths) == CODE


class TestMixedPullRequest:
    """«PR с docs И кодом не должен попадать в docs-only» — the lead's own node."""

    @pytest.mark.parametrize(
        "paths",
        [
            ["docs/OPEN_DECISIONS.md", "apps/llm/router.py"],
            ["README.md", "pyproject.toml"],
            ["docs/runbooks/on-call.md", "docs/measurements/census.py"],
            ["docs/runbooks/on-call.md", ".github/workflows/ci.yml"],
            ["docs/a.png", "tests/tools/test_x.py"],
        ],
    )
    def test_one_code_path_among_documents_is_enough(self, pull_request, paths: list[str]) -> None:
        assert pull_request(paths) == CODE

    @pytest.mark.parametrize(
        "paths",
        [["apps/llm/router.py"], ["pyproject.toml"], ["uv.lock"], ["tools/lint/pii_guard.py"]],
    )
    def test_plain_code_is_code(self, pull_request, paths: list[str]) -> None:
        assert pull_request(paths) == CODE


class TestTheNameIsNotThePlace:
    @pytest.mark.parametrize(
        "paths",
        [
            ["mydocs/tool.py"],  # not under docs/
            ["apps/docs/handler.py"],  # a package called docs is not the docs tree
            ["docs.py"],
            ["docs_helper/a.py"],
            ["apps/x/notes.md.py"],  # ends in .py, not in .md
            ["apps/x/picture.png"],  # a picture OUTSIDE docs/ is an asset of the product
            ["apps/docs/picture.png"],  # …and so is one in a package called docs
        ],
    )
    def test_a_lookalike_path_is_code(self, pull_request, paths: list[str]) -> None:
        assert pull_request(paths) == CODE


class TestNotAPullRequest:
    def test_a_push_always_runs_everything(self, pull_request) -> None:
        assert pull_request(["docs/OPEN_DECISIONS.md"], event="push") == CODE


class TestAChangeSetThatCannotBeComputed:
    def test_a_failed_diff_is_not_an_empty_change_set(self, pull_request) -> None:
        """No merge base with the base branch: fail closed, run the jobs.

        The same documents-only change that is DOCS_ONLY against ``dev`` — the
        only thing that differs is that the change set could not be computed.
        """
        assert pull_request(["docs/OPEN_DECISIONS.md"], base="unrelated") == CODE
