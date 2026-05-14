"""SkillFSM helper tests (DRF-835 / Sprint 9 / D3).

Uses a 3-step toy FSM (per ticket acceptance) plus the two built-in
validators. Tests target:

* Lifecycle: enter → transition → Completed.
* Validation errors re-ask the same step.
* Edit flow via goto().
* Serialize / deserialize round-trip (the persistence contract).
* Validators: ranges + choices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest

from apps.skills.fsm import (
    COMPLETE,
    Completed,
    NextStep,
    SkillFSM,
    _Step,
    validate_choice,
    validate_int_range,
)


# ─── 3-step toy FSM ───────────────────────────────────────────────────────


@dataclass
class _ToyFSM(SkillFSM):
    """name (free text) → age (14-90) → goal (lose/maintain/gain)."""

    STEPS: ClassVar[dict[str, _Step]] = {
        "name": _Step(
            prompt="Как тебя зовут?",
            validator=lambda s: (s.strip(), None) if s.strip() else (None, "Введи имя."),
            next="age",
        ),
        "age": _Step(
            prompt="Сколько лет?",
            validator=validate_int_range(14, 90, name="возраст"),
            next="goal",
        ),
        "goal": _Step(
            prompt="Цель?",
            validator=validate_choice(
                {"lose": "Похудеть", "maintain": "Поддержать", "gain": "Набрать"}
            ),
            next=COMPLETE,
        ),
    }
    INITIAL_STEP: ClassVar[str] = "name"

    # Required by dataclass — re-declare for subclass.
    current_step: str = ""
    answers: dict[str, Any] = field(default_factory=dict)
    is_complete: bool = False


# ─── lifecycle ────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_enter_returns_first_prompt(self) -> None:
        fsm = _ToyFSM()
        result = fsm.enter()
        assert isinstance(result, NextStep)
        assert result.prompt == "Как тебя зовут?"
        assert fsm.current_step == "name"
        assert not fsm.is_complete

    def test_full_path_to_completed(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()

        r1 = fsm.transition("Алина")
        assert isinstance(r1, NextStep)
        assert r1.prompt == "Сколько лет?"
        assert not r1.is_validation_error

        r2 = fsm.transition("28")
        assert isinstance(r2, NextStep)
        assert r2.prompt == "Цель?"

        r3 = fsm.transition("maintain")
        assert isinstance(r3, Completed)
        assert r3.answers == {"name": "Алина", "age": 28, "goal": "maintain"}
        assert fsm.is_complete

    def test_transition_before_enter_raises(self) -> None:
        fsm = _ToyFSM()
        with pytest.raises(RuntimeError, match="no current step"):
            fsm.transition("anything")

    def test_transition_after_complete_raises(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()
        fsm.transition("Алина")
        fsm.transition("28")
        fsm.transition("maintain")

        with pytest.raises(RuntimeError, match="already completed"):
            fsm.transition("?")

    def test_enter_unknown_step_raises(self) -> None:
        fsm = _ToyFSM()
        with pytest.raises(ValueError, match="unknown step"):
            fsm.enter("nope")


# ─── validation errors ────────────────────────────────────────────────────


class TestValidationErrors:
    def test_invalid_age_re_asks_same_step(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()
        fsm.transition("Алина")

        result = fsm.transition("abc")
        assert isinstance(result, NextStep)
        assert result.is_validation_error
        # Error AND original prompt appear.
        assert "цифра" in result.prompt.lower()
        assert "Сколько лет?" in result.prompt
        # Still on same step.
        assert fsm.current_step == "age"
        assert "age" not in fsm.answers

    def test_invalid_age_out_of_range(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()
        fsm.transition("Алина")

        result = fsm.transition("150")
        assert isinstance(result, NextStep)
        assert result.is_validation_error
        assert "от 14 до 90" in result.prompt

    def test_invalid_choice_lists_options(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()
        fsm.transition("Алина")
        fsm.transition("28")

        result = fsm.transition("kill_aliens")
        assert isinstance(result, NextStep)
        assert result.is_validation_error
        assert "Похудеть" in result.prompt


# ─── edit / goto ──────────────────────────────────────────────────────────


class TestGoto:
    def test_goto_step_clears_that_answer_keeps_others(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()
        fsm.transition("Алина")
        fsm.transition("28")
        fsm.transition("maintain")  # complete

        # Want to change age — re-enter to clear is_complete + goto.
        result = fsm.goto("age")
        assert isinstance(result, NextStep)
        assert result.prompt == "Сколько лет?"
        assert "age" not in fsm.answers
        assert fsm.answers["name"] == "Алина"  # kept
        # is_complete cleared so transition works again.
        assert not fsm.is_complete

    def test_goto_unknown_step_raises(self) -> None:
        fsm = _ToyFSM()
        with pytest.raises(ValueError, match="unknown step"):
            fsm.goto("nope")


# ─── serialization ────────────────────────────────────────────────────────


class TestSerialization:
    def test_round_trip_mid_flight(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()
        fsm.transition("Алина")

        snapshot = fsm.serialize()
        assert snapshot == {
            "current_step": "age",
            "answers": {"name": "Алина"},
            "is_complete": False,
        }

        restored = _ToyFSM.deserialize(snapshot)
        assert restored.current_step == "age"
        assert restored.answers == {"name": "Алина"}

        # Continue from restored state.
        result = restored.transition("28")
        assert isinstance(result, NextStep)
        assert result.prompt == "Цель?"

    def test_deserialize_none_yields_fresh_fsm(self) -> None:
        """First-turn path: no persisted state yet."""
        fsm = _ToyFSM.deserialize(None)
        assert fsm.current_step == ""
        assert fsm.answers == {}
        assert not fsm.is_complete

    def test_deserialize_empty_dict_yields_fresh_fsm(self) -> None:
        """Legacy/clean conversation rows may store ``{}`` instead of None."""
        fsm = _ToyFSM.deserialize({})
        assert fsm.current_step == ""

    def test_completed_snapshot_round_trip(self) -> None:
        fsm = _ToyFSM()
        fsm.enter()
        fsm.transition("Алина")
        fsm.transition("28")
        fsm.transition("maintain")

        restored = _ToyFSM.deserialize(fsm.serialize())
        assert restored.is_complete
        assert restored.answers == {"name": "Алина", "age": 28, "goal": "maintain"}


# ─── validators ───────────────────────────────────────────────────────────


class TestValidateIntRange:
    def test_in_range(self) -> None:
        v = validate_int_range(10, 20)
        assert v("15") == (15, None)

    def test_below_range(self) -> None:
        v = validate_int_range(10, 20)
        _, err = v("5")
        assert err is not None
        assert "от 10 до 20" in err

    def test_above_range(self) -> None:
        v = validate_int_range(10, 20)
        _, err = v("100")
        assert err is not None

    def test_non_numeric(self) -> None:
        v = validate_int_range(10, 20)
        _, err = v("abc")
        assert err is not None
        assert "цифра" in err.lower()

    def test_empty(self) -> None:
        v = validate_int_range(10, 20)
        _, err = v("")
        assert err is not None

    def test_strips_whitespace(self) -> None:
        v = validate_int_range(10, 20)
        assert v("  15  ") == (15, None)


class TestValidateChoice:
    def test_valid_slug(self) -> None:
        v = validate_choice({"a": "Apple", "b": "Banana"})
        assert v("a") == ("a", None)

    def test_invalid_slug_lists_labels(self) -> None:
        v = validate_choice({"a": "Apple", "b": "Banana"})
        _, err = v("c")
        assert err is not None
        assert "Apple" in err
        assert "Banana" in err
