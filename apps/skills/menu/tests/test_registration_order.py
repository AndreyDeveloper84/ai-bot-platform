"""Registry-position tests for the DRF-963 skills.

These positions are the load-bearing part of the design, not a detail:

* ``menu`` LAST before ``echo`` is what makes the widened U-1 coverage
  strictly additive — it only ever sees turns no other skill claimed, so
  it cannot take a turn away from booking/FAQ/wellness and the booking
  channel gates CG-1..CG-8 never reach it.
* ``help`` BEFORE ``faq`` is what stops «что ты умеешь?» from becoming a
  two-LLM-call knowledge-base lookup (and an operator handoff when the
  LLM is down).

A future edit that merges the two skills into one module, or reorders the
imports in ``SkillsConfig.ready()``, silently breaks both guarantees —
hence this test.
"""

from __future__ import annotations

import pytest

from apps.skills.registry import registered


@pytest.fixture
def order() -> list[str]:
    names = [skill.name for skill in registered()]
    # The app registry is populated on Django boot; if it isn't, these
    # assertions would vacuously pass.
    assert "echo" in names, "skill registry not populated"
    return names


def test_menu_is_the_last_skill_before_echo(order):
    assert order[-1] == "echo"
    assert order[-2] == "menu"


def test_help_registers_before_faq(order):
    assert order.index("help") < order.index("faq")


def test_help_registers_after_the_safety_and_fsm_skills(order):
    """Help must never pre-empt a red-flag screening or a live FSM turn."""
    for earlier in ("human_handoff", "health_screening", "nutrition_anketa"):
        assert order.index(earlier) < order.index("help"), earlier


def test_menu_registers_after_booking(order):
    """Booking's own matcher must get first refusal on every turn."""
    assert order.index("booking") < order.index("menu")


def test_skills_are_registered_once(order):
    """A shared module would register both skills at one position."""
    assert order.count("menu") == 1
    assert order.count("help") == 1
