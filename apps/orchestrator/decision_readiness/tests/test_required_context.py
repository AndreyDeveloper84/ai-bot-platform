"""§8.3 satisfaction and the closed §8.2 predicate language.

The headline assertion is `test_model_signals_satisfy_nothing`: this is the
structural half of spec §3.1's required test. The end-to-end half — same input
through `evaluate()`, expecting `NEEDS_REQUIRED_CONTEXT` — arrives with E5.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness import evidence as ev
from apps.orchestrator.decision_readiness import required_context as rc
from apps.orchestrator.decision_readiness.candidates import CandidateSetSignature
from apps.orchestrator.decision_readiness.events import user_text_event
from apps.orchestrator.decision_readiness.safety_input import SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.state import (
    ConversationState,
    SlotState,
    SlotValue,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def _candidates(**kwargs: object) -> CandidateSetSignature:
    defaults: dict[str, object] = {
        "digest": "d1",
        "visible_count": 8,
        "recommendation_eligible_count": 8,
        "ordered_ids": ("m1", "m2", "m3"),
        "separation": 0.0,
        "spanning_fields": frozenset({"city", "price_band"}),
    }
    defaults.update(kwargs)
    return CandidateSetSignature(**defaults)  # type: ignore[arg-type]


def _ctx(
    *,
    state: ConversationState | None = None,
    candidates: CandidateSetSignature | None = None,
    mode: rc.Mode = rc.Mode.DISCOVERY,
    safety: SafetyResult | None = None,
    execution_params: frozenset[str] = frozenset(),
) -> rc.PredicateContext:
    return rc.PredicateContext(
        state=state or ConversationState(conversation_id="conv-1", revision=1),
        candidates=candidates or _candidates(),
        mode=mode,
        safety=safety or SafetyResult(state=SafetyState.NORMAL, evaluated_at_revision=1),
        execution_required_params=execution_params,
    )


def _row(**kwargs: object) -> rc.RequiredSlot:
    defaults: dict[str, object] = {
        "slot": "city",
        "owner": rc.SlotOwner.CATALOG,
        "required_when": rc.Always(),
        "question_id": "q-city",
    }
    defaults.update(kwargs)
    return rc.RequiredSlot(**defaults)  # type: ignore[arg-type]


def _user_text_evidence(slot: str = "city", value: str = "Пенза") -> ev.ConfirmedEvidence:
    return ev.from_user_text(
        evidence_id="c1",
        slot=slot,
        value=value,
        event=user_text_event(
            event_id="e1",
            conversation_id="conv-1",
            revision=2,
            message_id="msg-1",
            raw_text=value,
            observed_at=NOW,
        ),
    )


# --- the required test of §3.1, structural half ------------------------------


def test_model_signals_satisfy_nothing() -> None:
    """A signal covering the slot leaves it UNSATISFIED. There is no route around this.

    `verdict()` has no `model_signals` parameter at all — the check is not a
    filter that could be removed, it is an argument that was never accepted.
    """

    import inspect

    assert "model_signals" not in inspect.signature(rc.verdict).parameters
    assert "signals" not in inspect.signature(rc.verdict).parameters

    # And the behavioural half: no confirmed evidence, slot stays unsatisfied.
    assert rc.verdict(_row(), ctx=_ctx(), evidence=[]) is rc.SlotVerdict.UNSATISFIED


def test_confirmed_evidence_satisfies() -> None:
    assert (
        rc.verdict(_row(), ctx=_ctx(), evidence=[_user_text_evidence()])
        is rc.SlotVerdict.SATISFIED_KNOWN
    )


def test_evidence_of_a_disallowed_origin_does_not_satisfy() -> None:
    """OD-DR-3's shape: a slot that excludes PROMOTED_MEMORY is not closed by it."""

    row = _row(satisfied_by_origins=frozenset({ev.EvidenceOrigin.USER_TEXT}))
    memory = ev.from_promoted_memory(
        evidence_id="c2",
        slot="city",
        value="Пенза",
        memory_entry_id="mem-1",
        promoted_at=NOW,
        at_revision=2,
    )

    assert rc.verdict(row, ctx=_ctx(), evidence=[memory]) is rc.SlotVerdict.UNSATISFIED
    assert (
        rc.verdict(row, ctx=_ctx(), evidence=[_user_text_evidence()])
        is rc.SlotVerdict.SATISFIED_KNOWN
    )


def test_a_slot_cannot_declare_a_model_origin_as_satisfying() -> None:
    with pytest.raises(ValueError, match="not evidence"):
        _row(satisfied_by_origins=frozenset({ev.EvidenceOrigin.MODEL_INFERENCE}))


def test_a_slot_with_no_satisfying_origin_is_refused() -> None:
    with pytest.raises(ValueError, match="never"):
        _row(satisfied_by_origins=frozenset())


# --- FLEXIBLE ----------------------------------------------------------------


def test_flexible_satisfies_when_the_person_said_so() -> None:
    state = ConversationState(
        conversation_id="conv-1",
        revision=3,
        slots={"city": SlotValue(state=SlotState.FLEXIBLE, evidence_id="c9", at_revision=3)},
    )

    assert (
        rc.verdict(_row(), ctx=_ctx(state=state), evidence=[]) is rc.SlotVerdict.SATISFIED_FLEXIBLE
    )


def test_flexible_without_evidence_behind_it_satisfies_nothing() -> None:
    """A flexibility nobody can point to is a flexibility somebody else decided on."""

    state = ConversationState(
        conversation_id="conv-1",
        revision=3,
        slots={"city": SlotValue(state=SlotState.FLEXIBLE)},
    )

    assert rc.verdict(_row(), ctx=_ctx(state=state), evidence=[]) is rc.SlotVerdict.UNSATISFIED


