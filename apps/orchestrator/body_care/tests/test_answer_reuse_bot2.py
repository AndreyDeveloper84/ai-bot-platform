"""BOT-2 — область ответа и запрет авто-реюза (контракт §9, §10.1, §18, F-BC-002)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
SCOPE_A = {"product_id": "product-A", "area": "abdomen", "offering_configuration_version": "3"}


def _answer(answer="NO", *, scope=None, user="u-1", question="WR-Q1", answered_at=T0):
    from apps.orchestrator.body_care.screening_answer import (
        ScreeningAnswer,
        ScreeningAnswerValue,
    )

    return ScreeningAnswer(
        id="a-1",
        user_id=user,
        question_code=question,
        answer=ScreeningAnswerValue(answer),
        answer_scope=dict(scope or SCOPE_A),
        source="chat",
        answered_at=None if answer == "NOT_ASKED" else answered_at,
        expires_at=T0 + timedelta(days=30),
    )


def _verdict(answer, *, scope=None, user="u-1", question="WR-Q1", now=None):
    from apps.orchestrator.body_care.answer_reuse import reuse_verdict

    return reuse_verdict(
        answer,
        user_id=user,
        question_code=question,
        scope=dict(scope or SCOPE_A),
        now=now or T0 + timedelta(days=1),
    )


def _pair(verdict) -> tuple[str, str]:
    return verdict.result.value, verdict.reason.value


def test_b1_the_four_results_of_section_10_1():
    from apps.orchestrator.body_care.answer_reuse import RecheckResult

    assert [r.value for r in RecheckResult] == [
        "REUSABLE",
        "PARTIALLY_REUSABLE",
        "REASK_REQUIRED",
        "FULL_RESCREEN_REQUIRED",
    ]


def test_b2_f_bc_002_allergy_for_product_a_is_reasked_for_product_b():
    yes_allergy = _answer("YES")

    verdict = _verdict(yes_allergy, scope={**SCOPE_A, "product_id": "product-B"})

    assert _pair(verdict) == ("REASK_REQUIRED", "CONTEXT_CHANGED")


def test_b3_an_area_answer_is_not_carried_to_another_area():
    verdict = _verdict(_answer("NO"), scope={**SCOPE_A, "area": "thighs"})

    assert _pair(verdict) == ("REASK_REQUIRED", "CONTEXT_CHANGED")


def test_b4_a_new_configuration_version_reasks():
    verdict = _verdict(_answer("NO"), scope={**SCOPE_A, "offering_configuration_version": "4"})

    assert _pair(verdict) == ("REASK_REQUIRED", "CONTEXT_CHANGED")


def test_b5_an_extra_scope_key_is_a_different_context():
    verdict = _verdict(_answer("NO"), scope={**SCOPE_A, "application_mode": "heat"})

    assert _pair(verdict) == ("REASK_REQUIRED", "CONTEXT_CHANGED")


def test_b6_same_context_unexpired_is_the_only_reuse():
    for value in ("YES", "NO", "NOT_SURE"):
        verdict = _verdict(_answer(value))
        assert _pair(verdict) == ("REUSABLE", "SAME_CONTEXT_UNEXPIRED"), value


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"user": "u-2"}, "OTHER_PERSON"),
        ({"question": "WR-Q2"}, "OTHER_QUESTION"),
        ({"now": T0 + timedelta(days=30)}, "EXPIRED"),
    ],
)
def test_b7_other_person_question_or_expiry_reask(kwargs, reason):
    verdict = _verdict(_answer("NO"), **kwargs)

    assert _pair(verdict) == ("REASK_REQUIRED", reason)


def test_b8_not_asked_is_never_reused():
    verdict = _verdict(_answer("NOT_ASKED"))

    assert _pair(verdict) == ("REASK_REQUIRED", "NOT_ASKED")


def test_b9_partial_reuse_and_full_rescreen_are_never_emitted_here():
    """§10: без правила политики старый ответ не объявляется reusable — даже частично."""

    from apps.orchestrator.body_care.answer_reuse import EMITTED_RESULTS, RecheckResult

    assert EMITTED_RESULTS == {RecheckResult.REUSABLE, RecheckResult.REASK_REQUIRED}
    seen = set()
    for scope in (SCOPE_A, {**SCOPE_A, "product_id": "p-X"}, {"area": "back"}):
        for value in ("YES", "NO", "NOT_SURE", "NOT_ASKED"):
            seen.add(_verdict(_answer(value), scope=scope).result)
    assert seen <= EMITTED_RESULTS
    assert RecheckResult.REUSABLE in seen and RecheckResult.REASK_REQUIRED in seen
