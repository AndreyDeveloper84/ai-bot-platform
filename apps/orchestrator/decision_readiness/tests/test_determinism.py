"""§17.1 and §17.3 — the same input gives the same answer, byte for byte.

DRF-1519 named this the main proof that a model's self-assessment has actually
been replaced: a decision you can re-run and get the same thing from is a
decision nobody's mood entered.

The spec asks for a thousand runs with zero divergence. A thousand identical
calls of a pure function tests the function's purity once and the test runner
999 times, so the loop here is short and the *shape* of non-determinism is
attacked directly instead: dictionary order, set iteration, and the tie-break in
question selection.
"""

from __future__ import annotations

from dataclasses import replace

from apps.orchestrator.decision_readiness import decision as dec
from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import questions as q
from apps.orchestrator.decision_readiness.tests.conftest import (
    REVISION,
    candidates,
    discrimination_entry,
    make_input,
)


def test_the_same_input_gives_an_identical_output() -> None:
    request = make_input(candidates=candidates(separation=0.1))

    first = eng.evaluate(request)
    second = eng.evaluate(request)

    assert first == second
    assert first.reason_codes == second.reason_codes
    assert first.next_question == second.next_question
    assert first.readiness_key == second.readiness_key


def test_repeated_evaluation_is_stable_across_many_runs() -> None:
    request = make_input(candidates=candidates(separation=0.1))
    reference = eng.evaluate(request)

    assert all(eng.evaluate(request) == reference for _ in range(50))


def test_catalog_order_does_not_change_the_chosen_question() -> None:
    """Set and dict iteration order is the usual source of a decision that
    "sometimes" differs."""

    a = discrimination_entry(discriminator_key="price_band")
    b = discrimination_entry(discriminator_key="duration", target_slots=("duration",))

    one_way = eng.evaluate(
        make_input(candidates=candidates(separation=0.1), catalog=q.QuestionCatalog(entries=(a, b)))
    )
    other_way = eng.evaluate(
        make_input(candidates=candidates(separation=0.1), catalog=q.QuestionCatalog(entries=(b, a)))
    )

    assert one_way.next_question is not None
    assert one_way.next_question.question_id == other_way.next_question.question_id  # type: ignore[union-attr]


def test_reason_codes_come_out_sorted_and_deduplicated() -> None:
    output = eng.evaluate(make_input(evidence=()))

    assert list(output.reason_codes) == sorted(set(output.reason_codes))


def test_the_readiness_key_changes_with_the_revision() -> None:
    """§17.3 — this is what makes a stale callback distinguishable (canon §13.4)."""

    request = make_input()
    later = replace(request, state_revision=REVISION + 1)

    assert eng.readiness_key(request) != eng.readiness_key(later)


def test_the_readiness_key_changes_with_the_evidence() -> None:
    request = make_input()
    without = replace(request, evidence=())

    assert eng.readiness_key(request) != eng.readiness_key(without)


def test_the_readiness_key_changes_with_delegation() -> None:
    request = make_input()
    delegated = replace(request, delegation=eng.Delegation.HIGH)

    assert eng.readiness_key(request) != eng.readiness_key(delegated)


def test_the_readiness_key_is_stable_for_the_same_input() -> None:
    request = make_input()

    assert eng.readiness_key(request) == eng.readiness_key(request)


def test_evidence_order_does_not_change_the_key() -> None:
    """Two deliveries of the same facts in a different order are the same facts."""

    from apps.orchestrator.decision_readiness.tests.conftest import city_evidence

    a = city_evidence(slot="city", value="Пенза")
    b = city_evidence(slot="budget", value="low")
    b = replace(b, evidence_id="c-budget")

    forwards = replace(make_input(), evidence=(a, b))
    backwards = replace(make_input(), evidence=(b, a))

    assert eng.readiness_key(forwards) == eng.readiness_key(backwards)


def test_the_decision_id_is_derived_not_generated() -> None:
    """A random id would satisfy canon §13.4 and break §17.1. Two runs of the
    same decision must carry the same id, so a retried delivery is recognisable."""

    request = make_input(candidates=candidates(separation=0.1))

    first = dec.project(eng.evaluate(request), state_revision=REVISION)
    second = dec.project(eng.evaluate(request), state_revision=REVISION)

    assert first.decision_id == second.decision_id
    assert len(first.decision_id) == 16


def test_different_decisions_get_different_ids() -> None:
    asking = dec.project(
        eng.evaluate(make_input(candidates=candidates(separation=0.1))), state_revision=REVISION
    )
    recommending = dec.project(eng.evaluate(make_input()), state_revision=REVISION)

    assert asking.decision_id != recommending.decision_id
