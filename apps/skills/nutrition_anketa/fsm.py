"""Nutrition anketa FSM — 6 steps, screening before anthropometry.

Walks the user through
``gender → age → screening → height → weight → goal``, then signals
COMPLETE. The skill (``skill.py``) takes the completed answers, POSTs to
Ayla ``upsert_profile``, and renders the targets card.

## Why screening sits third, before height and weight

Owner decision of 2026-09-09 (``docs/decisions/
AYLA_NUTRITION_TARGETS_ARCHITECTURE_DECISION.md`` §7.1): Ayla does not
compute targets automatically for minors, during pregnancy or nursing,
for a declared eating disorder, or for a condition affecting nutrition
or fluid balance. Those people keep the diary; they just don't get a
computed number.

The order follows from that. Asking a fifteen-year-old for their weight
and *then* refusing to compute would collect the most sensitive answer
in the flow for nothing — and weight is allowed only inside a voluntary
calculation (§2.2). So both stop-gates fire **before** the anthropometry
questions: age is checked the moment it is given, screening the moment
it is answered. Nobody in a stop scenario is asked their weight.

## The screening step is one question, not three

Pregnancy, nursing, an eating disorder and an illness affecting
nutrition all lead to the *same* outcome — no automatic calculation —
and none of the answers is stored (see ``skill.py``). Splitting them
into three yes/no turns would read as an interrogation and buy nothing:
a person with two of them still lands in the same branch. One
single-select question, with an explicit "none of these" option, is the
same decision with a third of the intrusion.

## What is deliberately NOT here yet

* ``pace`` / desired rate. §7.1 also stops at "losing faster than about
  0.9 kg per week", but that threshold only means something against a
  methodology that computes a rate, and the pilot methodology
  (Mifflin-St Jeor, flat ±10% goal correction) has no pace term yet.
  The question lands with the calculation service, not here — otherwise
  we would be asking for a number nothing consumes.
* **Consent before the weight question.** The decision requires a
  separate consent for weight (§2.2); whether it also covers the
  special-category screening answers is an open question with the owner
  (docs/PLAN_NUTRITION_TARGETS.md, question 1). Wiring one consent that
  silently covers both is exactly what must not be done, so the consent
  screen lands in its own change once that is answered.
* ``gain_clarify``, ``bmi_ladder``, ``allergies`` / ``meds`` — unchanged
  from the DRF-820 scope note.
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

#: Screening answers. Every value except ``none`` is a §7.1 stop scenario;
#: which one it was is decided and then dropped, never stored (skill.py).
_SCREENING_CHOICES = {
    "none": "Ничего из этого",
    "pregnancy_nursing": "Беременность или кормление",
    "eating_disorder": "Расстройство пищевого поведения",
    "condition": "Заболевание, влияющее на питание",
}

#: The one screening answer that lets the calculation proceed.
SCREENING_CLEAR = "none"

#: Below this the calculation is not offered (§7.1). The diary stays.
ADULT_AGE = 18


@dataclass
class AnketaFSM(SkillFSM):
    """6-step nutrition profile FSM. Screening precedes anthropometry."""

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
            # Wide on purpose. The range is here to catch "не число" and
            # typos, NOT to enforce the adult gate: rejecting a
            # fifteen-year-old with «возраст должно быть от 18 до 90»
            # would be a refusal without a name, and it would take the
            # diary away from someone the owner decision says keeps it.
            # The gate lives in skill.py and explains itself.
            validator=validate_int_range(1, 120, name="возраст"),
            next="screening",
        ),
        "screening": _Step(
            prompt=(
                "И последнее перед расчётом — есть ли сейчас что-то из этого? "
                "Спрашиваю потому, что в таких случаях числа должен называть "
                "специалист, а не я."
            ),
            validator=validate_choice(_SCREENING_CHOICES),
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
SCREENING_CHOICES = _SCREENING_CHOICES


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
    if step == "screening":
        # «Ничего из этого» first: it is the common answer, and putting
        # the conditions above it would make the neutral path the one you
        # scroll past.
        return [(label, slug) for slug, label in _SCREENING_CHOICES.items()]
    raise KeyError(f"step {step!r} has no choice keyboard (text-input step)")


CHOICE_STEPS = frozenset({"gender", "goal", "screening"})
"""Steps that present a choice keyboard. Text-input steps are the complement."""
