"""BOT-4 (DRF-2814) — ``ScreeningResult``, ядро без клинических значений.

Политика здесь — тестовая заглушка: она ничего не утверждает о клинике, а
только позволяет проверить, что ядро делает само (S1-приоритет, «не оценено»,
запрет допуска при NOT_SURE) независимо от того, что скажет политика.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 10, 6, 16, 0, tzinfo=UTC)
NOW = T0 + timedelta(days=1)
SCOPE = {"product_id": "p-1", "area": "abdomen", "offering_configuration_version": "3"}


def _offering():
    from apps.orchestrator.body_care.screening_result import OfferingContext

    return OfferingContext(
        "offering-1", canonical_version="1.1", offering_configuration_version="3"
    )


def _answer(value="NO", *, question="WR-Q1", scope=None, user="u-1", aid="a-1"):
    from apps.orchestrator.body_care.screening_answer import (
        ScreeningAnswer,
        ScreeningAnswerValue,
    )

    return ScreeningAnswer(
        id=aid,
        user_id=user,
        question_code=question,
        answer=ScreeningAnswerValue(value),
        answer_scope=dict(scope or SCOPE),
        source="chat",
        answered_at=None if value == "NOT_ASKED" else T0,
        expires_at=T0 + timedelta(days=30),
    )


@dataclass
class _Policy:
    """Тестовая политика: отвечает тем, что ей велели. Не клиника."""

    status: str = "CLEAR_TO_PROCEED"
    codes: tuple = ()
    questions: tuple = ("WR-Q1",)
    clinical_policy_version: str = "cp-test"
    questionnaire_version: str = "q-test"
    routing_version: str = "r-test"
    claim_policy_version: str = "c-test"
    seen: list = field(default_factory=list)

    def required_questions(self, offering):
        from apps.orchestrator.body_care.screening_result import RequiredQuestion

        return [RequiredQuestion(q, dict(SCOPE)) for q in self.questions]

    def codes_for(self, answer):
        from apps.orchestrator.body_care.reason_codes import ReasonCode

        return [ReasonCode(c) for c in self.codes]

    def status_for(self, codes, answers):
        from apps.orchestrator.body_care.screening_result import ScreeningStatus

        self.seen.append((codes, tuple(a.id for a in answers)))
        return ScreeningStatus(self.status)

    def actions_for(self, status, codes):
        return ("ACTION_FROM_POLICY",)


def _evaluate(answers, policy):
    from apps.orchestrator.body_care.screening_result import evaluate

    return evaluate(_offering(), user_id="u-1", answers=answers, policy=policy, now=NOW)


def test_e1_exactly_the_five_statuses_of_section_11():
    from apps.orchestrator.body_care.screening_result import ScreeningStatus

    assert [s.value for s in ScreeningStatus] == [
        "CLEAR_TO_PROCEED",
        "CLARIFICATION_REQUIRED",
        "MEDICAL_ASSESSMENT_REQUIRED",
        "DO_NOT_PROCEED",
        "S1_ROUTE",
    ]


def test_e2_no_policy_is_not_evaluated_never_clear():
    from apps.orchestrator.body_care.screening_result import NotEvaluated

    outcome = _evaluate([_answer()], None)

    assert isinstance(outcome, NotEvaluated)
    assert outcome.reason.value == "NO_POLICY"


@pytest.mark.parametrize(
    "answer_kwargs",
    [
        {"value": "NOT_ASKED"},
        {"scope": {**SCOPE, "product_id": "p-OTHER"}},  # чужой контекст
        {"user": "u-OTHER"},
        {"question": "WR-Q2"},
    ],
)
def test_e3_without_a_reusable_answer_it_is_incomplete_even_if_policy_would_clear(
    answer_kwargs,
):
    from apps.orchestrator.body_care.screening_result import NotEvaluated

    policy = _Policy(status="CLEAR_TO_PROCEED")
    outcome = _evaluate([_answer(**answer_kwargs)], policy)

    assert isinstance(outcome, NotEvaluated)
    assert outcome.reason.value == "ANSWERS_INCOMPLETE"
    assert [q.question_code for q in outcome.pending] == ["WR-Q1"]
    assert policy.seen == []  # политику не спрашивали: оценивать было нечего


def test_e4_an_expired_answer_is_incomplete():
    from apps.orchestrator.body_care.screening_result import NotEvaluated, evaluate

    outcome = evaluate(
        _offering(),
        user_id="u-1",
        answers=[_answer()],
        policy=_Policy(),
        now=T0 + timedelta(days=30),
    )

    assert isinstance(outcome, NotEvaluated)
    assert outcome.reason.value == "ANSWERS_INCOMPLETE"


def test_e5_s1_escalation_overrides_whatever_the_policy_said():
    outcome = _evaluate(
        [_answer("YES")], _Policy(status="CLEAR_TO_PROCEED", codes=("S1_ESCALATION",))
    )

    assert outcome.screening_status.value == "S1_ROUTE"
    assert outcome.as_record()["reason_codes"] == ["S1_ESCALATION"]


def test_e6_clear_with_not_sure_is_refused_as_a_program_error():
    from apps.orchestrator.body_care.screening_result import ScreeningPolicyViolation

    with pytest.raises(ScreeningPolicyViolation):
        _evaluate([_answer("NOT_SURE")], _Policy(status="CLEAR_TO_PROCEED"))


def test_e7_not_sure_with_a_non_clear_status_is_a_normal_result():
    outcome = _evaluate([_answer("NOT_SURE")], _Policy(status="CLARIFICATION_REQUIRED"))

    assert outcome.screening_status.value == "CLARIFICATION_REQUIRED"


def test_e8_the_record_carries_refs_and_six_versions_not_answers():
    answer = _answer("NO", aid="answer-42")
    outcome = _evaluate(
        [answer], _Policy(status="CLEAR_TO_PROCEED", codes=("SUNBURN", "OPEN_WOUND"))
    )
    record = outcome.as_record()

    assert record["source_answers"] == [
        {
            "answer_id": "answer-42",
            "question_code": "WR-Q1",
            "answer_context_hash": answer.answer_context_hash,
        }
    ]
    assert record["reason_codes"] == ["OPEN_WOUND", "SUNBURN"]  # отсортированы
    assert record["reason_code_registry_version"] == "0.2.0"
    assert {
        k: record[k]
        for k in (
            "canonical_version",
            "clinical_policy_version",
            "offering_configuration_version",
            "questionnaire_version",
            "routing_version",
            "claim_policy_version",
        )
    } == {
        "canonical_version": "1.1",
        "clinical_policy_version": "cp-test",
        "offering_configuration_version": "3",
        "questionnaire_version": "q-test",
        "routing_version": "r-test",
        "claim_policy_version": "c-test",
    }
    assert record["required_actions"] == ["ACTION_FROM_POLICY"]
    flat = repr(record)
    # Ответ (его значение) в записи не лежит: только ссылка и хэш контекста.
    assert answer.answer_context_hash in flat  # присутствие: ссылка есть
    assert "'answer': " not in flat  # empty-assert-ok: самого ответа в записи нет по DRF-2802


def test_e9_the_result_refuses_s1_code_without_s1_route():
    from apps.orchestrator.body_care.reason_codes import ReasonCode
    from apps.orchestrator.body_care.screening_result import (
        VERSION_FIELDS,
        ScreeningResult,
        ScreeningResultError,
        ScreeningStatus,
    )

    with pytest.raises(ScreeningResultError):
        ScreeningResult(
            screening_status=ScreeningStatus.CLEAR_TO_PROCEED,
            reason_codes=(ReasonCode.S1_ESCALATION,),
            required_actions=(),
            source_answers=(),
            versions={name: "v" for name in VERSION_FIELDS},
            evaluated_at=NOW,
        )


def test_e10_no_helper_turns_a_result_into_permission():
    """§18: у результата нет публичного «разрешающего» имени — решает статус."""

    from apps.orchestrator.body_care.screening_result import NotEvaluated, ScreeningResult

    for cls in (ScreeningResult, NotEvaluated):
        public = {n for n in dir(cls) if not n.startswith("_")}
        assert public  # присутствие
        for word in ("clear", "safe", "allow", "permit", "ok", "pass"):
            # empty-assert-ok: разрешающих имён нет по §18; публичные имена есть выше
            assert not [n for n in public if word in n.lower()], (cls.__name__, word)
