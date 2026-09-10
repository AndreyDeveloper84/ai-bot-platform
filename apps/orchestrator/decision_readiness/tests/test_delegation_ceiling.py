"""DELEGATION CEILING — §10.2, tests D1–D6 exactly as the spec names them.

`delegation=HIGH` may unlock a recommendation when candidates are merely hard to
tell apart. It never substitutes for required context, and it never gets past
`CLARIFY` or `STOP`.

D6 is the one that matters most and looks least like a test: it asserts the
**signature** of `f`. A behavioural check passes on an implementation that reads
`delegation` and then happens to ignore it; a parameter that was never accepted
cannot be read at all.

D5 is the paired positive. Without it, "fix" the ceiling by making delegation do
nothing anywhere and every other test here still passes.
"""

from __future__ import annotations

import inspect

from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import reason_codes as rc
from apps.orchestrator.decision_readiness.safety_input import SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.tests.conftest import (
    REVISION,
    SplittingProbe,
    candidates,
    make_input,
)


def test_d1_high_delegation_does_not_close_required_context() -> None:
    output = eng.evaluate(make_input(evidence=(), delegation=eng.Delegation.HIGH))

    assert output.readiness_state is eng.ReadinessState.NEEDS_REQUIRED_CONTEXT
    assert output.allow_recommend is False
    assert rc.DELEG_CEILING_REQUIRED_CONTEXT in output.reason_codes


def test_d2_high_delegation_does_not_get_past_clarify() -> None:
    output = eng.evaluate(
        make_input(
            safety=SafetyResult(state=SafetyState.CLARIFY, evaluated_at_revision=REVISION),
            candidates=candidates(separation=0.1),
            delegation=eng.Delegation.HIGH,
        )
    )

    assert output.allow_recommend is False
    assert rc.DELEG_CEILING_SAFETY in output.reason_codes


def test_d3_stop_blocks_regardless_of_delegation() -> None:
    output = eng.evaluate(
        make_input(
            safety=SafetyResult(state=SafetyState.STOP, evaluated_at_revision=REVISION),
            delegation=eng.Delegation.HIGH,
        )
    )

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.allow_recommend is False


def test_d4_high_delegation_does_not_substitute_for_grounding() -> None:
    output = eng.evaluate(
        make_input(probe=SplittingProbe(narrows=False), delegation=eng.Delegation.HIGH)
    )

    assert output.readiness_state is eng.ReadinessState.INSUFFICIENT_EVIDENCE
    assert output.allow_recommend is False
    assert rc.DELEG_CEILING_INSUFFICIENT_EVIDENCE in output.reason_codes


def test_d5_high_delegation_does_unlock_discrimination() -> None:
    """The paired positive. Without it, a ceiling that blocks everything passes."""

    output = eng.evaluate(
        make_input(candidates=candidates(separation=0.1), delegation=eng.Delegation.HIGH)
    )

    assert output.readiness_state is eng.ReadinessState.NEEDS_DISCRIMINATION
    assert output.allow_recommend is True
    assert rc.DELEG_APPLIED_HIGH in output.reason_codes


def test_d5b_low_delegation_leaves_discrimination_asking() -> None:
    output = eng.evaluate(
        make_input(candidates=candidates(separation=0.1), delegation=eng.Delegation.LOW)
    )

    assert output.readiness_state is eng.ReadinessState.NEEDS_DISCRIMINATION
    assert output.allow_recommend is False
    assert output.next_question is not None


def test_d6_delegation_is_not_a_parameter_of_the_state_function() -> None:
    """The structural half. §10.2: "`f` не принимает `delegation`" — so it cannot
    turn NEEDS_REQUIRED_CONTEXT into READY however the body is later edited."""

    parameters = set(inspect.signature(eng.f).parameters)

    # Presence first: a signature this test failed to read would be an empty
    # set, and an empty set contains no "delegation" either.
    assert "safety" in parameters
    assert "evidence" in parameters
    assert "delegation" not in parameters
    # And the positive control: `g` is where it does belong.
    assert "delegation" in set(inspect.signature(eng.g).parameters)


def test_the_state_function_does_not_read_delegation_from_anywhere_else() -> None:
    """A signature check alone would miss `request.delegation` read inside `f`.

    `f` takes no `ReadinessInput` either, so there is no object on it through
    which delegation could arrive — this asserts that, rather than trusting it.
    """

    source = inspect.getsource(eng.f)
    parameters = set(inspect.signature(eng.f).parameters)

    assert "ReadinessState.READY" in source  # presence: the source really was read
    assert "delegation" not in source.replace("`delegation`", "")

    assert "safety" in parameters  # presence: the signature really was read
    assert "ReadinessInput" not in parameters


def test_ready_recommends_at_every_delegation_level() -> None:
    for level in eng.Delegation:
        output = eng.evaluate(make_input(delegation=level))

        assert output.readiness_state is eng.ReadinessState.READY
        assert output.allow_recommend is True
