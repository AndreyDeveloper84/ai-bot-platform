"""Skill FSM helper for multi-step skills.

Sprint 9 / D3 (DRF-835). Provides a thin state-machine wrapper skills can
use to manage multi-turn flows (nutrition_anketa's 8 steps, food_correction's
3-choice fork). The pattern is opt-in — single-turn skills (privacy_consent,
faq, food_clarify) ignore it.

## Lifecycle

1. **Skill starts an FSM**::

       fsm = MyAnketaFSM()
       prompt = fsm.enter("gender")
       # → returns the prompt text for the first step

2. **Each turn the skill resumes**::

       fsm = MyAnketaFSM.deserialize(context.conversation.skill_state.get("my_anketa"))
       result = fsm.transition(context.message_text)
       # → returns NextStep(prompt) OR Completed(answers)

3. **Persistence** — at the end of ``handle()``, the skill writes
   ``fsm.serialize()`` back into ``conversation.skill_state["my_anketa"]``
   via a follow-up DB update. The pipeline doesn't auto-persist — keeps
   the writer side explicit (avoids surprise commits when a skill bails
   early).

## State model

Subclasses declare ``STEPS: dict[str, _Step]``. Each step knows:
* ``prompt`` — text shown when the FSM enters this state
* ``validator`` — turns user text → (cleaned_value, error_message_or_None)
* ``next`` — name of the next step OR sentinel ``COMPLETE``

The FSM tracks ``current_step`` and accumulates validated answers in
``answers: dict[str, Any]``. On ``transition``:

1. Validate the input against the current step's validator.
2. On error → return ``NextStep`` with the validation error appended to
   the same step's prompt (re-ask path).
3. On success → store answer, advance to ``next``, return
   ``NextStep(next_step_prompt)`` OR ``Completed(answers)``.

## Why not a third-party library

Looked at ``transitions`` and ``python-statemachine``. Both ship rich
event/callback systems we don't need — our state is per-conversation,
serialised JSON, four operations (enter / transition / serialize /
deserialize). Carrying a 4k-LoC library for that is overkill.

## Resume / edit semantics

* **Resume** is implicit: ``deserialize`` returns an FSM at the saved
  step. If the saved state is ``None`` (first turn), the skill should
  call ``enter()`` itself.
* **Edit** (e.g. "хочу изменить рост") is the **skill's** call — it
  walks the FSM back to a specific step by calling ``goto(step)`` and
  re-asking. The FSM doesn't try to be clever about user intent; that's
  the skill's domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar

COMPLETE = "__complete__"
"""Sentinel for `_Step.next` indicating the FSM ends after this step."""


# ─── per-step config ──────────────────────────────────────────────────────


# Validator contract: (cleaned_value, error_or_None). When error is non-empty
# the FSM re-asks the same step with the error prepended.
Validator = Callable[[str], tuple[Any, str | None]]


@dataclass(frozen=True)
class _Step:
    """One node in the FSM. Subclasses declare these as class data."""

    prompt: str
    validator: Validator
    next: str


# ─── transition outcomes ──────────────────────────────────────────────────


@dataclass(frozen=True)
class NextStep:
    """FSM advanced (or re-asks the same step on validation error)."""

    prompt: str
    is_validation_error: bool = False


@dataclass(frozen=True)
class Completed:
    """FSM reached the COMPLETE sentinel. ``answers`` is the final dict."""

    answers: dict[str, Any]


TransitionResult = NextStep | Completed


# ─── base class ───────────────────────────────────────────────────────────


@dataclass
class SkillFSM:
    """Base class for multi-step skill state machines.

    Subclasses must define:

      * ``STEPS: dict[str, _Step]`` — the state map (class attribute).
      * ``INITIAL_STEP: str`` — first step name (class attribute).

    Subclasses get serialization + transition free. Subclasses MAY
    override ``goto()`` for edit-flow if they want custom validation.
    """

    # Subclasses fill these via class attributes; instance fields below
    # are the runtime mutable state.
    STEPS: ClassVar[dict[str, _Step]] = {}
    INITIAL_STEP: ClassVar[str] = ""

    current_step: str = ""
    answers: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False

    # ─── entry + transition ──────────────────────────────────────────────

    def enter(self, step: str | None = None) -> NextStep:
        """Initialise the FSM at ``step`` (default: ``INITIAL_STEP``).

        Returns the prompt for that step. Existing answers are kept —
        ``enter`` is safe to call mid-flight for an explicit "restart at
        step X" path (e.g. cross-domain re-entry).
        """
        target = step or self.INITIAL_STEP
        if target not in self.STEPS:
            raise ValueError(f"unknown step: {target!r}")
        self.current_step = target
        self.is_complete = False
        return NextStep(prompt=self.STEPS[target].prompt)

    def transition(self, user_input: str) -> TransitionResult:
        """Consume ``user_input`` for the current step.

        Validation error → :class:`NextStep` with the same step's prompt
        prefixed with the validator's error message. Success → advance
        to ``next``; if ``next == COMPLETE`` returns :class:`Completed`.

        Raises:
            RuntimeError: FSM has no current step (caller forgot
                ``enter``) or has already completed (caller must check
                ``is_complete`` before transitioning again).
        """
        if self.is_complete:
            raise RuntimeError("FSM already completed — call enter() to restart")
        if not self.current_step:
            raise RuntimeError("FSM has no current step — call enter() first")

        step = self.STEPS[self.current_step]
        cleaned, error = step.validator(user_input)
        if error:
            return NextStep(
                prompt=f"{error}\n\n{step.prompt}",
                is_validation_error=True,
            )

        self.answers[self.current_step] = cleaned

        if step.next == COMPLETE:
            self.is_complete = True
            self.current_step = ""
            return Completed(answers=dict(self.answers))

        self.current_step = step.next
        return NextStep(prompt=self.STEPS[step.next].prompt)

    def goto(self, step: str) -> NextStep:
        """Jump back to ``step`` for the edit-flow.

        Clears the answer for ``step`` (will be re-collected) but keeps
        all other answers. Useful for "хочу изменить рост" → step="height".
        """
        if step not in self.STEPS:
            raise ValueError(f"unknown step: {step!r}")
        self.answers.pop(step, None)
        self.current_step = step
        self.is_complete = False
        return NextStep(prompt=self.STEPS[step].prompt)

    # ─── persistence ─────────────────────────────────────────────────────

    def serialize(self) -> dict[str, Any]:
        """JSON-safe snapshot. Store in ``Conversation.skill_state[skill_name]``."""
        return {
            "current_step": self.current_step,
            "answers": dict(self.answers),
            "is_complete": self.is_complete,
        }

    @classmethod
    def deserialize(cls, raw: dict[str, Any] | None) -> "SkillFSM":
        """Rebuild from :meth:`serialize` output.

        ``raw=None`` returns a fresh FSM with no current step — the
        caller should ``enter()`` to start the flow.
        """
        if not raw:
            return cls()
        return cls(
            current_step=str(raw.get("current_step") or ""),
            answers=dict(raw.get("answers") or {}),
            is_complete=bool(raw.get("is_complete") or False),
        )


# ─── built-in validators ──────────────────────────────────────────────────


def validate_int_range(minimum: int, maximum: int, *, name: str = "значение") -> Validator:
    """Make a validator for an integer in the inclusive range ``[min, max]``.

    Used by nutrition_anketa: age 14-90, height 100-220, weight 30-200.
    Returns (int_value, None) on success OR (None, "понятная ошибка") on
    failure. Error text is user-facing — no stack traces.
    """

    def _validate(user_input: str) -> tuple[Any, str | None]:
        text = user_input.strip()
        if not text:
            return None, f"Не понял — напиши {name} цифрой."
        try:
            value = int(text)
        except ValueError:
            return None, f"Нужна цифра — попробуй ещё раз. {name.capitalize()}?"
        if value < minimum or value > maximum:
            return (
                None,
                f"{name.capitalize()} должно быть от {minimum} до {maximum} — проверь?",
            )
        return value, None

    return _validate


def validate_choice(choices: dict[str, str]) -> Validator:
    """Make a validator that accepts one of the allowed slugs.

    ``choices`` maps slug → label (so the error message can show what
    we expected). Used by anketa steps that present a choice keyboard;
    the callback decoder feeds the slug straight here.
    """

    def _validate(user_input: str) -> tuple[Any, str | None]:
        slug = user_input.strip()
        if slug in choices:
            return slug, None
        allowed = ", ".join(f"{label!r}" for label in choices.values())
        return None, f"Нужно выбрать один из вариантов: {allowed}."

    return _validate
