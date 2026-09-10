"""§11 — the five states, in the normative order, and §15.2's fail-closed table.

The order of §11 is not stylistic. Safety is checked before availability,
availability before arithmetic, and structural impossibility before required
context — because asking a person to narrow a set that cannot produce a
recommendation spends their patience on a question that cannot pay out.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import reason_codes as rc
from apps.orchestrator.decision_readiness.policy import ControlledPolicy
from apps.orchestrator.decision_readiness.required_context import RequiredContextSpec
from apps.orchestrator.decision_readiness.safety_input import SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.tests.conftest import (
    REVISION,
    SplittingProbe,
    candidates,
    make_input,
)


def _spec(*slots: object) -> RequiredContextSpec:
    return RequiredContextSpec(policy_version=1, slots=tuple(slots))  # type: ignore[arg-type]


# --- §11 step 1: safety ------------------------------------------------------


def test_stop_blocks_before_anything_else() -> None:
    output = eng.evaluate(
        make_input(
            safety=SafetyResult(
                state=SafetyState.STOP, evaluated_at_revision=REVISION, rule_id="R-7"
            )
        )
    )

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.allow_recommend is False
    assert output.blockers[0].blocker_type is eng.BlockerType.SAFETY_STOP
    assert rc.SAFETY_STOP in output.reason_codes


def test_unevaluated_safety_blocks_rather_than_defaulting_to_normal() -> None:
    """§11.4 — a silent NORMAL is forbidden. Not having run is UNKNOWN."""

    output = eng.evaluate(make_input(safety=SafetyResult.not_evaluated()))

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.blockers[0].blocker_type is eng.BlockerType.SAFETY_UNKNOWN
    assert rc.SAFETY_UNKNOWN in output.reason_codes


def test_clarify_asks_rather_than_blocking() -> None:
    """§11.1 — both readings forbid recommending; only this one describes a way out."""

    output = eng.evaluate(
        make_input(
            safety=SafetyResult(state=SafetyState.CLARIFY, evaluated_at_revision=REVISION),
            catalog=make_input().catalog,
        )
    )

    assert output.readiness_state is eng.ReadinessState.NEEDS_REQUIRED_CONTEXT
    assert output.allow_recommend is False
    assert rc.SAFETY_CLARIFY_REQUIRED in output.reason_codes


def test_caution_is_recorded_without_stopping_the_turn() -> None:
    output = eng.evaluate(
        make_input(safety=SafetyResult(state=SafetyState.CAUTION, evaluated_at_revision=REVISION))
    )

    assert rc.SAFETY_CAUTION_CONSTRAINED in output.reason_codes
    assert output.readiness_state is eng.ReadinessState.READY


# --- §11 step 2 / §15.2: availability ---------------------------------------


def _uncalibrated_policy() -> ControlledPolicy:
    """An uncalibrated threshold is spelled UNCALIBRATED, never `None` or `0.0`."""

    return ControlledPolicy(policy_version=1, ranking_comparison_depth=3)


@pytest.mark.parametrize(
    ("override", "fragment"),
    [
        (
            {"availability": eng.InputAvailability(probe_available=True, candidates_fresh=True)},
            "ledger",
        ),
        (
            {"availability": eng.InputAvailability(ledger_readable=True, candidates_fresh=True)},
            "probe",
        ),
        (
            {"availability": eng.InputAvailability(ledger_readable=True, probe_available=True)},
            "stale",
        ),
        ({"probe": None}, "probe"),
        ({"candidates": candidates(recommendation_eligible_count=None)}, "recommendation_eligible"),
        ({"measures": eng.Measures(completeness=1.0, conflicts=None)}, "conflicts"),
        ({"policy": _uncalibrated_policy()}, "uncalibrated"),
    ],
)
def test_every_missing_input_blocks_with_its_own_reason(
    override: dict[str, object], fragment: str
) -> None:
    """§15.2 is total, and each row answers with its own name.

    One outward state, separate reasons inward: a caller that only saw
    "unavailable" could not tell a broken register from an uncalibrated
    threshold, and those need different people to fix them.
    """

    output = eng.evaluate(make_input(**override))

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.blockers[0].blocker_type is eng.BlockerType.READINESS_INPUT_UNAVAILABLE
    assert fragment in output.blockers[0].attribution
    assert rc.BLOCK_READINESS_INPUT_UNAVAILABLE in output.reason_codes


def test_an_uncalibrated_threshold_blocks_at_the_availability_check() -> None:
    """OD-DR-1: until calibration the threshold has no value, so the engine cannot
    tell READY from NEEDS_DISCRIMINATION and must not claim either.

    The attribution is asserted in full rather than by substring. There are two
    guards for this property — the availability check here and a total-function
    fallback in step 6 — and a loose assertion stays green when either one is
    removed, which would leave one of the two proved by nothing. Each is pinned
    by the words only it produces. Found by substitution: removing the
    availability check left the whole suite green.
    """

    output = eng.evaluate(make_input(policy=_uncalibrated_policy()))

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert output.blockers[0].attribution == "tau_separation is uncalibrated (OD-DR-1, DRF-1519)"


def test_an_uncomputed_separation_blocks_at_the_step_six_fallback() -> None:
    """The other half of the same property, reached by the other route.

    The availability check catches an uncalibrated *threshold*. A `separation`
    the resolver never computed passes that check and arrives at step 6, where
    it must still block: absent is not zero, and a comparison against a missing
    number is not a comparison that came out false.
    """

    reference = make_input()

    state, blockers, _verdicts, _codes = eng.f(
        state=reference.state,
        evidence=reference.evidence,
        candidates=candidates(separation=None),
        safety=SafetyResult(state=SafetyState.NORMAL, evaluated_at_revision=REVISION),
        spec=reference.required_context_spec,
        availability=eng.InputAvailability(
            ledger_readable=True, probe_available=True, candidates_fresh=True
        ),
        state_revision=REVISION,
        mode=reference.mode,
        measures=eng.Measures(completeness=1.0, conflicts=0),
        probe_usable=True,
        policy=make_input().policy,
        execution_required_params=frozenset(),
        current_need=None,
        probe=reference.probe,
    )

    assert state is eng.ReadinessState.BLOCKED
    assert blockers[0].attribution == "separation threshold uncalibrated"


def test_stale_safety_blocks() -> None:
    """P3 — a verdict computed before the person's last message answers a different
    question."""

    output = eng.evaluate(
        make_input(
            safety=SafetyResult(state=SafetyState.NORMAL, evaluated_at_revision=REVISION - 1)
        )
    )

    assert output.readiness_state is eng.ReadinessState.BLOCKED
    assert "safety evaluated at revision" in output.blockers[0].attribution


# --- §11 step 3: structural impossibility -----------------------------------


def test_visible_but_not_recommendable_blocks_with_its_own_name() -> None:
    """Canon §14: showable, bookable by explicit choice, not recommendable. The
    silent substitution is what is forbidden."""

    output = eng.evaluate(
        make_input(
            candidates=candidates(
                visible_count=5, recommendation_eligible_count=0, eligible_ids=(), ordered_ids=()
            )
        )
    )

    assert output.blockers[0].blocker_type is eng.BlockerType.CATALOG_NOT_RECOMMENDABLE
    assert rc.BLOCK_CATALOG_NOT_RECOMMENDABLE in output.reason_codes


def test_nobody_at_all_blocks_differently() -> None:
    output = eng.evaluate(
        make_input(
            candidates=candidates(
                visible_count=0, recommendation_eligible_count=0, eligible_ids=(), ordered_ids=()
            )
        )
    )

    assert output.blockers[0].blocker_type is eng.BlockerType.NO_ADMISSIBLE_CANDIDATES
    assert rc.BLOCK_NO_ADMISSIBLE_CANDIDATES in output.reason_codes


# --- §11 step 4: required context -------------------------------------------


def test_an_unsatisfied_required_slot_asks() -> None:
    output = eng.evaluate(make_input(evidence=()))

    assert output.readiness_state is eng.ReadinessState.NEEDS_REQUIRED_CONTEXT
    assert output.allow_recommend is False
    assert rc.slot_code(rc.REQ_CONTEXT_UNSATISFIED, "city") in output.reason_codes
    assert output.next_question is not None
    assert output.next_question.kind.value == "required_context"


def test_the_required_test_of_section_3_1_end_to_end() -> None:
    """Spec §3.1's mandated test: signals cover every required slot, evidence is
    empty, and the answer must still be "ask"."""

    from apps.orchestrator.decision_readiness.evidence import ModelSignal

    output = eng.evaluate(
        make_input(
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
    )

    assert output.readiness_state is eng.ReadinessState.NEEDS_REQUIRED_CONTEXT
    assert output.allow_recommend is False
    assert rc.EVID_MODEL_SIGNAL_NOT_EVIDENCE in output.reason_codes
    assert output.model_signals_rejected == (
        {
            "slot": "city",
            "extractor_id": "max-concierge-v1",
            "reason": rc.EVID_MODEL_SIGNAL_NOT_EVIDENCE,
        },
    )


# --- §11 step 5: grounding --------------------------------------------------


def test_a_need_nothing_narrowed_is_insufficient_evidence() -> None:
    """§11.2 — grounded means the set narrowed, not that something was said."""

    output = eng.evaluate(make_input(probe=SplittingProbe(narrows=False)))

    assert output.readiness_state is eng.ReadinessState.INSUFFICIENT_EVIDENCE


# --- §11 steps 6–7 ----------------------------------------------------------


def test_a_standing_conflict_forbids_ready() -> None:
    """§11.5 — recommending over an unresolved refusal is the DRF-1474 class:
    the bot promises to find the thing it just said it does not have."""

    output = eng.evaluate(make_input(measures=eng.Measures(completeness=1.0, conflicts=1)))

    assert output.readiness_state is eng.ReadinessState.NEEDS_DISCRIMINATION
    assert rc.MEASURE_CONFLICTS_PRESENT in output.reason_codes


def test_separation_below_tau_asks_a_discriminating_question() -> None:
    output = eng.evaluate(make_input(candidates=candidates(separation=0.1)))

    assert output.readiness_state is eng.ReadinessState.NEEDS_DISCRIMINATION
    assert rc.MEASURE_SEPARATION_BELOW_TAU in output.reason_codes
    assert output.next_question is not None
    assert output.next_question.kind.value == "discrimination"


def test_everything_settled_is_ready() -> None:
    output = eng.evaluate(make_input())

    assert output.readiness_state is eng.ReadinessState.READY
    assert output.allow_recommend is True
    assert output.next_question is None  # §5 invariant
    assert rc.STATE_READY in output.reason_codes


# --- §5 output invariants ----------------------------------------------------


def test_blocked_and_blockers_imply_each_other() -> None:
    blocked = eng.evaluate(make_input(safety=SafetyResult.not_evaluated()))
    ready = eng.evaluate(make_input())

    assert (blocked.readiness_state is eng.ReadinessState.BLOCKED) == bool(blocked.blockers)
    assert (ready.readiness_state is eng.ReadinessState.BLOCKED) == bool(ready.blockers)


def test_allow_recommend_only_in_two_states() -> None:
    for override in (
        {},
        {"candidates": candidates(separation=0.1)},
        {"evidence": ()},
        {"probe": SplittingProbe(narrows=False)},
        {"safety": SafetyResult.not_evaluated()},
    ):
        output = eng.evaluate(make_input(**override))
        if output.allow_recommend:
            assert output.readiness_state in {
                eng.ReadinessState.READY,
                eng.ReadinessState.NEEDS_DISCRIMINATION,
            }


def test_every_output_carries_at_least_one_reason_code() -> None:
    output = eng.evaluate(make_input())

    assert len(output.reason_codes) >= 1
    assert all(rc.is_known(code) for code in output.reason_codes)


def test_reason_codes_are_sorted_and_unique() -> None:
    output = eng.evaluate(make_input(evidence=()))

    assert list(output.reason_codes) == sorted(set(output.reason_codes))
