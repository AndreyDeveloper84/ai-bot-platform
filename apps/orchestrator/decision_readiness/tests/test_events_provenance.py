"""A tap cannot present itself as prose, and substituted prose cannot present
itself as a tap.

Two live facts stand behind these assertions:

* `apps/orchestrator/nutrition_global.py:262-286` — the model's tool-call
  arguments are placed into `message_text`, a field that everywhere else means
  "what the human said". That is the EVIDENCE ORIGIN violation, and the
  mechanism behind DRF-1542.
* `apps/channels/max_bot/quick_actions.py:416-472` — a tap is substituted into
  the text pipeline as if the caption had been typed, which erases the
  `(decision_id, option_id)` pair. Recorded as hazard §7 п.3 of the 10.09
  inventory.

`SemanticUserEvent` cannot fix either call site — those are other slices and, in
the first case, another executor's task. What it can do is refuse to be the type
they produce.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness.events import (
    SemanticUserEvent,
    Surface,
    UserEventKind,
    user_action_event,
    user_text_event,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def test_text_event_carries_the_transports_message_id() -> None:
    event = user_text_event(
        event_id="e1",
        conversation_id="conv-1",
        revision=1,
        message_id="msg-77",
        raw_text="болит поясница",
        observed_at=NOW,
        surface=Surface.MAX_CHAT,
    )

    assert event.kind is UserEventKind.TEXT
    assert event.raw_text == "болит поясница"
    assert event.message_id == "msg-77"
    assert event.decision_id is None and event.option_id is None


def test_action_event_carries_the_decision_the_surface_rendered() -> None:
    event = user_action_event(
        event_id="e2",
        conversation_id="conv-1",
        revision=2,
        decision_id="d-9",
        option_id="opt-city-penza",
        observed_at=NOW,
    )

    assert event.kind is UserEventKind.ACTION
    assert (event.decision_id, event.option_id) == ("d-9", "opt-city-penza")
    assert event.raw_text is None


def test_substituted_button_text_cannot_claim_to_be_a_tap() -> None:
    """quick_actions substitution: caption in, decision reference gone."""

    with pytest.raises(ValueError, match="TEXT event must not carry"):
        SemanticUserEvent(
            event_id="e3",
            conversation_id="conv-1",
            revision=3,
            kind=UserEventKind.TEXT,
            observed_at=NOW,
            raw_text="Пенза",
            message_id="msg-78",
            decision_id="d-9",
            option_id="opt-city-penza",
        )


def test_a_tap_cannot_smuggle_prose() -> None:
    """Prose that merely matches a caption is not an answer from a closed set."""

    with pytest.raises(ValueError, match="ACTION event must not carry raw_text"):
        SemanticUserEvent(
            event_id="e4",
            conversation_id="conv-1",
            revision=3,
            kind=UserEventKind.ACTION,
            observed_at=NOW,
            decision_id="d-9",
            option_id="opt-city-penza",
            raw_text="Пенза",
        )


def test_text_without_a_message_id_is_refused() -> None:
    """The model can compose a string. It cannot compose the transport's id for it."""

    with pytest.raises(ValueError, match="message_id"):
        SemanticUserEvent(
            event_id="e5",
            conversation_id="conv-1",
            revision=1,
            kind=UserEventKind.TEXT,
            observed_at=NOW,
            raw_text="болит поясница",
        )


def test_empty_text_is_allowed_but_missing_text_is_not() -> None:
    """Empty and absent are different answers — rule 4, discipline of absence."""

    ok = user_text_event(
        event_id="e6",
        conversation_id="conv-1",
        revision=1,
        message_id="msg-79",
        raw_text="",
        observed_at=NOW,
    )
    assert ok.raw_text == ""

    with pytest.raises(ValueError, match="raw_text"):
        SemanticUserEvent(
            event_id="e7",
            conversation_id="conv-1",
            revision=1,
            kind=UserEventKind.TEXT,
            observed_at=NOW,
            message_id="msg-79",
            raw_text=None,
        )


def test_action_needs_both_halves_of_the_decision_reference() -> None:
    with pytest.raises(ValueError, match="decision_id and option_id"):
        SemanticUserEvent(
            event_id="e8",
            conversation_id="conv-1",
            revision=1,
            kind=UserEventKind.ACTION,
            observed_at=NOW,
            decision_id="d-9",
        )


def test_revision_zero_is_refused() -> None:
    """`confirm()` compares with a strict `>`; zero would blur "before any event"."""

    with pytest.raises(ValueError, match="revision must be >= 1"):
        user_text_event(
            event_id="e9",
            conversation_id="conv-1",
            revision=0,
            message_id="msg-80",
            raw_text="привет",
            observed_at=NOW,
        )


def test_there_is_no_model_event_kind() -> None:
    """A third member would be the readable form of the substitution §3.1 forbids."""

    assert {member.value for member in UserEventKind} == {"text", "action"}


def test_surface_is_not_part_of_the_answer_only_of_the_audit() -> None:
    """SURFACE-INDEPENDENT (§13.2): the same answer from either surface is the
    same answer. `surface` exists so an audit can say where, not so a decision
    can branch on it."""

    from_chat = user_action_event(
        event_id="e10",
        conversation_id="conv-1",
        revision=4,
        decision_id="d-9",
        option_id="opt-city-penza",
        observed_at=NOW,
        surface=Surface.MAX_CHAT,
    )
    from_miniapp = user_action_event(
        event_id="e11",
        conversation_id="conv-1",
        revision=4,
        decision_id="d-9",
        option_id="opt-city-penza",
        observed_at=NOW,
        surface=Surface.MINIAPP,
    )

    assert from_chat.option_id == from_miniapp.option_id
    assert from_chat.surface is not from_miniapp.surface
