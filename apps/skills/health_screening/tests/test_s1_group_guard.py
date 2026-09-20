"""Сторож S1-детекторов по семи рабочим группам (DRF-1998, S-1c).

CLINICAL-F01 (BLOCKER): у S1-детекторов не было ни сторожа, ни gate, привязанного к
семи группам OD-SAF-11 п. 8, — а живые детекторы молча пропускали длинные сообщения,
«трудно дышать» и целые группы. Этот сторож задаёт матрицу «группа × вид»
(``s1_fixtures.py``). Сегодняшние пропуски стоят в реестре со счётчиком. Лист,
который чинит детектор, краснеет здесь и снимает свою запись.

Runtime не меняется: тесты только читают ``pre_check`` и ``classify``.
"""

from __future__ import annotations

from collections import Counter

import pytest

from apps.orchestrator.safety.pre_check import SafetyVerdict, pre_check
from apps.skills.health_screening.classifier import PainSignal, classify
from apps.skills.health_screening.tests.s1_fixtures import (
    FIXTURES,
    GROUPS,
    KINDS,
    KNOWN_MISSES,
    KNOWN_MISSES_COUNT,
    LONG_NEGATIVES,
    LONG_PREFIX,
    S1A,
    S1B,
    S1_DECISION,
    S1D,
    S1Fixture,
)


def s1_detected(text: str) -> bool:
    """Как на живых путях MAX: сначала гейт (HANDOFF или MEDICAL — DRF-2000),
    затем классификатор (RED_FLAG)."""

    verdict = pre_check(text).verdict
    return (
        verdict is SafetyVerdict.HANDOFF
        or verdict is SafetyVerdict.MEDICAL
        or classify(text) is PainSignal.RED_FLAG
    )


def _fixture_id(fixture: S1Fixture) -> str:
    return f"{fixture.group}-{fixture.kind}-{fixture.core}"


_CHECKED = [f for f in FIXTURES if f.key not in KNOWN_MISSES]
_MISSING = [f for f in FIXTURES if f.key in KNOWN_MISSES]


def test_every_group_has_every_kind() -> None:
    have = Counter((f.group, f.kind) for f in FIXTURES)
    absent = [(group, kind) for group in GROUPS for kind in KINDS if have[(group, kind)] == 0]

    assert absent == []
    assert set(GROUPS) == {f"G{n}" for n in range(1, 8)}


def test_long_fixtures_are_past_the_classifier_length_cap() -> None:
    """Иначе «длинное сообщение» проверяло бы короткое: префикс сам длиннее 200."""

    long_ones = [f for f in FIXTURES if f.kind == "long"]

    assert len(LONG_PREFIX) > 200
    assert len(long_ones) == len(GROUPS)
    assert all(len(f.text) > 200 for f in long_ones)


@pytest.mark.parametrize("fixture", _CHECKED, ids=_fixture_id)
def test_detector_meets_the_expectation(fixture: S1Fixture) -> None:
    assert s1_detected(fixture.text) is fixture.expected_detected


@pytest.mark.parametrize("fixture", _MISSING, ids=_fixture_id)
def test_a_known_miss_is_still_a_miss(fixture: S1Fixture) -> None:
    leaf = KNOWN_MISSES[fixture.key]

    assert s1_detected(fixture.text) is not fixture.expected_detected, (
        f"детектор теперь ловит {fixture.key}: сними запись ({leaf}) из KNOWN_MISSES "
        "и уменьши KNOWN_MISSES_COUNT"
    )


@pytest.mark.parametrize("text", LONG_NEGATIVES, ids=lambda t: t[:40])
def test_a_long_message_without_s1_is_not_s1(text: str) -> None:
    """Порог длины снят (S-1a): длинный рассказ без S1 не должен давать S1."""

    assert len(text) > 200
    assert s1_detected(text) is False


def test_the_known_miss_register_is_counted_and_points_at_fixtures() -> None:
    keys = {f.key for f in FIXTURES}

    assert len(KNOWN_MISSES) == KNOWN_MISSES_COUNT
    assert [key for key in KNOWN_MISSES if key not in keys] == []
    assert set(KNOWN_MISSES.values()) <= {S1A, S1B, S1D, S1_DECISION}
    assert len(FIXTURES) == len(keys)
