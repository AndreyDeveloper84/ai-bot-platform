"""Environment-vs-pin guard (DRF-1384).

## The failure this exists to prevent

``ayla-ai-core`` is pinned to a 40-char commit SHA in ``pyproject.toml``
(see the bump procedure in that file's header). A virtualenv built
*before* a re-pin keeps the old package. Nothing in the repo changes,
nothing in the venv changes -- and yet the test run dies with::

    ImportError: cannot import name 'SOURCE_INFERRED' from 'ayla_ai_core'

which reads exactly like a broken import in ``apps/orchestrator/``. On
2026-08-25 two sub-agents in a row went to *fix that import* before
realising the code was fine and the environment was stale. The measured
state that day: 23 worktree venvs, one of them current.

The same class of failure, one layer earlier: running the suite on the
**system** interpreter instead of the worktree venv. Django 5.0.6 there
against the required >=5.2 means ``CheckConstraint(condition=...)`` fails
to import -- again looking like a code defect.

## What this module does

Compares the *installed* environment against the pins declared in
``pyproject.toml`` and returns human-readable problems. It is called from
the repo-root ``conftest.py`` at ``pytest_configure``, i.e. **before**
collection imports a single application module, so a stale environment
refuses the run with an instruction instead of a traceback.

Stdlib only, no Django import, no project import -- it has to be able to
run inside a broken environment. Fails *open*: anything it cannot parse
or determine is not reported as a problem, because a guard that blocks
work on its own bugs is worse than the bug it guards against.

Also runnable standalone (``uv run python tools/env_guard.py``) -- that is
how ``scripts/dev-env.sh`` verifies a freshly built environment.

Bypass: ``AYLA_ENV_GUARD=off`` (also ``0`` / ``false`` / ``no``).
"""

from __future__ import annotations

import json
import os
import re
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# `ayla-ai-core[django] @ git+https://github.com/.../ayla-ai-core.git@<40 hex>`
# Accepts https and ssh forms -- private-repo auth uses either.
_AYLA_PIN_RE = re.compile(
    r"ayla-ai-core\[django\]\s*@\s*git\+(?:https|ssh)://[^@\s]+@([a-fA-F0-9]{40})"
)
# `"django>=5.2,<6.0",` in [project].dependencies
_DJANGO_SPEC_RE = re.compile(r'"django>=([0-9]+(?:\.[0-9]+)*)\s*,\s*<([0-9]+(?:\.[0-9]+)*)"')

_ONE_COMMAND = "uv sync --extra dev --extra ai-core --frozen"


def _version_tuple(raw: str) -> tuple[int, ...]:
    """Numeric prefix of a version string. ``5.2.1rc1`` -> ``(5, 2, 1)``."""
    parts: list[int] = []
    for chunk in raw.split("."):
        digits = re.match(r"\d+", chunk)
        if digits is None:
            break
        parts.append(int(digits.group()))
    return tuple(parts)


