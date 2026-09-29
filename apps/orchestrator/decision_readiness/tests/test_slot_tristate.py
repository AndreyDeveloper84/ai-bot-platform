"""`UNKNOWN` and `FLEXIBLE` never collapse into one another — canon §16.2, spec §3.2.

`null` for both is the defect: the one that disappears is `FLEXIBLE`, and a
`FLEXIBLE` slot read as "still missing" produces a question the person has
already answered out loud ("любой", "неважно"). Spec §13.3 п.1 forbids asking
about a `FLEXIBLE` slot at all.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness.state import (
    UNKNOWN_SLOT,
    ConversationState,
    SlotState,
    SlotValue,
)


def _state(**slots: SlotValue) -> ConversationState:
    return ConversationState(conversation_id="conv-1", revision=1, slots=dict(slots))


def test_absent_slot_reads_as_unknown_not_none() -> None:
    state = _state()

    assert state.slot("city") is UNKNOWN_SLOT
    assert state.slot("city").state is SlotState.UNKNOWN


def test_flexible_is_not_unknown() -> None:
    state = _state(city=SlotValue(state=SlotState.FLEXIBLE, at_revision=2))

    assert state.slot("city").state is SlotState.FLEXIBLE
    assert state.slot("city").state is not SlotState.UNKNOWN
    # Both carry no value. The value is not what distinguishes them — the state is.
    assert state.slot("city").value is None
    assert state.slot("budget").value is None
    assert state.slot("city").state is not state.slot("budget").state


def test_known_requires_a_value() -> None:
    with pytest.raises(ValueError, match="KNOWN"):
        SlotValue(state=SlotState.KNOWN, value=None)


def test_non_known_states_must_not_carry_a_value() -> None:
    """A `FLEXIBLE` slot holding a value would read as `KNOWN` to anything that
    checks the value instead of the state — the collapse arriving by the back door."""

    with pytest.raises(ValueError, match="flexible"):
        SlotValue(state=SlotState.FLEXIBLE, value="Пенза")

    with pytest.raises(ValueError, match="unknown"):
        SlotValue(state=SlotState.UNKNOWN, value="Пенза")


def test_there_are_exactly_three_slot_states() -> None:
    """Canon §16.2 froze three. A fourth (`ERASED`, say) reopens a closed decision.

    "Deliberately erased counts as filled" is preserved as an ask-suppression
    rule in the ask policy (slice E11), not as a slot value — see `state.py`.
    """

    assert {member.value for member in SlotState} == {"known", "unknown", "flexible"}


def test_with_slot_does_not_mutate_the_original() -> None:
    original = _state(city=SlotValue(state=SlotState.UNKNOWN))

    updated = original.with_slot("city", SlotValue(state=SlotState.KNOWN, value="Пенза"))

    assert original.slot("city").state is SlotState.UNKNOWN
    assert updated.slot("city").state is SlotState.KNOWN
