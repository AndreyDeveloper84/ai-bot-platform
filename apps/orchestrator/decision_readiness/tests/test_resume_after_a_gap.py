"""E9 — coming back after the two hours, and the dead end that used to be there.

The first test is the defect, written down before it was fixed and kept
afterwards. Three facts collided:

* the slot was empty, because slot values lived in `ConversationState` and that
  expired at two hours (OD-DR-4);
* the register was intact, because it lives in `Conversation.skill_state`, which
  has no TTL of its own;
* the question was suppressed as already asked, because it had been.

So the slot could never be satisfied and the question could never be asked — for
the whole 24-hour window. The difference between two TTLs belonging to one
conversation, and nobody reconciling them.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import ledger as led
from apps.orchestrator.decision_readiness import questions as q
from apps.orchestrator.decision_readiness import reason_codes as rc
from apps.orchestrator.decision_readiness import required_context as rq
from apps.orchestrator.decision_readiness import resume as res
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.safety_input import SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.state import ConversationState, StateLifecycle
from apps.orchestrator.decision_readiness.tests.conftest import calibrated_policy, make_input
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis

SLOT = "city"
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(res, "_redis_client", lambda: client)
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


def _entry() -> q.QuestionCatalogEntry:
    return q.QuestionCatalogEntry(
        kind=q.QuestionKind.REQUIRED_CONTEXT,
        target_slots=(SLOT,),
        mode=q.MODE_CONFIRM_ONE,
        semantics_version=1,
        answer_domain=("Пенза", "Москва"),
    )


def _spec() -> rq.RequiredContextSpec:
    return rq.RequiredContextSpec(
        policy_version=1,
        slots=(
            rq.RequiredSlot(
                slot=SLOT,
                owner=rq.SlotOwner.CATALOG,
                required_when=rq.Always(),
                question_id="unused",
            ),
        ),
    )


def _ledger_with_an_answered_question() -> led.QuestionLedger:
    entry = _entry()
    ledger = led.QuestionLedger().record_ask(
        led.LedgerEntry(
            qid=entry.question_id,
            kind=entry.kind,
            slots=(SLOT,),
            semantics_version=1,
            policy_version=1,
            asked_at=NOW.isoformat(),
            asked_at_revision=7,
        ),
        policy=calibrated_policy(),
    )
    return ledger.mark_resolved(entry.question_id, evidence_id="c-city", at_revision=8)


def _turn_after_the_gap(
    ledger: led.QuestionLedger,
    conditions: dict[str, led.ReaskConditions],
) -> eng.ReadinessOutput:
    """Revision 9: the state expired, so the slots are empty and the evidence is
    gone (its own lifetime is the adapter's business, P2). The register survived."""

    return eng.evaluate(
        make_input(
            state_revision=9,
            state=ConversationState(conversation_id="conv-9", revision=9),
            safety=SafetyResult(state=SafetyState.NORMAL, evaluated_at_revision=9),
            required_context_spec=_spec(),
            catalog=q.QuestionCatalog(entries=(_entry(),)),
            question_ledger=ledger,
            evidence=(),
            reask_conditions=conditions,
        )
    )


# --- the defect, and the way out --------------------------------------------


def test_without_a_summary_the_conversation_is_still_a_dead_end() -> None:
    """The reproduction, kept. Nothing to resume from means no way out — and that
    is correct: the engine must not invent a reason to ask again."""

    output = _turn_after_the_gap(_ledger_with_an_answered_question(), conditions={})

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.next_question is None
    assert rc.ASK_SUPPRESSED_ALREADY_ASKED in output.reason_codes
    assert rc.slot_code(rc.REQ_CONTEXT_UNSATISFIED, SLOT) in output.reason_codes


def test_with_a_summary_the_question_may_be_asked_once_more() -> None:
    """The same turn, with something to resume from. One controlled re-ask."""

    ledger = _ledger_with_an_answered_question()
    summary = res.summarise(
        ConversationState(conversation_id="conv-9", revision=8), ledger, now=NOW
    )
    conditions = res.resume_conditions(
        summary, after=state_mod.LoadResult(lifecycle=StateLifecycle.EXPIRED)
    )

    output = _turn_after_the_gap(ledger, conditions)

    assert output.readiness_state is eng.ReadinessState.NEEDS_REQUIRED_CONTEXT
    assert output.next_question is not None
    assert output.next_question.ask_reason is q.AskReason.REASK_ANSWER_EXPIRED
    assert rc.ASK_REASK_ANSWER_EXPIRED in output.reason_codes


def test_the_ceiling_still_binds_after_a_resume() -> None:
    """One controlled re-ask, not an open licence. `MAX_ASKS_PER_QUESTION_ID` is
    unchanged, so the resumed question cannot come back a third time."""

    entry = _entry()
    ledger = _ledger_with_an_answered_question().record_ask(
        led.LedgerEntry(
            qid=entry.question_id,
            kind=entry.kind,
            slots=(SLOT,),
            semantics_version=1,
            policy_version=1,
            asked_at=NOW.isoformat(),
            asked_at_revision=9,
        ),
        policy=calibrated_policy(),
    )
    summary = res.summarise(
        ConversationState(conversation_id="conv-9", revision=9), ledger, now=NOW
    )
    conditions = res.resume_conditions(
        summary, after=state_mod.LoadResult(lifecycle=StateLifecycle.EXPIRED)
    )

    output = _turn_after_the_gap(ledger, conditions)

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.blockers[0].blocker_type is eng.BlockerType.ASK_BUDGET_EXHAUSTED


# --- what the summary is not -------------------------------------------------


def test_the_summary_has_nowhere_to_put_a_slot_value() -> None:
    """OD-DR-4: not authoritative domain truth, not mutable transaction truth,
    not UserMemory. Restoring a value from a 24-hour-old note would make it the
    source of truth for a price or a preference, and would smuggle back evidence
    whose own lifetime had ended.

    Structural, not a rule: there is no field."""

    fields = set(res.SettledQuestion.__dataclass_fields__) | set(
        res.ResumeSummary.__dataclass_fields__
    )

    assert "qid" in fields  # presence: the right objects were inspected
    assert "value" not in fields
    assert "values" not in fields
    assert "slot_values" not in fields
    assert "evidence" not in fields


def test_only_settled_questions_get_a_re_ask() -> None:
    """A single `answer_expired=True` for the whole turn would also hand a re-ask
    to questions nobody ever answered. Only answers can expire."""

    entry = _entry()
    asked_never_answered = led.QuestionLedger().record_ask(
        led.LedgerEntry(
            qid=entry.question_id,
            kind=entry.kind,
            slots=(SLOT,),
            semantics_version=1,
            policy_version=1,
            asked_at=NOW.isoformat(),
            asked_at_revision=7,
        ),
        policy=calibrated_policy(),
    )

    summary = res.summarise(
        ConversationState(conversation_id="conv-9", revision=8), asked_never_answered, now=NOW
    )
    conditions = res.resume_conditions(
        summary, after=state_mod.LoadResult(lifecycle=StateLifecycle.EXPIRED)
    )

    # Presence on the same data first: the SAME call, on the SAME register once
    # the question is answered, does produce a condition. So the empty result
    # below is caused by the question not being settled, and by nothing else.
    answered = asked_never_answered.mark_resolved(
        entry.question_id, evidence_id="c-city", at_revision=8
    )
    assert res.resume_conditions(
        res.summarise(ConversationState(conversation_id="conv-9", revision=8), answered, now=NOW),
        after=state_mod.LoadResult(lifecycle=StateLifecycle.EXPIRED),
    )

    assert len(asked_never_answered.entries) == 1
    assert summary.settled == ()
    assert conditions == {}


def test_a_live_state_grants_nothing() -> None:
    """Nothing expired, so nothing may be re-asked on that ground."""

    ledger = _ledger_with_an_answered_question()
    summary = res.summarise(ConversationState(conversation_id="conv-9", revision=8), ledger)
    live = state_mod.LoadResult(
        lifecycle=StateLifecycle.LIVE,
        state=ConversationState(conversation_id="conv-9", revision=8),
    )

    # Presence first: there IS something settled to grant, and it is withheld
    # because nothing expired — not because the summary was empty.
    assert len(summary.settled) == 1
    assert res.resume_conditions(summary, after=live) == {}


def test_no_summary_grants_nothing() -> None:
    absent = state_mod.LoadResult(lifecycle=StateLifecycle.ABSENT)
    ledger = _ledger_with_an_answered_question()
    with_summary = res.summarise(
        ConversationState(conversation_id="conv-9", revision=8), ledger, now=NOW
    )

    # Presence first: the same expired read DOES grant a re-ask when there is a
    # summary, so the empty result below is caused by its absence and nothing else.
    assert res.resume_conditions(
        with_summary, after=state_mod.LoadResult(lifecycle=StateLifecycle.EXPIRED)
    )
    assert res.resume_conditions(None, after=absent) == {}


# --- one code outward, two counters inward -----------------------------------


def test_the_expiry_mechanism_is_named_and_reaches_the_audit() -> None:
    """The requirement, in one assertion. `REASK_ANSWER_EXPIRED` is one code over
    two different things; a month from now "why are we re-asking" has to be
    answerable."""

    from apps.orchestrator.decision_readiness import audit

    ledger = _ledger_with_an_answered_question()
    summary = res.summarise(
        ConversationState(conversation_id="conv-9", revision=8), ledger, now=NOW
    )
    conditions = res.resume_conditions(
        summary, after=state_mod.LoadResult(lifecycle=StateLifecycle.EXPIRED)
    )

    assert conditions[_entry().question_id].expiry_mechanism is led.ExpiryMechanism.SESSION_ENDED

    request = make_input(
        state_revision=9,
        state=ConversationState(conversation_id="conv-9", revision=9),
        safety=SafetyResult(state=SafetyState.NORMAL, evaluated_at_revision=9),
        required_context_spec=_spec(),
        catalog=q.QuestionCatalog(entries=(_entry(),)),
        question_ledger=ledger,
        evidence=(),
        reask_conditions=conditions,
    )
    output = eng.evaluate(request)
    record = audit.build(request, output, evaluation_id="ev-resume")

    assert record.question is not None
    assert record.question["ask_reason"] == "reask_answer_expired"  # outward, one name
    assert record.question["ask_reason_mechanism"] == "session_ended"  # inward, told apart


def test_an_unnamed_expiry_is_refused() -> None:
    """An expiry that cannot be counted apart from the other is not accepted at
    all — the alternative is a log where both mechanisms look identical."""

    with pytest.raises(ValueError, match="unnamed one cannot be counted"):
        led.ReaskConditions(answer_expired=True)

    with pytest.raises(ValueError, match="without answer_expired"):
        led.ReaskConditions(expiry_mechanism=led.ExpiryMechanism.VOLATILE_TTL)


def test_both_mechanisms_exist_and_are_different() -> None:
    """The positive control: one member would make the discriminator useless."""

    assert {m.value for m in led.ExpiryMechanism} == {"volatile_ttl", "session_ended"}


# --- storage -----------------------------------------------------------------


def test_the_summary_round_trips_with_its_24h_ttl(fake_redis: FakeRedis) -> None:
    ledger = _ledger_with_an_answered_question()
    summary = res.summarise(
        ConversationState(conversation_id="conv-9", revision=8), ledger, now=NOW
    )

    res.save(summary)

    assert fake_redis.ttls[res._key("conv-9")] == res.RESUME_TTL_SECONDS
    assert res.RESUME_TTL_SECONDS == 24 * 3600  # OD-DR-4
    assert res.load("conv-9") == summary


def test_past_the_horizon_there_is_nothing_to_resume_from(fake_redis: FakeRedis) -> None:
    res.save(
        res.summarise(
            ConversationState(conversation_id="conv-9", revision=8),
            _ledger_with_an_answered_question(),
            now=NOW,
        )
    )
    fake_redis.expire_key(res._key("conv-9"))

    assert res.load("conv-9") is None


def test_a_corrupt_summary_is_no_summary(fake_redis: FakeRedis) -> None:
    """And `None` is not an empty summary: the caller opens a clean epoch rather
    than granting re-asks it cannot justify."""

    fake_redis.set(res._key("conv-9"), "{not json")

    assert res.load("conv-9") is None


def test_the_summary_horizon_matches_the_revision_horizon() -> None:
    """Past 24 hours the revisions are gone too, so there would be nothing to
    resume *into*. Two numbers that must not drift apart."""

    assert res.RESUME_TTL_SECONDS == state_mod.REVISION_HORIZON_SECONDS
