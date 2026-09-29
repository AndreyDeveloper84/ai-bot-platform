"""EVIDENCE ORIGIN: what the model produced cannot become what the person said.

Spec §3.1 makes this a **required** test, with a required shape: feed an input
where `ModelSignal` covers every required slot and `ConfirmedEvidence` is empty,
and the answer must still be "ask". That end-to-end half arrives with
`evaluate()` (slice E5); this module holds the structural half — the types
themselves refusing the substitution.

The live shape being refused is `apps/orchestrator/nutrition_global.py:262-286`,
where the model's tool-call arguments are placed into `message_text`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness import evidence as ev
from apps.orchestrator.decision_readiness.events import (
    SemanticUserEvent,
    user_action_event,
    user_text_event,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _text_event(revision: int = 2) -> SemanticUserEvent:
    return user_text_event(
        event_id="e1",
        conversation_id="conv-1",
        revision=revision,
        message_id="msg-77",
        raw_text="в Пензе",
        observed_at=NOW,
    )


def _action_event(revision: int = 2) -> SemanticUserEvent:
    return user_action_event(
        event_id="e2",
        conversation_id="conv-1",
        revision=revision,
        decision_id="d-9",
        option_id="opt-penza",
        observed_at=NOW,
    )


def _signal(revision: int = 1) -> ev.ModelSignal:
    return ev.ModelSignal(
        signal_id="s-1",
        slot="city",
        value="Пенза",
        produced_at_revision=revision,
        extractor_id="max-concierge-v1",
    )


# --- the enumeration itself --------------------------------------------------


def test_model_inference_is_not_a_confirmable_origin() -> None:
    assert ev.EvidenceOrigin.MODEL_INFERENCE not in ev.CONFIRMABLE_ORIGINS
    assert ev.CONFIRMABLE_ORIGINS == {
        ev.EvidenceOrigin.USER_TEXT,
        ev.EvidenceOrigin.USER_ACTION,
        ev.EvidenceOrigin.DOMAIN_AUTHORITY,
        ev.EvidenceOrigin.PROMOTED_MEMORY,
    }


def test_model_signal_origin_is_a_constant_of_the_type() -> None:
    """Not a parameter — a signal cannot be constructed claiming another origin."""

    assert _signal().origin is ev.EvidenceOrigin.MODEL_INFERENCE
    with pytest.raises(TypeError):
        ev.ModelSignal(  # type: ignore[call-arg]
            signal_id="s-2",
            slot="city",
            value="Пенза",
            produced_at_revision=1,
            extractor_id="x",
            origin=ev.EvidenceOrigin.USER_TEXT,
        )


# --- there is no intake function for a model inference -----------------------


def test_there_is_no_intake_for_model_inference() -> None:
    intakes = {name for name in dir(ev) if name.startswith("from_")}

    assert intakes == {
        "from_user_text",
        "from_user_action",
        "from_domain_authority",
        "from_promoted_memory",
    }
    assert not any("model" in name or "signal" in name for name in intakes)


def test_confirmed_evidence_refuses_the_model_origin_outright() -> None:
    with pytest.raises(ValueError, match="not a confirmable origin"):
        ev.ConfirmedEvidence(
            evidence_id="x1",
            slot="city",
            value="Пенза",
            origin=ev.EvidenceOrigin.MODEL_INFERENCE,
            source_ref=ev.MessageRef(message_id="msg-77"),
            observed_at=NOW,
            captured_at_revision=2,
            issued_by=ev._INTAKE,
        )


def test_direct_construction_without_an_intake_is_refused() -> None:
    with pytest.raises(ValueError, match="intake function"):
        ev.ConfirmedEvidence(
            evidence_id="x2",
            slot="city",
            value="Пенза",
            origin=ev.EvidenceOrigin.USER_TEXT,
            source_ref=ev.MessageRef(message_id="msg-77"),
            observed_at=NOW,
            captured_at_revision=2,
        )


def test_source_reference_must_match_its_origin() -> None:
    """A message id standing in for a backend fetch would make provenance a label."""

    with pytest.raises(ValueError, match="requires a DomainRef"):
        ev.ConfirmedEvidence(
            evidence_id="x3",
            slot="price",
            value="2000",
            origin=ev.EvidenceOrigin.DOMAIN_AUTHORITY,
            source_ref=ev.MessageRef(message_id="msg-77"),
            observed_at=NOW,
            captured_at_revision=2,
            issued_by=ev._INTAKE,
        )


# --- the four intakes --------------------------------------------------------


def test_user_text_intake_takes_an_event_not_a_string() -> None:
    e = ev.from_user_text(evidence_id="c1", slot="city", value="Пенза", event=_text_event())

    assert e.origin is ev.EvidenceOrigin.USER_TEXT
    assert e.source_ref == ev.MessageRef(message_id="msg-77")
    assert e.captured_at_revision == 2
    assert e.confirmed_by is None


def test_user_action_intake_carries_the_decision_reference() -> None:
    e = ev.from_user_action(evidence_id="c2", slot="city", value="Пенза", event=_action_event())

    assert e.origin is ev.EvidenceOrigin.USER_ACTION
    assert e.source_ref == ev.DecisionRef(decision_id="d-9", option_id="opt-penza")


def test_intakes_refuse_the_wrong_event_kind() -> None:
    with pytest.raises(ValueError, match="requires a TEXT event"):
        ev.from_user_text(evidence_id="c3", slot="city", value="Пенза", event=_action_event())

    with pytest.raises(ValueError, match="requires an ACTION event"):
        ev.from_user_action(evidence_id="c4", slot="city", value="Пенза", event=_text_event())


def test_domain_authority_keeps_the_fetch_time() -> None:
    fetched = datetime(2026, 9, 10, 11, 0, tzinfo=UTC)

    e = ev.from_domain_authority(
        evidence_id="c5",
        slot="price",
        value="2000",
        api="catalog.services",
        entity_id="svc-42",
        fetched_at=fetched,
        at_revision=3,
    )

    assert isinstance(e.source_ref, ev.DomainRef)
    assert e.source_ref.fetched_at == fetched
    assert e.observed_at == fetched


def test_promoted_memory_keeps_the_entry_id() -> None:
    e = ev.from_promoted_memory(
        evidence_id="c6",
        slot="city",
        value="Пенза",
        memory_entry_id="mem-7",
        promoted_at=NOW,
        at_revision=3,
    )

    assert e.source_ref == ev.MemoryRef(memory_entry_id="mem-7")


# --- the one legal transition ------------------------------------------------


def test_confirm_takes_authority_from_the_event_and_value_from_the_signal() -> None:
    confirmed = ev.confirm(_signal(revision=1), _action_event(revision=2), evidence_id="c7")

    assert confirmed.value == "Пенза"  # the model proposed it
    assert confirmed.origin is ev.EvidenceOrigin.USER_ACTION  # the person settled it
    assert confirmed.source_ref == ev.DecisionRef(decision_id="d-9", option_id="opt-penza")
    assert confirmed.confirmed_by == "s-1"  # the guess survives in the audit trail


def test_confirm_by_text_yields_user_text_origin() -> None:
    confirmed = ev.confirm(_signal(revision=1), _text_event(revision=2), evidence_id="c8")

    assert confirmed.origin is ev.EvidenceOrigin.USER_TEXT
    assert confirmed.source_ref == ev.MessageRef(message_id="msg-77")


def test_confirmation_must_follow_the_signal_strictly() -> None:
    """An event that predates the guess cannot be a reply to it.

    Without the strict comparison an earlier turn, replayed, would confirm
    whatever the model invented afterwards — the shape of DRF-1542's fourth and
    fifth screens, where the model re-supplied `symptom_text` on every turn.
    """

    with pytest.raises(ValueError, match="does not follow"):
        ev.confirm(_signal(revision=2), _text_event(revision=2), evidence_id="c9")

    with pytest.raises(ValueError, match="does not follow"):
        ev.confirm(_signal(revision=3), _text_event(revision=2), evidence_id="c10")


def test_there_is_no_conversion_function_from_signal_to_evidence() -> None:
    """`confirm()` needs an event. Nothing in the module turns a signal alone into evidence."""

    import inspect

    signal_takers: dict[str, inspect.Signature] = {}
    for name, obj in vars(ev).items():
        if not inspect.isfunction(obj) or name.startswith("_"):
            continue
        signature = inspect.signature(obj)
        if any(
            p.annotation in {"ModelSignal", ev.ModelSignal} for p in signature.parameters.values()
        ):
            signal_takers[name] = signature

    # Positive guard first: a loop that found nothing would pass on an empty
    # module just as happily (`docs/EXECUTOR-RULES.md` §16).
    assert set(signal_takers) == {"confirm"}
    assert "confirming_event" in signal_takers["confirm"].parameters
