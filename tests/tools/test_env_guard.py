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


# ---------------------------------------------------------------------------
# check_against_lock — the DRF-1437 installed-vs-uv.lock gate
# ---------------------------------------------------------------------------
#
# Same two forces as above, with the stakes swapped. This check does NOT run
# before every pytest invocation, so a false positive costs a confused build
# rather than the whole repo; but a false NEGATIVE is what shipped anthropic
# 1.2.0 to the pilot while every pin check in this file stayed green. The
# versions below are the ones actually measured inside
# ayla-bot-staging-worker-1 on 2026-09-01, so a regression here is a
# regression against a real incident rather than an invented one.


def _write_lock(root: Path, packages: dict[str, str]) -> None:
    """Minimal uv.lock carrying only the fields the guard reads."""
    body = ["version = 1", 'requires-python = ">=3.12"', ""]
    for name, version in packages.items():
        body += ["[[package]]", f'name = "{name}"', f'version = "{version}"', ""]
    (root / "uv.lock").write_text("\n".join(body), encoding="utf-8")


class _NamedDist:
    """Stand-in for a `Distribution` as `_installed_versions` reads it."""

    def __init__(self, name: str, version: str) -> None:
        self.metadata = {"Name": name}
        self.version = version


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch):
    """Set the environment the guard believes it is inspecting."""

    def _set(mapping: dict[str, str]) -> None:
        monkeypatch.setattr(env_guard, "_installed_versions", lambda: dict(mapping))

    return _set


# --- it must bite ----------------------------------------------------------


def test_reports_a_version_the_lock_did_not_select(tmp_path: Path, installed) -> None:
    _write_lock(tmp_path, {"anthropic": "0.101.0"})
    installed({"anthropic": "1.2.0"})

    problems = env_guard.check_against_lock(tmp_path)

    assert len(problems) == 1, problems
    assert "anthropic" in problems[0]
    assert "0.101.0" in problems[0], "the report must name the version the lock chose"
    assert "1.2.0" in problems[0], "the report must name the version actually installed"


def test_names_major_drift_separately(tmp_path: Path, installed) -> None:
    """A patch bump and a major bump are not the same news.

    Seventy-eight packages had drifted on the pilot; seven crossed a major.
    The seven are what a human needs to see first, so they get their own line.
    """
    _write_lock(tmp_path, {"anthropic": "0.101.0", "certifi": "2026.4.22"})
    installed({"anthropic": "1.2.0", "certifi": "2026.7.22"})

    problems = env_guard.check_against_lock(tmp_path)

    joined = "".join(problems)
    assert "whole majors apart" in joined
    majors_line = [ln for ln in joined.splitlines() if "whole majors apart" in ln][0]
    assert "anthropic" in majors_line
    assert "certifi" not in majors_line, "a same-major bump must not be called a major"


def test_reports_a_package_the_lock_does_not_contain(tmp_path: Path, installed) -> None:
    """How httpx2 arrived: dragged in by a drifted major, in no lock anywhere."""
    _write_lock(tmp_path, {"httpx": "0.28.1"})
    installed({"httpx": "0.28.1", "httpx2": "2.12.0"})

    problems = env_guard.check_against_lock(tmp_path)

    assert len(problems) == 1, problems
    assert "httpx2" in problems[0]
    assert "2.12.0" in problems[0]


def test_lock_report_names_the_fix() -> None:
    rendered = env_guard.render_lock_report(["  * something drifted"])

    assert "uv.lock" in rendered
    assert env_guard._ONE_COMMAND in rendered, "a report without the fix is just bad news"
    assert "DRF-1437" in rendered


def test_the_real_repo_lock_is_readable() -> None:
    """The presence assertion behind every silence asserted below.

    ``check_against_lock`` returns ``[]`` both when the environment matches
    and when the lock cannot be read. Without this, a uv.lock renamed or
    reformatted out of recognition would switch the guard off and read
    exactly like success — the vacuous-pass failure that
    tools/lint/negative_assert_guard.py exists for, one layer up.
    """
    locked = env_guard._read_lock(_PROJECT_ROOT)

    assert locked is not None, "uv.lock must parse, or the guard silently checks nothing"
    assert len(locked) > 100, f"uv.lock parsed to only {len(locked)} packages"
    assert "anthropic" in locked


# --- it must not cry wolf --------------------------------------------------