def test_flexible_is_ignored_when_the_slot_forbids_it() -> None:
    state = ConversationState(
        conversation_id="conv-1",
        revision=3,
        slots={"city": SlotValue(state=SlotState.FLEXIBLE, evidence_id="c9")},
    )

    row = _row(flexible_allowed=False)

    assert rc.verdict(row, ctx=_ctx(state=state), evidence=[]) is rc.SlotVerdict.UNSATISFIED


def test_unknown_slot_is_not_flexible() -> None:
    state = ConversationState(conversation_id="conv-1", revision=3)

    assert rc.verdict(_row(), ctx=_ctx(state=state), evidence=[]) is rc.SlotVerdict.UNSATISFIED


# --- required_when -----------------------------------------------------------


def test_not_required_when_the_condition_is_false() -> None:
    row = _row(required_when=rc.ModeIs(rc.Mode.EXECUTION))

    assert (
        rc.verdict(row, ctx=_ctx(mode=rc.Mode.DISCOVERY), evidence=[])
        is rc.SlotVerdict.NOT_REQUIRED
    )


def test_spec_example_city_is_required_only_when_it_discriminates() -> None:
    """Spec §8.2's own worked example: city matters when the set spans more than one."""

    row = _row(
        required_when=rc.And(
            (
                rc.SpansMoreThanOne("city"),
                rc.SlotIs("city", SlotState.UNKNOWN),
            )
        )
    )

    spanning = _ctx(candidates=_candidates(spanning_fields=frozenset({"city"})))
    single_city = _ctx(candidates=_candidates(spanning_fields=frozenset()))

    assert rc.verdict(row, ctx=spanning, evidence=[]) is rc.SlotVerdict.UNSATISFIED
    assert rc.verdict(row, ctx=single_city, evidence=[]) is rc.SlotVerdict.NOT_REQUIRED


def test_execution_requires_term() -> None:
    row = _row(required_when=rc.ExecutionRequires("ayla_service_id"))

    assert (
        rc.verdict(row, ctx=_ctx(execution_params=frozenset({"ayla_service_id"})), evidence=[])
        is rc.SlotVerdict.UNSATISFIED
    )
    assert rc.verdict(row, ctx=_ctx(), evidence=[]) is rc.SlotVerdict.NOT_REQUIRED


def test_cardinality_term_refuses_to_guess_at_an_unknown_count() -> None:
    """Absent eligibility is UNKNOWN (§12.3), and UNKNOWN is not zero."""

    ctx = _ctx(candidates=_candidates(recommendation_eligible_count=None))

    with pytest.raises(ValueError, match="cardinality is unknown"):
        rc.CardinalityGreaterThan(3).holds(ctx)

    with pytest.raises(ValueError, match="absent"):
        rc.NoRecommendableCandidates().holds(ctx)


def test_boolean_combinators() -> None:
    yes = rc.Always()
    no = rc.Not(rc.Always())

    assert rc.And((yes, yes)).holds(_ctx()) is True
    assert rc.And((yes, no)).holds(_ctx()) is False
    assert rc.Or((yes, no)).holds(_ctx()) is True
    assert rc.Or((no, no)).holds(_ctx()) is False


# --- the language is closed and printable ------------------------------------


def test_the_predicate_language_matches_section_8_2_exactly() -> None:
    """A term that is not in §8.2 is a rule the audit cannot render.

    Kept as an explicit set rather than a count so that adding a term forces a
    reader to check it against the spec instead of bumping a number.
    """

    terms = {cls.__name__ for cls in rc.Predicate.__subclasses__()}

    assert terms == {
        "SpansMoreThanOne",
        "CardinalityGreaterThan",
        "SlotIs",
        "ModeIs",
        "SafetyIs",
        "ExecutionRequires",
        "NoRecommendableCandidates",
        "And",
        "Or",
        "Not",
        "Always",
    }


def test_every_predicate_can_describe_itself() -> None:
    """§19: the condition must be visible in the audit, not reconstructed from the outcome."""

    described = rc.And(
        (
            rc.SpansMoreThanOne("city"),
            rc.SlotIs("city", SlotState.UNKNOWN),
            rc.Not(rc.SafetyIs(SafetyState.STOP)),
        )
    ).describe()

    assert described == (
        "(candidate_set.spans_more_than_one(city) ∧ slot(city).state == UNKNOWN "
        "∧ ¬safety.state == STOP)"
    )


# --- the spec table ----------------------------------------------------------


def test_the_default_spec_is_empty_and_that_is_deliberate() -> None:
    """§11.4: with no safety matrix there are no SAFETY-owned slots, and no plausible
    stand-in for them either — an invented row is indistinguishable later from a
    ruling the owner actually gave."""

    assert rc.EMPTY_SPEC.slots == ()
    assert rc.EMPTY_SPEC.need_slots() == frozenset()


def test_unsatisfied_slots_are_canonically_ordered() -> None:
    spec = rc.RequiredContextSpec(
        policy_version=1,
        slots=(
            _row(slot="service", question_id="q-service"),
            _row(slot="city", question_id="q-city"),
            _row(slot="budget", question_id="q-budget", required_when=rc.Not(rc.Always())),
        ),
    )

    verdicts = rc.evaluate_required_context(spec, ctx=_ctx(), evidence=[])

    assert rc.unsatisfied_slots(verdicts) == ("city", "service")
    assert verdicts["budget"] is rc.SlotVerdict.NOT_REQUIRED
