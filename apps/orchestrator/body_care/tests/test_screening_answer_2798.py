"""BOT-1 (DRF-2798) — ``ScreeningAnswer``, контракт §8.

Значения ответов сверяются литералами контракта, не самим перечислением.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
SCOPE = {"product_id": "p-1", "area": "abdomen", "offering_configuration_version": "3"}


def _answer(**over):
    from apps.orchestrator.body_care.screening_answer import (
        ScreeningAnswer,
        ScreeningAnswerValue,
    )

    kw = {
        "id": "a-1",
        "user_id": "u-1",
        "question_code": "WR-Q1",
        "answer": ScreeningAnswerValue.NO,
        "answer_scope": dict(SCOPE),
        "source": "chat",
        "answered_at": T0,
        "expires_at": T0 + timedelta(days=30),
    }
    kw.update(over)
    return ScreeningAnswer(**kw)


def test_a1_exactly_the_four_answers_of_section_8():
    from apps.orchestrator.body_care.screening_answer import ScreeningAnswerValue

    assert [v.value for v in ScreeningAnswerValue] == ["YES", "NO", "NOT_SURE", "NOT_ASKED"]


def test_a2_not_sure_is_not_no():
    from apps.orchestrator.body_care.screening_answer import ScreeningAnswerValue

    not_sure = _answer(answer=ScreeningAnswerValue.NOT_SURE)
    no = _answer(answer=ScreeningAnswerValue.NO)

    assert not_sure.answer is ScreeningAnswerValue.NOT_SURE
    assert not_sure.answer != no.answer
    assert not_sure.as_record()["answer"] == "NOT_SURE"


def test_a3_no_helper_turns_an_answer_into_clearance():
    """§18: NO — не медицинский допуск. У типа нет ни одного «разрешающего» метода."""

    from apps.orchestrator.body_care.screening_answer import ScreeningAnswer

    public = {n for n in dir(ScreeningAnswer) if not n.startswith("_")}
    assert "is_expired" in public  # присутствие: публичные методы у типа есть
    for word in ("clear", "safe", "allow", "permit", "ok", "pass"):
        # empty-assert-ok: разрешающих имён нет по §18; публичные методы перечислены выше
        assert not [n for n in public if word in n.lower()], word


def test_a4_the_context_hash_is_computed_deterministic_and_scope_bound():
    first = _answer()
    reordered = _answer(answer_scope=dict(reversed(list(SCOPE.items()))))
    other_area = _answer(answer_scope={**SCOPE, "area": "thighs"})
    other_question = _answer(question_code="WR-Q2")

    assert len(first.answer_context_hash) == 64
    assert first.answer_context_hash == reordered.answer_context_hash
    assert first.answer_context_hash != other_area.answer_context_hash
    assert first.answer_context_hash != other_question.answer_context_hash


def test_a5_the_hash_is_a_sha256_of_canonical_json_not_python_hash():
    import hashlib
    import json

    expected = hashlib.sha256(
        json.dumps(
            {"question_code": "WR-Q1", "scope": SCOPE},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()

    assert _answer().answer_context_hash == expected


def test_a6_the_hash_cannot_be_passed_in():
    with pytest.raises(TypeError):
        _answer(answer_context_hash="forged")


def test_a7_scope_is_frozen_after_the_hash():
    answer = _answer()

    with pytest.raises(TypeError):
        answer.answer_scope["area"] = "thighs"  # type: ignore[index]


@pytest.mark.parametrize(
    "over",
    [
        {"answer_scope": {}},
        {"question_code": " "},
        {"user_id": ""},
        {"source": ""},
        {"answer": "NO"},  # строка вместо члена реестра
        {"answered_at": None},  # у ответа NO есть момент
        {"expires_at": T0},  # не позже ответа
        {"answered_at": datetime(2026, 10, 6, 12, 0)},  # без пояса
    ],
)
def test_a8_invariants_refuse(over):
    from apps.orchestrator.body_care.screening_answer import ScreeningAnswerError

    with pytest.raises(ScreeningAnswerError):
        _answer(**over)


def test_a9_not_asked_has_no_answer_moment():
    from apps.orchestrator.body_care.screening_answer import (
        ScreeningAnswerError,
        ScreeningAnswerValue,
    )

    asked = _answer(answer=ScreeningAnswerValue.NOT_ASKED, answered_at=None)
    assert asked.as_record()["answered_at"] is None
    with pytest.raises(ScreeningAnswerError):
        _answer(answer=ScreeningAnswerValue.NOT_ASKED, answered_at=T0)


def test_a10_expiry_boundary_counts_as_expired():
    answer = _answer()
    expires = answer.expires_at

    assert answer.is_expired(expires - timedelta(seconds=1)) is False
    assert answer.is_expired(expires) is True


def test_a11_the_record_carries_every_field_of_section_8_in_order():
    assert list(_answer().as_record()) == [
        "id",
        "user_id",
        "question_code",
        "answer",
        "answer_scope",
        "answer_context_hash",
        "source",
        "answered_at",
        "expires_at",
    ]