def test_silent_when_the_environment_is_exactly_the_lock(tmp_path: Path, installed) -> None:
    _write_lock(tmp_path, {"anthropic": "0.101.0", "httpx": "0.28.1"})
    installed({"anthropic": "0.101.0", "httpx": "0.28.1"})

    # test_the_real_repo_lock_is_readable proves this function can parse a lock,
    # and the biting tests above prove it can speak. So silence here is the
    # match, not a dead branch.
    # empty-assert-ok: an environment that IS the lock must produce no report.
    assert env_guard.check_against_lock(tmp_path) == []


def test_silent_on_bootstrap_packages_absent_from_the_lock(tmp_path: Path, installed) -> None:
    """pip/setuptools/wheel/uv and the project itself are not drift."""
    _write_lock(tmp_path, {"httpx": "0.28.1"})
    installed(
        {
            "httpx": "0.28.1",
            "pip": "26.2.1",
            "setuptools": "80.0.0",
            "wheel": "0.45.0",
            "uv": "0.11.12",
            "ai-bot-platform": "0.1.0",
        }
    )

    # test_reports_a_package_the_lock_does_not_contain proves an UNlisted extra
    # package IS reported by this same branch, so silence here is the allowlist
    # working rather than the branch being dead.
    # empty-assert-ok: bootstrap tooling absent from the lock is not drift.
    assert env_guard.check_against_lock(tmp_path) == []


def test_silent_when_a_locked_package_is_simply_not_installed(tmp_path: Path, installed) -> None:
    """The deliberate asymmetry.

    Which packages are present depends on which extras were synced. Reporting
    "locked but absent" would fire on every honest environment built without
    ``--extra ai-core``, and a guard that fires on everything gets bypassed.
    """
    _write_lock(tmp_path, {"httpx": "0.28.1", "chromadb": "0.5.0"})
    installed({"httpx": "0.28.1"})

    # The opposite direction is asserted to fire, two tests above, so this
    # branch is reachable and deliberately quiet rather than broken.
    # empty-assert-ok: "locked but not installed" is contractually not drift.
    assert env_guard.check_against_lock(tmp_path) == []


def test_silent_when_names_differ_only_by_normalisation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``Django_Stubs.Ext`` and ``django-stubs-ext`` are one package (PEP 503).

    This one patches ``distributions`` rather than using the ``installed``
    fixture on purpose: normalisation happens inside ``_installed_versions``,
    so a fixture that hands the check an already-normalised table would skip
    the very code under test and pass no matter what.
    """
    _write_lock(tmp_path, {"django-stubs-ext": "6.0.4"})
    monkeypatch.setattr(
        env_guard,
        "distributions",
        lambda: [_NamedDist("Django_Stubs.Ext", "6.0.4")],
    )
    assert env_guard._installed_versions() == {"django-stubs-ext": "6.0.4"}

    # empty-assert-ok: a normalisation difference is not drift. Were the guard
    # comparing raw names, this input would be reported twice over — as a
    # missing package AND an extraneous one. That is the cry-wolf failure this
    # asserts against.
    assert env_guard.check_against_lock(tmp_path) == []


def test_silent_when_there_is_no_lock(tmp_path: Path, installed) -> None:
    installed({"anthropic": "1.2.0"})

    # A directory with no uv.lock is not a project checkout this guard
    # understands, and it must not block work there.
    # empty-assert-ok: fail open — no lock to compare against means no verdict.
    assert env_guard.check_against_lock(tmp_path) == []


def test_silent_when_the_lock_is_unparseable(tmp_path: Path, installed) -> None:
    (tmp_path / "uv.lock").write_text("this is not toml [[[", encoding="utf-8")
    installed({"anthropic": "1.2.0"})

    # empty-assert-ok: fail open on its own inability to read, never a guess.
    assert env_guard.check_against_lock(tmp_path) == []


def test_bypass_env_var_disables_the_lock_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, installed
) -> None:
    _write_lock(tmp_path, {"anthropic": "0.101.0"})
    installed({"anthropic": "1.2.0"})
    assert env_guard.check_against_lock(tmp_path), "precondition: this drift IS reported"

    monkeypatch.setenv("AYLA_ENV_GUARD", "off")

    # empty-assert-ok: the bypass is the claim, and the precondition assertion
    # directly above proves the same input is otherwise reported.
    assert env_guard.check_against_lock(tmp_path) == []
