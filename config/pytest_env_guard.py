"""pytest plugin: refuse a test run against a stale virtualenv (DRF-1384).

Wired in via ``addopts = "-p config.pytest_env_guard ..."`` in
``pyproject.toml``. The actual checks live in :mod:`tools.env_guard`;
this module is only the pytest entry point.

## Why a `-p` plugin and not a root `conftest.py`

A root ``conftest.py`` is the obvious place and it is **too late**.
pytest-django resolves ``DJANGO_SETTINGS_MODULE`` and calls
``django.setup()`` inside its own ``pytest_load_initial_conftests``
hookimpl; pytest's own conftest loading is registered ``trylast``, so
every conftest in the tree — root included — is imported *after* Django
has already populated the app registry. And populating the app registry
is exactly where a stale ``ayla-ai-core`` explodes::

    apps/skills/apps.py ready()
      -> apps/channels/max/handler.py
        -> apps/orchestrator/memory_ask.py
          -> apps/orchestrator/memory_block.py
            ImportError: cannot import name 'SOURCE_INFERRED' from 'ayla_ai_core'

Verified empirically on 2026-08-25 against revision ``0af8c26`` (the
last one before ``build_memory_block`` landed): with the check in a root
conftest, the ImportError still won.

Plugins named with ``-p`` are imported during ``Config._preparse``,
before the ``pytest_load_initial_conftests`` hook is called at all. That
is early enough, and ``tryfirst`` puts this hookimpl ahead of
pytest-django's within that call.

## Why it lives under `config/`

``-p`` takes an importable module name, and at preparse time ``sys.path``
does not contain the repo root — only what is installed. The project's
editable install maps exactly two top-level packages, ``apps`` and
``config``. ``tools`` is not importable, so ``-p tools.env_guard`` fails
with ``ModuleNotFoundError`` — the worst possible message for the
population this guard exists to help. ``config`` is mapped by the
editable finder, which resolves submodules dynamically from the
worktree, so this module is importable from *every* venv, including ones
built long before this file existed. That property is the whole point:
the stale venvs are the ones that must get the message.

The checks themselves stay in ``tools/env_guard.py``, next to the other
guards, and are loaded from there by path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_PATH = _REPO_ROOT / "tools" / "env_guard.py"


def _load_guard() -> ModuleType | None:
    """Import ``tools/env_guard.py`` by path (``tools`` is not importable).

    Returns ``None`` if it cannot be loaded — the guard fails open. A
    missing or unimportable guard must never be the thing that stops a
    test run.
    """
    try:
        spec = importlib.util.spec_from_file_location("_ayla_env_guard", _GUARD_PATH)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 — deliberately total; see docstring
        return None


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(
    early_config: pytest.Config,
    parser: pytest.Parser,
    args: list[str],
) -> None:
    # `--help` / `--version` must keep working in a broken environment —
    # they are among the first things somebody tries.
    known = early_config.known_args_namespace
    if getattr(known, "version", 0) or getattr(known, "help", False):
        return

    guard = _load_guard()
    if guard is None:
        return

    problems = guard.check_environment(_REPO_ROOT)
    if problems:
        # UsageError renders as a plain `ERROR: <text>` and aborts the
        # session: no traceback, no half-populated app registry, nothing
        # that invites the reader to go debug application code.
        raise pytest.UsageError(guard.render_report(problems))
