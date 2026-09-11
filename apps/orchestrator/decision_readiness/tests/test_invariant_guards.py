"""E12 — the invariants as standing guards, not as probes run once.

Every barrier in this package has been proved by substitution: remove the rule,
watch a test go red, put it back. That proof is real and it happens **once**, on
the machine where it was run. A guard proves the same thing on every run, on
every base, for everyone who comes after.

Three kinds of thing live here, and the second and third exist because a guard
can fail in ways that look exactly like success:

1. **The defect, replayed.** DRF-1542's five turns through `evaluate()`, so the
   three barriers of §14 are demonstrated together rather than separately.
2. **The subject still exists** (`docs/EXECUTOR-RULES.md` §28). A guard that
   names a constant somebody later renamed keeps passing while guarding
   nothing. Each invariant here names the code element it depends on, and one
   test asserts those elements are still there.
3. **The guard is not vacuous** (NEGATIVE_GUARD contamination). A filter over an
   empty set filters nothing; a permitted-set containing everything permits
   everything. Each of those is checked from the side that would be silent.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import evidence as ev
from apps.orchestrator.decision_readiness import ledger as led
from apps.orchestrator.decision_readiness import migration as mig
from apps.orchestrator.decision_readiness import policy as pol
from apps.orchestrator.decision_readiness import questions as q
from apps.orchestrator.decision_readiness import reason_codes as rc
from apps.orchestrator.decision_readiness import required_context as rq
from apps.orchestrator.decision_readiness.events import user_text_event
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.state import ConversationState
from apps.orchestrator.decision_readiness.tests.conftest import (
    SplittingProbe,
    calibrated_policy,
    candidates,
    make_input,
)

NOW = datetime(2026, 9, 10, 7, 52, 28, tzinfo=UTC)

BODY_AREA = "body_area"
ONSET = "onset_context"


# --- the scenario: DRF-1542, replayed through the engine ---------------------


def _spec() -> rq.RequiredContextSpec:
    """Two slots, both required, both catalog-owned so they can ground a need."""

    return rq.RequiredContextSpec(
        policy_version=1,
        slots=(
            rq.RequiredSlot(
                slot=BODY_AREA,
                owner=rq.SlotOwner.CATALOG,
                required_when=rq.Always(),
                question_id="unused",
            ),
            rq.RequiredSlot(
                slot=ONSET,
                owner=rq.SlotOwner.CATALOG,
                required_when=rq.Always(),
                question_id="unused",
            ),
        ),
    )


def _catalog() -> q.QuestionCatalog:
    """The wide question and the narrowed one.

    The wide one wins the first turn by §13.6's third tiebreak — a smaller
    answer domain is a shorter path to an answer — and that is the only reason
    it wins. Nothing here depends on dictionary order.
    """

    return q.QuestionCatalog(
        entries=(
            q.QuestionCatalogEntry(
                kind=q.QuestionKind.REQUIRED_CONTEXT,
                target_slots=(BODY_AREA, ONSET),
                mode=q.MODE_CONFIRM_ONE,
                semantics_version=1,
                answer_domain=("lower_back", "neck"),
            ),
            q.QuestionCatalogEntry(
                kind=q.QuestionKind.REQUIRED_CONTEXT,
                target_slots=(BODY_AREA,),
                mode=q.MODE_CONFIRM_ONE,
                semantics_version=1,
                answer_domain=("lower_back", "neck", "shoulder"),
            ),
        )
    )


def _evidence(slot: str, value: str, revision: int) -> ev.ConfirmedEvidence:
    return ev.from_user_text(
        evidence_id=f"c-{slot}",
        slot=slot,
        value=value,
        event=user_text_event(
            event_id=f"e-{slot}",
            conversation_id="conv-1542",
            revision=revision,
            message_id=f"msg-{revision}",
            raw_text=value,
            observed_at=NOW,
        ),
    )


def _turn(
    *,
    revision: int,
    ledger: led.QuestionLedger,
    evidence: tuple[ev.ConfirmedEvidence, ...] = (),
    signals: tuple[ev.ModelSignal, ...] = (),
    safety_state: SafetyState = SafetyState.CLARIFY,
) -> eng.ReadinessOutput:
    return eng.evaluate(
        make_input(
            state_revision=revision,
            state=ConversationState(conversation_id="conv-1542", revision=revision),
            # §127 (#1558): известное состояние обязано нести обещание.
            # Фикстуры приведены к контракту; семантика инвариантов не менялась.
            safety=SafetyResult(
                state=safety_state, evaluated_at_revision=revision, handoff=Handoff.NONE
            ),
            required_context_spec=_spec(),
            catalog=_catalog(),
            question_ledger=ledger,
            evidence=evidence,
            model_signals=signals,
        )
    )


def _record(ledger: led.QuestionLedger, output: eng.ReadinessOutput, revision: int):
    question = output.next_question
    assert question is not None
    return ledger.record_ask(
        led.LedgerEntry(
            qid=question.question_id,
            kind=question.kind,
            slots=question.target_slots,
            semantics_version=question.semantics_version,
            policy_version=1,
            asked_at=NOW.isoformat(),
            asked_at_revision=revision,
        ),
        policy=calibrated_policy(),
    )


def test_the_five_screens_of_drf_1542_cannot_happen() -> None:
    """The live defect, turn by turn, against the contract that replaces it.

    On the pilot `health_screening` produced the same screen five times, and
    three of the turns in between contained an answer. §14 walks the same five
    turns through this contract; this is that walk, executed.
    """

    ledger = led.QuestionLedger()

    # 07:52:28 — the complaint. Safety asks for clarification.
    first = _turn(revision=1, ledger=ledger)
    assert first.readiness_state is eng.ReadinessState.NEEDS_REQUIRED_CONTEXT
    assert first.allow_recommend is False
    assert rc.SAFETY_CLARIFY_REQUIRED in first.reason_codes
    assert first.next_question is not None
    q1 = first.next_question.question_id
    ledger = _record(ledger, first, revision=1)

    # 07:52:55 — the person names the circumstance. That closes one slot, so the
    # next question targets a different slot set — and a different slot set is a
    # different question_id (§13.2). The same screen twice is not expressible.
    second = _turn(revision=2, ledger=ledger, evidence=(_evidence(ONSET, "поднял коробку", 2),))
    assert second.readiness_state is eng.ReadinessState.NEEDS_REQUIRED_CONTEXT
    assert second.next_question is not None
    q2 = second.next_question.question_id

    assert q1 != q2, "barrier B2: the register knows q1 was asked, so it cannot come back"
    assert second.next_question.target_slots == (BODY_AREA,)
    ledger = _record(ledger, second, revision=2)

    # 07:53:13 — the person names the place. Both slots are closed and the
    # clarification is resolved, so there is nothing left to ask.
    third = _turn(
        revision=3,
        ledger=ledger,
        evidence=(
            _evidence(ONSET, "поднял коробку", 2),
            _evidence(BODY_AREA, "поясница", 3),
        ),
        safety_state=SafetyState.NORMAL,
    )
    assert third.readiness_state is eng.ReadinessState.READY
    assert third.next_question is None

    # 10:50:23 — the model re-supplies `symptom_text` as its own inference. It is
    # a ModelSignal: it closes nothing, re-opens nothing, and buys no re-ask.
    fourth = _turn(
        revision=4,
        ledger=ledger,
        evidence=(
            _evidence(ONSET, "поднял коробку", 2),
            _evidence(BODY_AREA, "поясница", 3),
        ),
        signals=(
            ev.ModelSignal(
                signal_id="s-1",
                slot="symptom_text",
                value="болит поясница",
                produced_at_revision=4,
                extractor_id="nutrition_global",
            ),
        ),
        safety_state=SafetyState.NORMAL,
    )
    assert fourth.readiness_state is eng.ReadinessState.READY
    assert fourth.next_question is None
    assert rc.EVID_MODEL_SIGNAL_NOT_EVIDENCE in fourth.reason_codes
    assert fourth.model_signals_rejected[0]["slot"] == "symptom_text"

    # 10:50:37 — the same again. Barrier B1 does not wear out with repetition.
    fifth = _turn(
        revision=5,
        ledger=ledger,
        evidence=(
            _evidence(ONSET, "поднял коробку", 2),
            _evidence(BODY_AREA, "поясница", 3),
        ),
        signals=(
            ev.ModelSignal(
                signal_id="s-2",
                slot="symptom_text",
                value="болит поясница",
                produced_at_revision=5,
                extractor_id="nutrition_global",
            ),
        ),
        safety_state=SafetyState.NORMAL,
    )
    assert fifth.next_question is None

    # And the register holds exactly two questions, not five.
    assert len({entry.qid for entry in ledger.entries}) == 2


def test_barrier_b2_alone_stops_the_repeat_even_with_nothing_else_changed() -> None:
    """The middle turn of the defect, isolated: same input, question already asked."""

    ledger = led.QuestionLedger()
    first = _turn(revision=1, ledger=ledger)
    assert first.next_question is not None
    ledger = _record(ledger, first, revision=1)

    repeat = _turn(revision=2, ledger=ledger)

    assert repeat.next_question is None or repeat.next_question.question_id != (
        first.next_question.question_id
    )
    assert rc.ASK_SUPPRESSED_ALREADY_ASKED in repeat.reason_codes


def test_the_ask_budget_makes_five_arithmetically_impossible() -> None:
    """§13.5's second ceiling, independent of every other barrier."""

    exhausted = led.QuestionLedger(consecutive_asks_without_new_evidence=2)

    output = _turn(revision=6, ledger=exhausted)

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.blockers[0].blocker_type is eng.BlockerType.ASK_BUDGET_EXHAUSTED
    assert rc.BLOCK_ASK_BUDGET_EXHAUSTED in output.reason_codes


