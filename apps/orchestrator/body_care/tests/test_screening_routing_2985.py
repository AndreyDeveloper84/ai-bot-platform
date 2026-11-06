"""BOT-4b (DRF-2985) — порядок строгости, реестр required_actions, D-5 в два слоя.

Литералы — из проекта D-5 ``d5-matrix-draft-0.1`` и решений владельца 20.10
(A5, В-4), не из самих перечислений: узел, собранный из константы, константу
не поймает.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

#: D-5 §1, от строжайшего; «не требуется» (P1) сегодня недостижим.
D5_STRICTNESS = (
    "S1_ROUTE",
    "DO_NOT_PROCEED",
    "MEDICAL_ASSESSMENT_REQUIRED",
    "NOT_EVALUATED",
    "CLARIFICATION_REQUIRED",
    "CLEAR_TO_PROCEED",
)

#: D-5 §5a + A5 и В-4 (20.10). MAR — показ, не исключение.
D5_CANDIDATE_CODES = {
    "S1_ROUTE": "ELIG_EXCLUDED_SAFETY",
    "DO_NOT_PROCEED": "ELIG_EXCLUDED_SAFETY",
    "MEDICAL_ASSESSMENT_REQUIRED": "ELIG_SHOWN_REQUIRES_CHECK",
    "NOT_EVALUATED": "ELIG_SHOWN_REQUIRES_CHECK",
    "CLARIFICATION_REQUIRED": "ELIG_SHOWN_REQUIRES_CHECK",
    "CLEAR_TO_PROCEED": "ELIG_SAFETY_CLEARED",
}

#: Кандидаты §16 + BOT-2 (дизайн BOT-4 §4), по порядку.
CANDIDATE_ACTIONS = (
    "CONTINUE_OR_MONITOR_PER_PROTOCOL",
    "STOP_CURRENT_EXPOSURE",
    "APPLY_PROTOCOL_AFTERCARE",
    "BLOCK_NEXT_SPA_STEP",
    "MEDICAL_ASSESSMENT_REQUIRED",
    "S1_ROUTE",
    "REASK_REQUIRED",
)


def _result(status: str, *, actions=(), codes=(), config_version="3"):
    from apps.orchestrator.body_care.reason_codes import ReasonCode
    from apps.orchestrator.body_care.screening_result import (
        VERSION_FIELDS,
        ScreeningResult,
        ScreeningStatus,
    )

    versions = {name: "v" for name in VERSION_FIELDS}
    versions["offering_configuration_version"] = config_version
    return ScreeningResult(
        screening_status=ScreeningStatus(status),
        reason_codes=tuple(ReasonCode(c) for c in codes),
        required_actions=actions,
        source_answers=(),
        versions=versions,
        evaluated_at=NOW,
    )


def _not_evaluated():
    from apps.orchestrator.body_care.screening_result import NotEvaluated, NotEvaluatedReason

    return NotEvaluated(NotEvaluatedReason.NO_POLICY)


def _outcome(name: str):
    if name == "NOT_EVALUATED":
        return _not_evaluated()
    return _result(name, codes=("S1_ESCALATION",) if name == "S1_ROUTE" else ())


# ── порядок строгости ──────────────────────────────────────────────────────


def test_r1_the_order_is_the_d5_draft_under_its_version():
    from apps.orchestrator.body_care.screening_routing import ROUTING_VERSION, STRICTNESS

    assert STRICTNESS == D5_STRICTNESS
    assert ROUTING_VERSION == "d5-matrix-draft-0.1"


@pytest.mark.parametrize("stricter_i", range(len(D5_STRICTNESS) - 1))
def test_r2_the_stricter_of_each_adjacent_pair_wins_in_any_order(stricter_i):
    from apps.orchestrator.body_care.screening_routing import strictest

    stricter, softer = D5_STRICTNESS[stricter_i], D5_STRICTNESS[stricter_i + 1]
    for pair in ((stricter, softer), (softer, stricter)):
        got = strictest([_outcome(n) for n in pair])
        name = (
            "NOT_EVALUATED"
            if got.__class__.__name__ == "NotEvaluated"
            else got.screening_status.value
        )
        assert name == stricter, pair


def test_r3_not_evaluated_beside_clear_is_not_clear():
    """A5: один неоценённый компонент — услуга «не оценена», а не допущена."""

    from apps.orchestrator.body_care.screening_result import NotEvaluated
    from apps.orchestrator.body_care.screening_routing import strictest

    got = strictest([_result("CLEAR_TO_PROCEED"), _not_evaluated(), _result("CLEAR_TO_PROCEED")])

    assert isinstance(got, NotEvaluated)


def test_r4_no_outcomes_is_an_error_not_a_permission():
    from apps.orchestrator.body_care.screening_routing import ScreeningRoutingError, strictest

    with pytest.raises(ScreeningRoutingError):
        strictest([])


# ── слой процедуры ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("name", "code"), sorted(D5_CANDIDATE_CODES.items()))
def test_p1_each_outcome_maps_to_its_candidate_code(name, code):
    from apps.orchestrator.body_care.screening_routing import candidate_effect

    assert candidate_effect(_outcome(name)).value == code


def test_p2_owner_v4_mar_is_shown_not_excluded():
    """В-4, 20.10: «нужна консультация» — показать, не скрыть."""

    from apps.orchestrator.body_care.screening_routing import candidate_effect

    assert (
        candidate_effect(_result("MEDICAL_ASSESSMENT_REQUIRED")).value
        == "ELIG_SHOWN_REQUIRES_CHECK"
    )


def test_p3_only_dnp_and_s1_exclude():
    from apps.orchestrator.body_care.screening_routing import CANDIDATE_EFFECT

    excluded = {
        name for name, code in CANDIDATE_EFFECT.items() if code.value == "ELIG_EXCLUDED_SAFETY"
    }

    assert excluded == {"DO_NOT_PROCEED", "S1_ROUTE"}


def test_p4_only_clear_is_cleared():
    from apps.orchestrator.body_care.screening_routing import CANDIDATE_EFFECT

    cleared = {
        name for name, code in CANDIDATE_EFFECT.items() if code.value == "ELIG_SAFETY_CLEARED"
    }

    assert cleared == {"CLEAR_TO_PROCEED"}


# ── слой хода ──────────────────────────────────────────────────────────────


def test_t1_s1_stops_the_turn_with_required_handoff():
    from apps.orchestrator.body_care.screening_routing import turn_effect
    from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyState

    effect = turn_effect(_outcome("S1_ROUTE"))

    assert effect is not None
    assert (effect.state, effect.handoff) == (SafetyState.STOP, Handoff.REQUIRED)


@pytest.mark.parametrize("name", [n for n in D5_STRICTNESS if n != "S1_ROUTE"])
def test_t2_nothing_but_s1_touches_the_turn(name):
    """D-5 В-2: «не оценено», DNP и MAR — про услугу, не про весь ход."""

    from apps.orchestrator.body_care.screening_routing import turn_effect

    assert turn_effect(_outcome(name)) is None


# ── провод резолвера (спецификация 1.4.0) ──────────────────────────────────


def test_w1_a_result_goes_on_the_wire_with_its_own_config_version():
    from apps.orchestrator.body_care.screening_result import OfferingContext
    from apps.orchestrator.body_care.screening_routing import wire_entry

    offering = OfferingContext(
        "salon-service-1", canonical_version="1.1", offering_configuration_version="4"
    )

    assert wire_entry(offering, _result("CLARIFICATION_REQUIRED", config_version="3")) == {
        "offering_ref": "salon-service-1",
        "offering_configuration_version": "3",
        "screening_status": "CLARIFICATION_REQUIRED",
    }


def test_w2_not_evaluated_is_not_sent():
    from apps.orchestrator.body_care.screening_result import OfferingContext
    from apps.orchestrator.body_care.screening_routing import wire_entry

    offering = OfferingContext(
        "salon-service-1", canonical_version="1.1", offering_configuration_version="4"
    )

    assert wire_entry(offering, _not_evaluated()) is None


# ── реестр required_actions ───────────────────────────────────────────────


def test_a1_the_registry_is_the_candidate_list_under_a_candidate_version():
    from apps.orchestrator.body_care.required_actions import (
        REQUIRED_ACTION_REGISTRY_VERSION,
        RequiredAction,
    )

    assert tuple(a.value for a in RequiredAction) == CANDIDATE_ACTIONS
    assert REQUIRED_ACTION_REGISTRY_VERSION == "0.1.0-candidate"


def test_a2_actions_are_deduplicated_and_in_registry_order():
    from apps.orchestrator.body_care.required_actions import RequiredAction as A

    result = _result(
        "CLARIFICATION_REQUIRED",
        actions=[A.REASK_REQUIRED, A.STOP_CURRENT_EXPOSURE, A.REASK_REQUIRED],
    )

    assert result.required_actions == (A.STOP_CURRENT_EXPOSURE, A.REASK_REQUIRED)


@pytest.mark.parametrize("actions", [("REASK_REQUIRED",), "S1_ROUTE", ("SOMETHING_NEW",)])
def test_a3_an_action_outside_the_registry_is_refused(actions):
    from apps.orchestrator.body_care.screening_result import ScreeningResultError

    with pytest.raises(ScreeningResultError):
        _result("CLARIFICATION_REQUIRED", actions=actions)


# ── D-7 В-1: смысл против формулировки ─────────────────────────────────────


def _answer(scope):
    from apps.orchestrator.body_care.screening_answer import ScreeningAnswer, ScreeningAnswerValue

    return ScreeningAnswer(
        id="a-1",
        user_id="u-1",
        question_code="Q-ALLERGY",
        answer=ScreeningAnswerValue.NO,
        answer_scope=scope,
        source="chat",
        answered_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )


def test_v1_a_semantics_change_asks_again():
    from apps.orchestrator.body_care.answer_reuse import reuse_verdict

    answer = _answer({"product_id": "p-1", "question_semantics_version": "1"})
    verdict = reuse_verdict(
        answer,
        user_id="u-1",
        question_code="Q-ALLERGY",
        scope={"product_id": "p-1", "question_semantics_version": "2"},
        now=NOW,
    )

    assert (verdict.result.value, verdict.reason.value) == ("REASK_REQUIRED", "CONTEXT_CHANGED")


def test_v2_the_same_semantics_is_reused_whatever_the_wording():
    """Формулировка в контекст не входит — после правки текста ответ тот же."""

    from apps.orchestrator.body_care.answer_reuse import reuse_verdict

    scope = {"product_id": "p-1", "question_semantics_version": "1"}
    verdict = reuse_verdict(
        _answer(scope), user_id="u-1", question_code="Q-ALLERGY", scope=scope, now=NOW
    )

    assert verdict.result.value == "REUSABLE"


def test_v3_the_wording_version_cannot_enter_an_answer_context():
    from apps.orchestrator.body_care.screening_answer import ScreeningAnswerError

    with pytest.raises(ScreeningAnswerError):
        _answer({"product_id": "p-1", "question_wording_version": "7"})


def test_v4_nor_the_context_a_question_is_asked_in():
    from apps.orchestrator.body_care.screening_result import RequiredQuestion, ScreeningResultError

    with pytest.raises(ScreeningResultError):
        RequiredQuestion("Q-ALLERGY", {"product_id": "p-1", "question_wording_version": "7"})
