"""Nutrition anketa FSM — 5 core steps (DRF-820 / Sprint 9 / P3).

Walks the user through: ``gender → age → height → weight → goal``,
then signals COMPLETE. The skill (``skill.py``) takes the completed
answers, POSTs to Ayla ``upsert_profile``, and renders the norms card.

Steps skipped vs the mysite source (Phase 3.1 Part 1, 823 LOC):

* ``pace`` (slow / balanced)
* ``gain_clarify`` — the gain-goal branch
* ``bmi_ladder`` — server-side override response handling
* ``allergies`` / ``meds`` — Phase 1 / Tier-B health screening

The skipped steps were not in the original 5-step DRF-820 acceptance
("8-step" was an aspirational count). Phase 1 extension adds them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from apps.skills.fsm import (
    COMPLETE,
    SkillFSM,
    _Step,
    validate_choice,
    validate_int_range,
)


_GENDER_CHOICES = {"female": "Женский", "male": "Мужской"}
_GOAL_CHOICES = {
    "lose": "Похудеть",
    "maintain": "Поддержать",
    "gain": "Набрать",
}


@dataclass
class AnketaFSM(SkillFSM):
    """5-step nutrition profile FSM."""

    STEPS: ClassVar[dict[str, _Step]] = {
        "gender": _Step(
            prompt=(
                "Какой у тебя пол? Это нужно для расчёта обмена веществ — "
                "у Ж и М разные коэффициенты."
            ),
            validator=validate_choice(_GENDER_CHOICES),
            next="age",
        ),
        "age": _Step(
            prompt="Сколько тебе лет? Напиши число.",
            validator=validate_int_range(14, 90, name="возраст"),
            next="height",
        ),
        "height": _Step(
            prompt="Какой у тебя рост в сантиметрах?",
            validator=validate_int_range(100, 220, name="рост"),
            next="weight",
        ),
        "weight": _Step(
            prompt="Какой текущий вес в килограммах?",
            validator=validate_int_range(30, 200, name="вес"),
            next="goal",
        ),
        "goal": _Step(
            prompt="Какая у тебя цель?",
            validator=validate_choice(_GOAL_CHOICES),
            next=COMPLETE,
        ),
    }
    INITIAL_STEP: ClassVar[str] = "gender"

    # Dataclass subclass field re-declaration.
    current_step: str = ""
    answers: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False


# ─── public helpers ──────────────────────────────────────────────────────


GENDER_CHOICES = _GENDER_CHOICES
GOAL_CHOICES = _GOAL_CHOICES


def choice_keyboard_options(step: str) -> list[tuple[str, str]]:
    """Return ``[(label, slug), ...]`` for a step that uses a choice keyboard.

    Used by the skill to call :func:`apps.orchestrator.ui.keyboards.anketa_choice_keyboard`
    with the right options per step.

    Raises:
        KeyError: step has no choice keyboard (caller should use text input).
    """
    if step == "gender":
        return [(label, slug) for slug, label in _GENDER_CHOICES.items()]
    if step == "goal":
        return [(label, slug) for slug, label in _GOAL_CHOICES.items()]
    raise KeyError(f"step {step!r} has no choice keyboard (text-input step)")


CHOICE_STEPS = frozenset({"gender", "goal"})
"""Steps that present a choice keyboard. Text-input steps are the complement."""