# --- rule 28: the subject of every guard still exists ------------------------

#: What each invariant leans on. If one of these is renamed or deleted, the
#: guard that names it keeps passing while guarding nothing — so this table is
#: checked directly.
GUARDED_SUBJECTS: tuple[tuple[str, object, str], ...] = (
    ("EVIDENCE ORIGIN", ev, "CONFIRMABLE_ORIGINS"),
    ("EVIDENCE ORIGIN", ev, "_INTAKE"),
    ("EVIDENCE ORIGIN", ev, "confirm"),
    ("QUESTION LOOP", led, "ask_permission"),
    ("QUESTION LOOP", led, "ReaskConditions"),
    ("QUESTION LOOP", pol, "MAX_ASKS_PER_QUESTION_ID"),
    ("QUESTION LOOP", pol, "MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE"),
    ("ASK IMPACT", q, "ask_allowed"),
    ("ASK IMPACT", q, "ASK_SUPPRESSED_NO_IMPACT"),
    ("SURFACE-INDEPENDENT", q, "question_id"),
    ("DELEGATION CEILING", eng, "f"),
    ("DELEGATION CEILING", eng, "g"),
    ("FAIL CLOSED", eng, "_unavailable_reason"),
    ("NO MODEL CONFIDENCE", pol, "UNCALIBRATED"),
    ("UNKNOWN != FLEXIBLE", rq, "verdict"),
    ("SEVERITY SCOPE", mig, "SCREENING_MAY_SUPPRESS"),
    ("PART IS NOT THE MEASURE", mig, "ConflictInputs"),
)


