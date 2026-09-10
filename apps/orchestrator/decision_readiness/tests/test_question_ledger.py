"""QUESTION LOOP (§13.4–13.5): the register that DRF-1542 did not have.

Live defect, 04.09 and again in DRF-1542: `health_screening` produced the same
screen five times in a row, and three of the turns between them contained an
answer. Nothing recorded that the question had been asked, so nothing could
notice it had been answered.

The re-ask reasons are a **closed list of five**, and the model computes none of
them — which is the barrier B2 of §14.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import ledger as led
from apps.orchestrator.decision_readiness.policy import ControlledPolicy
from apps.orchestrator.decision_readiness.questions import REASK_REASONS, AskReason, QuestionKind

POLICY = ControlledPolicy(policy_version=1)


def _entry(**kwargs: object) -> led.LedgerEntry:
    defaults: dict[str, object] = {
        "qid": "q1",
        "kind": QuestionKind.REQUIRED_CONTEXT,
        "slots": ("body_area",),
        "semantics_version": 1,
        "policy_version": 1,
        "asked_at": "2026-09-10T12:00:00+00:00",
        "asked_at_revision": 2,
    }
    defaults.update(kwargs)
    return led.LedgerEntry(**defaults)  # type: ignore[arg-type]


# --- idempotency of delivery -------------------------------------------------


def test_a_redelivered_event_does_not_create_a_second_record() -> None:
    """A retried webhook must not spend the ask budget (§17.3)."""

    ledger = led.QuestionLedger().record_ask(_entry(), policy=POLICY)
    again = ledger.record_ask(_entry(), policy=POLICY)

    assert len(again.entries) == 1
    assert again.total_asks("q1") == 1
    assert again is ledger


def test_the_same_question_at_a_later_revision_is_a_second_ask() -> None:
    ledger = led.QuestionLedger().record_ask(_entry(asked_at_revision=2), policy=POLICY)
    ledger = ledger.record_ask(_entry(asked_at_revision=5), policy=POLICY)

    assert len(ledger.entries) == 2
    assert ledger.total_asks("q1") == 2


def test_the_register_is_bounded() -> None:
    ledger = led.QuestionLedger()
    for revision in range(1, 30):
        ledger = ledger.record_ask(
            _entry(qid=f"q{revision}", asked_at_revision=revision), policy=POLICY
        )

    assert len(ledger.entries) == POLICY.max_ledger_entries
    # The oldest go, not the newest — a bound that dropped recent questions
    # would forget exactly what it was keeping.
    assert ledger.latest_for("q29") is not None
    assert ledger.latest_for("q1") is None


# --- resolution --------------------------------------------------------------


def test_marking_resolved_records_which_evidence_closed_it() -> None:
    ledger = led.QuestionLedger().record_ask(_entry(), policy=POLICY)

    ledger = ledger.mark_resolved("q1", evidence_id="c7", at_revision=3)

    entry = ledger.latest_for("q1")
    assert entry is not None
    assert entry.resolved is True
    assert entry.resolved_by_evidence_id == "c7"
    assert entry.resolved_at_revision == 3


def test_an_answer_resets_the_consecutive_ask_counter() -> None:
    ledger = led.QuestionLedger(consecutive_asks_without_new_evidence=1).record_ask(
        _entry(), policy=POLICY
    )

    ledger = ledger.mark_resolved("q1", evidence_id="c7", at_revision=3)

    assert ledger.consecutive_asks_without_new_evidence == 0


# --- §13.5: the five reasons and nothing else --------------------------------


def test_a_first_ask_needs_no_reason() -> None:
    permission = led.ask_permission("q1", ledger=led.QuestionLedger(), policy=POLICY)

    assert permission.allowed is True
    assert permission.reason is AskReason.FIRST_ASK


def test_a_repeat_without_one_of_the_five_reasons_is_suppressed() -> None:
    """This is the third turn of DRF-1542: the question was asked, it was
    answered, and nothing has changed. It is not asked again."""

    ledger = led.QuestionLedger().record_ask(_entry(), policy=POLICY)

    permission = led.ask_permission("q1", ledger=ledger, policy=POLICY)

    assert permission.allowed is False
    assert permission.outcome is led.AskOutcome.SUPPRESSED_ALREADY_ASKED
    assert permission.detail == led.ASK_SUPPRESSED_ALREADY_ASKED


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        ("answer_retracted", AskReason.REASK_ANSWER_RETRACTED),
        ("answer_expired", AskReason.REASK_ANSWER_EXPIRED),
        ("semantics_changed", AskReason.REASK_SEMANTICS_CHANGED),
        ("candidate_set_changed", AskReason.REASK_CANDIDATE_SET_CHANGED),
        ("safety_reevaluation", AskReason.REASK_SAFETY_REEVALUATION),
    ],
)
def test_each_of_the_five_reasons_permits_exactly_one_repeat(
    condition: str, expected: AskReason
) -> None:
    ledger = led.QuestionLedger().record_ask(_entry(), policy=POLICY)

    permission = led.ask_permission(
        "q1",
        ledger=ledger,
        policy=POLICY,
        conditions=led.ReaskConditions(**{condition: True}),
    )

    assert permission.allowed is True
    assert permission.reason is expected


def test_there_are_exactly_five_reask_reasons() -> None:
    """A sixth is a `spec_version` change, not a quiet edit (§13.5)."""

    assert set(led.ReaskConditions.__dataclass_fields__) == {
        "answer_retracted",
        "answer_expired",
        "semantics_changed",
        "candidate_set_changed",
        "safety_reevaluation",
    }
    assert set(REASK_REASONS) == {
        AskReason.REASK_ANSWER_RETRACTED,
        AskReason.REASK_ANSWER_EXPIRED,
        AskReason.REASK_SEMANTICS_CHANGED,
        AskReason.REASK_CANDIDATE_SET_CHANGED,
        AskReason.REASK_SAFETY_REEVALUATION,
    }


def test_two_simultaneous_reasons_give_one_deterministic_answer() -> None:
    """§17.1 — the same input must always name the same reason."""

    both = led.ReaskConditions(answer_expired=True, safety_reevaluation=True)

    assert both.first_matching() is AskReason.REASK_ANSWER_EXPIRED
    assert both.first_matching() is AskReason.REASK_ANSWER_EXPIRED


# --- the ceilings ------------------------------------------------------------


def test_the_second_ask_is_the_last_one() -> None:
    """MAX_ASKS_PER_QUESTION_ID = 2 is derived, not chosen: a third ask under the
    same reason would mean the reason did not work (§13.5)."""

    ledger = led.QuestionLedger()
    ledger = ledger.record_ask(_entry(asked_at_revision=2), policy=POLICY)
    ledger = ledger.record_ask(_entry(asked_at_revision=4), policy=POLICY)

    permission = led.ask_permission(
        "q1",
        ledger=ledger,
        policy=POLICY,
        conditions=led.ReaskConditions(answer_expired=True),
    )

    assert permission.allowed is False
    assert permission.outcome is led.AskOutcome.BUDGET_EXHAUSTED


def test_two_dry_asks_exhaust_the_budget_even_for_a_new_question() -> None:
    """The arithmetic that makes "five identical screens" impossible on its own.

    Checked before the first-ask branch on purpose: after two turns that asked
    and got nothing back, the next move is not another question.
    """

    ledger = led.QuestionLedger(consecutive_asks_without_new_evidence=2)

    permission = led.ask_permission("brand-new-question", ledger=ledger, policy=POLICY)

    assert permission.allowed is False
    assert permission.outcome is led.AskOutcome.BUDGET_EXHAUSTED
    assert permission.detail is not None
    assert "no new confirmed evidence" in permission.detail


def test_one_dry_ask_still_permits_a_question() -> None:
    """The positive half — without it, a ceiling set to zero would look correct."""

    ledger = led.QuestionLedger(consecutive_asks_without_new_evidence=1)

    assert led.ask_permission("q-new", ledger=ledger, policy=POLICY).allowed is True


def test_a_dry_ask_is_counted() -> None:
    ledger = led.QuestionLedger().note_ask_without_new_evidence()

    assert ledger.consecutive_asks_without_new_evidence == 1


def test_new_evidence_is_recognised_by_slot() -> None:
    assert led.new_evidence_on(("city",), evidence_slots=("city", "budget")) is True
    assert led.new_evidence_on(("city",), evidence_slots=("budget",)) is False


# --- serialisation, and the difference between empty and unreadable ---------


def test_round_trip_through_skill_state() -> None:
    ledger = led.QuestionLedger(consecutive_asks_without_new_evidence=1).record_ask(
        _entry(), policy=POLICY
    )
    ledger = ledger.mark_resolved("q1", evidence_id="c7", at_revision=3)

    restored = led.QuestionLedger.from_state(ledger.to_state())

    assert restored == ledger


def test_a_missing_key_is_an_empty_register() -> None:
    assert led.QuestionLedger.from_state(None) == led.QuestionLedger()


def test_an_unreadable_register_is_not_an_empty_one() -> None:
    """An empty register is a licence to ask. Reading a broken payload as empty
    is how a question loop restarts itself after a bad deploy."""

    with pytest.raises(led.LedgerUnavailable):
        led.QuestionLedger.from_state({"v": 99, "asked": []})

    with pytest.raises(led.LedgerUnavailable):
        led.QuestionLedger.from_state({"v": 1, "asked": [{"qid": "q1"}]})


def test_the_state_key_does_not_collide_with_the_keys_already_in_use() -> None:
    """Checked against `883b7539`: global_booking, coach_observation, no_match,
    time_pref, booking_flow, food_correction, food_scan, my_anketa."""

    assert led.STATE_KEY == "decision_readiness"
    assert led.STATE_KEY not in {
        "global_booking",
        "coach_observation",
        "no_match",
        "time_pref",
        "booking_flow",
        "food_correction",
        "food_scan",
        "my_anketa",
    }
