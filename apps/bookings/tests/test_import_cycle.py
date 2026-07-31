"""Regression tests for the W0-B1A tasks ↔ escalation import cycle.

W0-B1 confirmed a circular import: ``apps.bookings.tasks`` re-exported
``escalate_stale_reminders`` from ``apps.bookings.escalation`` (so Celery
``autodiscover_tasks()`` registers the R2 beat), while
``apps.bookings.escalation`` imported ``_recheck_booking_state`` and the
``_ACTION_*`` constants back from ``apps.bookings.tasks`` — declared
*after* that re-export import. Either import order raised
``ImportError: cannot import name ... from partially initialized
module``, breaking pytest collection and Celery worker boot.

The fix moved the shared send-time booking-state recheck helper into the
dependency-neutral :mod:`apps.bookings.services.recheck`; both beats now
import from there and never from each other (escalation → tasks edge
removed; tasks → escalation re-export kept for autodiscovery).

These tests pin that contract:

1. ``apps.bookings.tasks`` imports successfully.
2. ``apps.bookings.escalation`` imports successfully.
3. Reversed import order works (proven in *fresh* interpreter
   subprocesses — in-process sys.modules manipulation cannot undo a
   partially-initialized-module failure the way a cold interpreter can).
4. The Celery app imports and autodiscovers all four bookings beat
   tasks without raising.
5. The escalation beat still resolves the *same* recheck helper object
   as the dispatch beat (behavioral alignment preserved by the move).
"""

from __future__ import annotations

import os
import subprocess
import sys


def _fresh_import(order: list[str]) -> subprocess.CompletedProcess[str]:
    """Import ``order`` in a cold interpreter with Django set up.

    A cold interpreter is the only honest way to prove independent
    importability: inside the pytest process both modules are usually
    already in ``sys.modules``, so a re-import would be a no-op lookup
    and could mask a real cycle.
    """
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    imports = "\n".join(f"import {mod}" for mod in order)
    script = f"import django\ndjango.setup()\n{imports}\nprint('IMPORT_OK')\n"
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def test_tasks_module_imports_in_fresh_interpreter() -> None:
    result = _fresh_import(["apps.bookings.tasks"])
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_escalation_module_imports_in_fresh_interpreter() -> None:
    result = _fresh_import(["apps.bookings.escalation"])
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_import_order_tasks_then_escalation() -> None:
    result = _fresh_import(["apps.bookings.tasks", "apps.bookings.escalation"])
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_import_order_escalation_then_tasks() -> None:
    """The order that raised ImportError before W0-B1A."""
    result = _fresh_import(["apps.bookings.escalation", "apps.bookings.tasks"])
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_escalation_does_not_import_tasks_module() -> None:
    """Structural guard: the escalation → tasks edge must stay removed.

    tasks → escalation (re-export for autodiscovery) is intentional;
    the reverse edge is what created the cycle. In a cold interpreter,
    importing escalation alone must NOT pull ``apps.bookings.tasks``
    into ``sys.modules`` — if a future change re-adds
    ``from apps.bookings.tasks import ...`` to escalation.py, this
    fails before the cycle can bite again.
    """
    env = os.environ.copy()
    env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    script = (
        "import sys\n"
        "import django\n"
        "django.setup()\n"
        "import apps.bookings.escalation\n"
        "assert 'apps.bookings.tasks' not in sys.modules, (\n"
        "    'escalation must not import tasks (W0-B1A cycle guard)'\n"
        ")\n"
        "print('IMPORT_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "IMPORT_OK" in result.stdout


def test_celery_autodiscovery_registers_bookings_tasks() -> None:
    """The Celery autodiscovery import path registers all bookings beats.

    ``config.celery`` calls ``app.autodiscover_tasks()``, whose per-app
    step is exactly ``import apps.bookings.tasks`` — the import the
    cycle broke (worker boot). Reproducing that import against the real
    Celery app must register all four bookings beats under their public
    task names, including ``bookings.escalate_stale_reminders`` via the
    intentional tasks → escalation re-export.
    """
    from config.celery import app as celery_app

    import apps.bookings.tasks  # noqa: F401 — the autodiscovery target module

    expected = {
        "bookings.send_due_reminders",
        "bookings.escalate_stale_reminders",
        "bookings.send_post_visit_followups",
        "bookings.detect_completed_bookings",
    }
    assert expected <= set(celery_app.tasks.keys())


def test_beats_share_the_same_recheck_helper() -> None:
    """Escalation and dispatch resolve one canonical helper object.

    Pins requirement «existing escalation behavior remains unchanged»:
    after the move both beats must still branch on the identical
    ``_recheck_booking_state`` implementation and action constants.
    """
    import apps.bookings.escalation as escalation_mod
    import apps.bookings.tasks as tasks_mod
    from apps.bookings.services import recheck

    assert escalation_mod._recheck_booking_state is recheck._recheck_booking_state
    assert tasks_mod._recheck_booking_state is recheck._recheck_booking_state
    assert escalation_mod._ACTION_DROP == recheck._ACTION_DROP == "drop"
    assert escalation_mod._ACTION_DEFER == recheck._ACTION_DEFER == "defer"
