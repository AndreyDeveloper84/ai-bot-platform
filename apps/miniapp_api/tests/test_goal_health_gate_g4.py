"""Mini App goal gate — explicit / recent-resolved G4 on every free-text entry.

[OD-BOT §161, §164]: an explicit G4 sign in ``goal_text``, in the anketa
``answer.text`` or in ``safety_answer`` is a hard stop that carries the canonical
:data:`MEDICAL_EMERGENCY_TEXT_V2` from the server — the Mini App renders
``safety.text`` as is (``GoalSelectScreen.tsx``), there is no client copy. The
crisis stop stays on its own route and text.

Technical checks only. Implementation of registered owner policy does not
constitute CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from django.core.cache import cache

from apps.miniapp_api.health_gate import (
    KIND_CRISIS,
    KIND_RED_FLAG,
    SAFETY_ANSWER_FIELD,
    screen_goal_body,
)
from apps.orchestrator.safety.gate import CRISIS_REPLY_TEXT
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2

G4_TEXTS = (
    "Внезапно онемела правая сторона тела",
    "Перекосило лицо",
    "Резко пропало зрение",
    "Внезапно потеряла равновесие",
    "Внезапно перекосило лицо, но сейчас прошло",
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.parametrize("text", G4_TEXTS)
def test_goal_text_g4_is_a_red_flag_stop_with_the_canonical_text(text: str) -> None:
    stop, forward = screen_goal_body(Mock(pk=4101), {"goal_text": text})
    assert stop is not None
    assert stop.kind == KIND_RED_FLAG
    assert stop.text == MEDICAL_EMERGENCY_TEXT_V2
    assert stop.as_payload() == {"kind": KIND_RED_FLAG, "text": MEDICAL_EMERGENCY_TEXT_V2}
    assert SAFETY_ANSWER_FIELD not in forward


@pytest.mark.parametrize("text", G4_TEXTS)
def test_anketa_answer_text_g4_is_the_same_stop(text: str) -> None:
    stop, _ = screen_goal_body(Mock(pk=4102), {"answer": {"text": text}})
    assert stop is not None
    assert stop.kind == KIND_RED_FLAG
    assert stop.text == MEDICAL_EMERGENCY_TEXT_V2


@pytest.mark.parametrize("text", G4_TEXTS)
def test_safety_answer_g4_stops_even_after_a_pain_question(text: str) -> None:
    body = {"goal_text": "болит спина", SAFETY_ANSWER_FIELD: text}
    stop, forward = screen_goal_body(Mock(pk=4103), body)
    assert stop is not None
    assert stop.kind == KIND_RED_FLAG
    assert stop.text == MEDICAL_EMERGENCY_TEXT_V2
    assert SAFETY_ANSWER_FIELD not in forward


def test_negated_g4_in_goal_text_is_not_a_red_flag() -> None:
    stop, _ = screen_goal_body(Mock(pk=4104), {"goal_text": "Лицо не перекосило, хочу массаж лица"})
    assert stop is None or stop.kind != KIND_RED_FLAG


def test_crisis_stop_is_still_the_crisis_text() -> None:
    stop, _ = screen_goal_body(Mock(pk=4105), {"goal_text": "я думаю о суициде"})
    assert stop is not None
    assert stop.kind == KIND_CRISIS
    assert stop.text == CRISIS_REPLY_TEXT
    assert stop.text != MEDICAL_EMERGENCY_TEXT_V2