@pytest.mark.parametrize(
    ("invariant", "module", "attribute"),
    GUARDED_SUBJECTS,
    ids=[f"{name}:{attr}" for name, _mod, attr in GUARDED_SUBJECTS],
)
def test_the_thing_each_guard_protects_still_exists(
    invariant: str, module: object, attribute: str
) -> None:
    assert hasattr(module, attribute), (
        f"invariant {invariant} leans on {module.__name__}.{attribute}, which is gone. "  # type: ignore[attr-defined]
        "The guard naming it will keep passing while protecting nothing."
    )


def test_the_subject_table_is_not_empty() -> None:
    """The positive control for the table above: an empty table parametrises to
    nothing and reports success."""

    assert len(GUARDED_SUBJECTS) >= 15
    assert len({name for name, _m, _a in GUARDED_SUBJECTS}) >= 8


# --- NEGATIVE_GUARD contamination: no guard may be vacuous -------------------


def test_the_origin_filter_actually_excludes_something() -> None:
    """A filter over every origin filters nothing. This is the check that would
    stay silent if `CONFIRMABLE_ORIGINS` were widened to the full enumeration."""

    assert ev.CONFIRMABLE_ORIGINS  # presence
    assert ev.CONFIRMABLE_ORIGINS != set(ev.EvidenceOrigin)
    assert set(ev.EvidenceOrigin) - ev.CONFIRMABLE_ORIGINS == {ev.EvidenceOrigin.MODEL_INFERENCE}


