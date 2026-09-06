"""Architecture guard: cadence belongs to DRF-1468, nowhere else (DRF-1464, T5).

The owner rule for every proactive surface: frequency is the shared
anti-nag mechanism's question (``apps.nutrition_proactive.antinag`` /
``prefs``), answered from the one outbound journal. A surface that grows
its own counter — a ``last_sent`` pref, a private cap, a cooldown — is a
second answer that can drift from the first, which is exactly the failure
DRF-1468 was built to delete.

This is a grep-guard, not a mock: the property being pinned is the
ABSENCE of whole classes of code, and a behaviour test would only sample
it. Two zones:

* ``apps/nutrition_coach/`` must not even READ the cadence machinery —
  triggers and copy answer «what pattern» and «what words», never
  «how often»;
* ``apps/nutrition_proactive/coach.py`` (the planner) may ask the shared
  mechanism — but only by DELEGATING to it, never by re-implementing a
  counter beside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COACH_PKG = REPO_ROOT / "apps" / "nutrition_coach"
PLANNER = REPO_ROOT / "apps" / "nutrition_proactive" / "coach.py"

#: Cadence primitives nobody on this surface may touch directly.
_JOURNAL_TOKENS = (
    "append_outbox",
    "OUTBOX_KEY",
    "weekly_sent_count",
    "MAX_WEEKLY_OUTBOUND_TOTAL",
    "WEEKLY_SURFACE_CAPS",
    "last_sent",
    "cooldown",
)

#: The shared mechanism itself: nutrition_coach must not even read it.
_MECHANISM_TOKENS = (
    "weekly_cap_reason",
    "surface_ignored_streak",
    "SURFACE_IGNORE_LIMIT",
)


def coach_sources() -> list[Path]:
    """Every non-test module of the coach package."""
    return sorted(p for p in COACH_PKG.glob("*.py") if p.name != "__init__.py")


class TestNutritionCoachHasNoCadenceAtAll:
    def test_the_package_exists_and_has_modules(self) -> None:
        assert coach_sources(), "guard would pass vacuously on an empty package"

    @pytest.mark.parametrize("token", [*_JOURNAL_TOKENS, *_MECHANISM_TOKENS])
    def test_no_cadence_token_in_any_coach_module(self, token: str) -> None:
        offenders = [
            path.name for path in coach_sources() if token in path.read_text(encoding="utf-8")
        ]
        # empty-assert-ok: свойство — отсутствие токена во всех модулях;
        # присутствие модулей прибито тестом выше на тех же данных.
        assert offenders == [], f"{token!r} found in {offenders}"


class TestPlannerDelegatesNeverReimplements:
    @pytest.mark.parametrize("token", _JOURNAL_TOKENS)
    def test_no_private_counter_in_the_planner(self, token: str) -> None:
        src = PLANNER.read_text(encoding="utf-8")
        # empty-assert-ok: отсутствие счётчика и есть свойство; делегация прибита ниже.
        assert token not in src

    def test_the_shared_mechanism_is_called_not_bypassed(self) -> None:
        """The other half of the proof: a planner that never touches the
        journal could also be one that never asks the cadence question at
        all. Delegation is pinned present, so «clean» above cannot mean
        «unchecked»."""
        src = PLANNER.read_text(encoding="utf-8")
        assert "weekly_cap_reason" in src
        assert "surface_ignored_streak" in src