def _read_pyproject(repo_root: Path) -> str | None:
    try:
        return (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None


def _check_django(pyproject_text: str) -> str | None:
    """Interpreter carries a Django that satisfies the declared range."""
    spec = _DJANGO_SPEC_RE.search(pyproject_text)
    if spec is None:
        return None  # fail open -- dependency line reworded, not our call
    lower, upper = _version_tuple(spec.group(1)), _version_tuple(spec.group(2))

    try:
        installed_raw = distribution("django").version
    except PackageNotFoundError:
        return (
            "  * Django is not installed in the interpreter running pytest\n"
            f"      interpreter: {sys.executable}"
        )
    installed = _version_tuple(installed_raw)
    if lower <= installed < upper:
        return None
    return (
        f"  * Django {installed_raw} installed, pyproject.toml requires "
        f">={spec.group(1)},<{spec.group(2)}\n"
        f"      interpreter: {sys.executable}\n"
        "      A too-old Django dies at `CheckConstraint(condition=...)` and\n"
        "      reads as a code defect. Usually means the system interpreter is\n"
        "      being used instead of this worktree's .venv -- run tests through\n"
        "      `uv run pytest ...`."
    )


def _check_ayla_pin(pyproject_text: str) -> str | None:
    """Installed ayla-ai-core revision == the revision pinned in pyproject.toml."""
    pins = _AYLA_PIN_RE.findall(pyproject_text)
    if len(pins) != 1:
        # No pin (future PyPI migration) or an ambiguous one. The
        # authoritative duplicate check lives in
        # tests/smoke/test_ayla_import.py::test_package_sha_pinned.
        return None
    pinned = pins[0].lower()

    try:
        dist = distribution("ayla-ai-core")
    except PackageNotFoundError:
        return (
            "  * ayla-ai-core is NOT installed, but pyproject.toml pins it\n"
            f"      pinned: {pinned}\n"
            "      Every `from ayla_ai_core import ...` in apps/ will raise\n"
            "      ModuleNotFoundError. The environment was most likely built\n"
            "      with `uv sync --extra dev` -- without `--extra ai-core`."
        )

    payload = dist.read_text("direct_url.json")
    if payload is None:
        return (
            "  * ayla-ai-core has no PEP 610 direct_url.json -- it was installed\n"
            "      from a non-VCS source (wheel / local path), so its revision\n"
            "      cannot be verified against the pin\n"
            f"      pinned: {pinned}"
        )
    try:
        installed = ((json.loads(payload).get("vcs_info") or {}).get("commit_id") or "").lower()
    except (ValueError, AttributeError):
        return None  # fail open -- unparseable metadata is not proof of drift
    if not installed or installed == pinned:
        return None
    return (
        "  * ayla-ai-core in this environment is NOT the pinned revision\n"
        f"      installed: {installed}\n"
        f"      pinned:    {pinned}\n"
        "      This is the DRF-1384 failure. Left alone it surfaces while\n"
        "      Django populates the app registry, as\n"
        "      `ImportError: cannot import name '<NAME>' from 'ayla_ai_core'`.\n"
        "      The import in apps/ is CORRECT for the pinned revision -- it is\n"
        "      not the thing to repair."
    )


def check_environment(repo_root: Path = REPO_ROOT) -> list[str]:
    """Rendered problems, one string each. Empty list == environment is current."""
    if os.environ.get("AYLA_ENV_GUARD", "").strip().lower() in {"off", "0", "false", "no"}:
        return []
    pyproject_text = _read_pyproject(repo_root)
    if pyproject_text is None:
        return []  # fail open -- not a project checkout we understand
    return [p for p in (_check_django(pyproject_text), _check_ayla_pin(pyproject_text)) if p]


def render_report(problems: list[str]) -> str:
    """Format problems as the message a human/agent reads instead of a traceback."""
    body = "\n".join(problems)
    return (
        "\n"
        "=================== ENVIRONMENT IS BEHIND THE PINS (DRF-1384) ==================\n"
        "This is NOT a code defect. This virtualenv was built before the current\n"
        "pins in pyproject.toml and still carries the old packages.\n"
        "\n"
        f"{body}\n"
        "\n"
        "FIX -- one command, from this worktree's root:\n"
        "\n"
        "    scripts/dev-env.sh                    # git bash / WSL / macOS / Linux\n"
        "    powershell -File scripts\\dev-env.ps1   # PowerShell\n"
        f"    {_ONE_COMMAND}     # or the raw command\n"
        "\n"
        "Then re-run your tests. Do NOT borrow another worktree's .venv -- one\n"
        "`pip install` in a borrowed venv breaks it for whoever owns it.\n"
        "\n"
        "Guard: tools/env_guard.py -- bypass with AYLA_ENV_GUARD=off\n"
        "==============================================================================="
    )


def main() -> int:
    problems = check_environment()
    if problems:
        print(render_report(problems), file=sys.stderr)
        return 1
    print("env_guard: environment matches the pins in pyproject.toml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