def test_the_screening_scope_actually_excludes_something() -> None:
    assert mig.SCREENING_MAY_SUPPRESS  # presence
    assert mig.SCREENING_MAY_SUPPRESS != frozenset(mig.Severity)
    assert set(mig.Severity) - mig.SCREENING_MAY_SUPPRESS == {mig.Severity.CRITICAL}


def test_the_ceilings_are_small_enough_to_bind() -> None:
    """A ceiling of a thousand is not a ceiling. Two is derived from the
    admission rule, and the point of asserting it is that a later edit to
    "something more generous" is a change to the rule, not to a setting."""

    assert pol.MAX_ASKS_PER_QUESTION_ID == 2
    assert pol.MAX_CONSECUTIVE_ASKS_WITHOUT_NEW_EVIDENCE == 2


def test_the_reason_registry_is_populated_and_closed() -> None:
    """An empty registry makes every "is this code known" check pass."""

    assert len(rc.ALL_CODES) >= 30
    assert rc.is_known(rc.STATE_READY)
    assert not rc.is_known("A_CODE_NOBODY_DECLARED")


def test_the_uncalibrated_sentinel_is_not_quietly_a_number() -> None:
    """The whole point of the sentinel: a comparison against it must not succeed.

    A placeholder that compared as `0.0` would let every separation pass the
    threshold, and nothing would look wrong.
    """

    with pytest.raises(TypeError):
        _ = 0.5 < pol.UNCALIBRATED  # type: ignore[operator]

    with pytest.raises(TypeError):
        bool(pol.UNCALIBRATED)


def test_the_required_context_spec_can_actually_refuse() -> None:
    """A satisfaction function that returns SATISFIED for everything is a guard
    with the subject removed. Both answers must be reachable."""

    ctx = rq.PredicateContext(
        state=ConversationState(conversation_id="c", revision=1),
        candidates=candidates(),
        mode=rq.Mode.DISCOVERY,
        safety=SafetyResult(
            state=SafetyState.NORMAL, evaluated_at_revision=1, handoff=Handoff.NONE
        ),
    )
    row = rq.RequiredSlot(
        slot=BODY_AREA,
        owner=rq.SlotOwner.CATALOG,
        required_when=rq.Always(),
        question_id="unused",
    )

    assert rq.verdict(row, ctx=ctx, evidence=[]) is rq.SlotVerdict.UNSATISFIED
    assert (
        rq.verdict(row, ctx=ctx, evidence=[_evidence(BODY_AREA, "поясница", 2)])
        is rq.SlotVerdict.SATISFIED_KNOWN
    )


def test_the_engine_can_reach_every_one_of_the_five_states() -> None:
    """A state machine that only ever returns one state passes every test that
    checks "did we get the state we expected" for that one state."""

    reached = set()

    reached.add(eng.evaluate(make_input()).readiness_state)
    reached.add(eng.evaluate(make_input(evidence=())).readiness_state)
    reached.add(eng.evaluate(make_input(candidates=candidates(separation=0.1))).readiness_state)
    reached.add(eng.evaluate(make_input(probe=SplittingProbe(narrows=False))).readiness_state)
    reached.add(eng.evaluate(make_input(safety=SafetyResult.not_evaluated())).readiness_state)

    assert reached == set(eng.ReadinessState)


def test_the_no_llm_guard_would_catch_a_real_import() -> None:
    """The guard's own positive control, asserted from this side too: the marker
    list must match something that looks like a provider import."""

    from apps.orchestrator.decision_readiness.tests import test_no_model_in_the_contour as guard

    caught = [
        name
        for name in ("openai", "anthropic.messages", "apps.orchestrator.llm.openai_provider")
        for marker in guard.FORBIDDEN_IMPORT_MARKERS
        if name == marker or name.startswith(f"{marker}.")
    ]

    assert len(caught) == 3


def test_the_delegation_ceiling_guard_is_checking_a_real_signature() -> None:
    """`inspect.signature` on something that is not a function would raise rather
    than return an empty set — but a wrapper could return one, and an empty set
    contains no "delegation" either."""

    params = set(inspect.signature(eng.f).parameters)

    assert len(params) >= 8  # presence: a real signature was read
    assert "delegation" not in params
