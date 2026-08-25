"""Tests for tools/env_guard.py — the DRF-1384 environment-vs-pin gate.

Two things are being protected here, and they pull in opposite
directions.

**It must bite.** A venv carrying an ayla-ai-core revision other than the
one pinned in ``pyproject.toml`` is the exact failure of 2026-08-25: the
run dies with ``ImportError: cannot import name 'SOURCE_INFERRED' from
'ayla_ai_core'``, which reads as a broken import in ``apps/``. Two
sub-agents went and edited that import before working out the code was
fine. If the drift check ever stops firing, that hour comes back.

**It must not cry wolf.** This guard runs before *every* pytest
invocation in the repo, including CI. A false positive blocks all work at
once. So every branch that cannot prove drift — unreadable pyproject,
unparseable metadata, no pin declared — must return silence rather than a
guess, and that is asserted here just as hard as the biting.

The wiring itself is pinned too: the guard is only load-bearing while
``addopts`` actually loads the plugin. A guard nobody runs is worse than
no guard, because it reads like coverage.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_personal_field_guard.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools"))
import env_guard  # type: ignore[import-not-found]  # noqa: E402

# Real revisions, on purpose: `_PINNED` is what pyproject.toml pins today and
# `_OTHER` is the last ayla-ai-core revision before `build_memory_block` landed
# — i.e. the one that actually reproduces the DRF-1384 ImportError. Public git
# SHAs of a code repository, not credentials; the pragmas are for
# detect-secrets' Hex-High-Entropy heuristic, which cannot tell the difference.
_PINNED = "ee6425ac90f6f4e2e0d899c22ebe981ae3b623e1"  # pragma: allowlist secret
_OTHER = "0af8c2634a1dc51e8b619c2bdb7613254772397e"  # pragma: allowlist secret

_PYPROJECT = f"""
[project]
dependencies = [
    "django>=5.2,<6.0",
]
[project.optional-dependencies]
ai-core = [
    "ayla-ai-core[django] @ git+https://github.com/AndreyDeveloper84/ayla-ai-core.git@{_PINNED}",
]
"""


class _FakeDist:
    """Stand-in for `importlib.metadata.Distribution`."""

    def __init__(self, version: str = "0.9.0", direct_url: str | None = None) -> None:
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json":
            return self._direct_url
        return None


def _direct_url(sha: str) -> str:
    return json.dumps(
        {
            "url": "https://github.com/AndreyDeveloper84/ayla-ai-core.git",
            "vcs_info": {"vcs": "git", "commit_id": sha},
        }
    )


@pytest.fixture
def dists(monkeypatch: pytest.MonkeyPatch):
    """Patch the guard's `distribution()` with a name -> object table."""

    table: dict[str, object] = {}

    def _fake(name: str):
        try:
            return table[name]
        except KeyError:
            raise env_guard.PackageNotFoundError(name) from None

    monkeypatch.setattr(env_guard, "distribution", _fake)
    return table


# --------------------------------------------------------------------------
# It bites
# --------------------------------------------------------------------------


def test_reports_revision_drift(dists) -> None:
    dists["django"] = _FakeDist(version="5.2.4")
    dists["ayla-ai-core"] = _FakeDist(direct_url=_direct_url(_OTHER))

    problem = env_guard._check_ayla_pin(_PYPROJECT)

    assert problem is not None
    # Both revisions must appear: "which one do I have" is the first
    # question the reader has, and the answer is what makes the message
    # actionable rather than merely alarming.
    assert _OTHER in problem
    assert _PINNED in problem


def test_reports_missing_package(dists) -> None:
    """`uv sync --extra dev` without `--extra ai-core` — the other half
    of how a venv ends up unable to import ayla_ai_core."""
    problem = env_guard._check_ayla_pin(_PYPROJECT)

    assert problem is not None
    assert "NOT installed" in problem


def test_reports_django_below_the_floor(dists) -> None:
    """The system interpreter carried Django 5.0.6 against a >=5.2 floor;
    that fails at `CheckConstraint(condition=...)`, one more thing that
    looks like a code defect."""
    dists["django"] = _FakeDist(version="5.0.6")

    problem = env_guard._check_django(_PYPROJECT)

    assert problem is not None
    assert "5.0.6" in problem


def test_report_names_the_fix() -> None:
    """The message exists to stop somebody debugging apps/ — it has to
    carry the command that actually resolves it."""
    report = env_guard.render_report(["  * something drifted"])

    assert "uv sync --extra dev --extra ai-core --frozen" in report
    assert "scripts/dev-env.sh" in report
    assert "NOT a code defect" in report


# --------------------------------------------------------------------------
# It stays silent
# --------------------------------------------------------------------------


def test_silent_when_revision_matches(dists) -> None:
    dists["django"] = _FakeDist(version="5.2.4")
    dists["ayla-ai-core"] = _FakeDist(direct_url=_direct_url(_PINNED.upper()))

    # Case-insensitive on the hex: metadata casing is not drift.
    assert env_guard._check_ayla_pin(_PYPROJECT) is None


def test_silent_when_django_inside_the_range(dists) -> None:
    dists["django"] = _FakeDist(version="5.2.11")

    assert env_guard._check_django(_PYPROJECT) is None


def test_silent_when_no_git_pin_declared(dists) -> None:
    """A future move to a PyPI-published ayla-ai-core removes the SHA to
    compare against. Silence, not a guess."""
    assert env_guard._check_ayla_pin('[project]\ndependencies = ["django>=5.2,<6.0"]\n') is None


def test_silent_when_metadata_is_unparseable(dists) -> None:
    dists["ayla-ai-core"] = _FakeDist(direct_url="{not json")

    assert env_guard._check_ayla_pin(_PYPROJECT) is None


def test_silent_when_pyproject_is_unreadable(tmp_path: Path) -> None:
    """Fails open outside a project checkout rather than blocking a run
    it cannot reason about."""
    assert env_guard.check_environment(tmp_path) == []


def test_bypass_env_var_disables_every_check(monkeypatch: pytest.MonkeyPatch, dists) -> None:
    dists["django"] = _FakeDist(version="5.0.6")
    monkeypatch.setenv("AYLA_ENV_GUARD", "off")

    assert env_guard.check_environment(_PROJECT_ROOT) == []


# --------------------------------------------------------------------------
# The wiring
# --------------------------------------------------------------------------


def test_plugin_is_wired_into_addopts() -> None:
    """The guard only runs because `addopts` loads it as a `-p` plugin,
    early enough to beat pytest-django's `django.setup()`. Move it to a
    conftest and the ImportError wins again (verified 2026-08-25) — so
    the wiring is part of the contract, not an implementation detail.
    """
    pyproject = (_PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "-p config.pytest_env_guard" in pyproject


def test_this_very_session_ran_with_the_guard_loaded(pytestconfig: pytest.Config) -> None:
    assert pytestconfig.pluginmanager.has_plugin("config.pytest_env_guard")
