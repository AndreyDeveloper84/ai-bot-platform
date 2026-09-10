"""The end of slice 1's vertical: five states projected onto ASK / RECOMMEND / BLOCK.

Plus the audit record (§19), whose two mandatory fields are the ones easiest to
leave out and hardest to reconstruct later: the origin of every piece of evidence
considered, and every model signal that was turned down.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import audit
from apps.orchestrator.decision_readiness import decision as dec
from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import questions as q
from apps.orchestrator.decision_readiness.evidence import ModelSignal
from apps.orchestrator.decision_readiness.safety_input import SafetyResult
from apps.orchestrator.decision_readiness.tests.conftest import REVISION, candidates, make_input


def _project(**overrides: object) -> dec.StructuredDecision:
    request = make_input(**overrides)
    return dec.project(eng.evaluate(request), state_revision=REVISION)


# --- the three outcomes ------------------------------------------------------


def test_ready_recommends() -> None:
    decision = _project()

    assert decision.decision_type is dec.DecisionType.RECOMMEND
    assert decision.prompt_semantics is None
    assert decision.based_on_state_revision == REVISION


def test_missing_required_context_asks() -> None:
    decision = _project(evidence=())

    assert decision.decision_type is dec.DecisionType.ASK
    assert decision.prompt_semantics is not None
    assert decision.prompt_semantics.kind == "required_context"
    assert decision.prompt_semantics.target_slots == ("city",)


def test_unknown_safety_blocks() -> None:
    decision = _project(safety=SafetyResult.not_evaluated())

    assert decision.decision_type is dec.DecisionType.BLOCK
    assert decision.blockers[0].blocker_type is eng.BlockerType.SAFETY_UNKNOWN
    assert decision.prompt_semantics is None
    assert decision.options == ()


def test_the_same_state_asks_or_recommends_depending_on_delegation() -> None:
    """The visible face of the DELEGATION CEILING — one state, two outcomes."""

    asking = _project(candidates=candidates(separation=0.1))
    recommending = _project(candidates=candidates(separation=0.1), delegation=eng.Delegation.HIGH)

    assert asking.readiness_state is recommending.readiness_state
    assert asking.decision_type is dec.DecisionType.ASK
    assert recommending.decision_type is dec.DecisionType.RECOMMEND


# --- what an ASK carries -----------------------------------------------------


def test_an_ask_carries_semantics_not_wording() -> None:
    """Spec §6: the model renders approved semantics; it does not receive a sentence
    to repeat, and it may not add an option controlled logic did not supply."""

    decision = _project(candidates=candidates(separation=0.1))

    assert decision.prompt_semantics is not None
    assert not hasattr(decision.prompt_semantics, "text")
    assert decision.prompt_semantics.question_id
    assert decision.prompt_semantics.mode in q.CLARIFICATION_MODES


def test_escape_and_delegation_survive_the_option_limit() -> None:
    decision = _project(candidates=candidates(separation=0.1))

    roles = {option.role for option in decision.options}

    assert q.OptionRole.ESCAPE in roles
    assert q.OptionRole.DELEGATE in roles


def test_a_broken_output_invariant_raises_rather_than_producing_an_empty_turn() -> None:
    """§5: a non-blocked state that may not recommend must carry a question.

    Inventing an outcome here would turn a broken invariant into a silently
    empty turn, which is the failure mode hardest to notice in production.
    """

    output = eng.evaluate(make_input(candidates=candidates(separation=0.1)))
    broken = eng.ReadinessOutput(
        readiness_state=output.readiness_state,
        allow_recommend=False,
        reason_codes=output.reason_codes,
        readiness_key=output.readiness_key,
        next_question=None,
    )

    with pytest.raises(ValueError, match="output invariant is broken"):
        dec.project(broken, state_revision=REVISION)


def test_the_summary_carries_codes_not_labels() -> None:
    """Canon §13.5: analytics keys off semantic ids, never localized labels."""

    summary = dec.DecisionSummary.of(_project(evidence=()))

    assert summary.decision_type is dec.DecisionType.ASK
    assert summary.question_id is not None
    assert not hasattr(summary, "label")


# --- §19: the audit record ---------------------------------------------------


def test_the_record_shows_the_origin_of_everything_it_used() -> None:
    request = make_input()
    record = audit.build(request, eng.evaluate(request), evaluation_id="ev-1")

    assert record.evidence_considered
    assert all("origin" in item for item in record.evidence_considered)
    assert record.evidence_considered[0]["origin"] == "user_text"


def test_the_record_shows_what_was_rejected_as_plainly_as_what_was_accepted() -> None:
    """Without this list a DRF-1542-class defect is indistinguishable from normal
    operation: both show a question being asked."""

    request = make_input(
        evidence=(),
        model_signals=(
            ModelSignal(
                signal_id="s1",
                slot="city",
                value="Пенза",
                produced_at_revision=2,
                extractor_id="max-concierge-v1",
            ),
        ),
    )

    record = audit.build(request, eng.evaluate(request), evaluation_id="ev-2")

    assert record.model_signals_rejected == (
        {
            "slot": "city",
            "extractor_id": "max-concierge-v1",
            "reason": "EVID_MODEL_SIGNAL_NOT_EVIDENCE",
        },
    )


def test_the_record_carries_references_and_not_utterances() -> None:
    """§19 PII: identifiers, never the text of what anyone said."""

    request = make_input()
    record = audit.build(request, eng.evaluate(request), evaluation_id="ev-3")

    serialised = repr(record)

    # Presence first: the reference IS in the record, so the absence below is
    # about what was left out rather than about a record that came out empty.
    assert "MessageRef" in serialised
    assert "Пенза" not in serialised


def test_a_record_that_no_longer_replays_says_so() -> None:
    request = make_input()
    record = audit.build(request, eng.evaluate(request), evaluation_id="ev-4")

    marked = audit.mark_not_replayable(record, "candidate digest no longer resolves")

    assert record.replayable is True
    assert marked.replayable is False
    assert marked.not_replayable_reason == "candidate digest no longer resolves"


def test_the_record_is_reproducible_from_the_same_input() -> None:
    request = make_input()

    first = audit.build(request, eng.evaluate(request), evaluation_id="ev-5")
    second = audit.build(request, eng.evaluate(request), evaluation_id="ev-5")

    assert first == second
