"""Unit tests for the MAX safety gate (#1053, S1-B).

The gate wraps :func:`apps.orchestrator.safety.pre_check.pre_check` for the live
MAX handlers: HANDOFF/BLOCK short-circuit with a canned reply; CLARIFY/ALLOW
proceed.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.safety.gate import (
    BLOCK_REPLY_TEXT,
    CRISIS_REPLY_TEXT,
    evaluate_inbound,
)


class TestCrisisHandoff:
    @pytest.mark.parametrize(
        "text",
        [
            "я думаю о суициде",
            "хочу убить себя",
            "мне так плохо, что умираю",
        ],
    )
    def test_red_flag_returns_crisis_reply(self, text):
        outcome = evaluate_inbound(text)
        assert outcome.allowed is False
        assert outcome.verdict == "handoff"
        assert outcome.reply_text == CRISIS_REPLY_TEXT
        # Observability carries the match provenance; never user-facing.
        assert outcome.matched_patterns


# #1081 — expanded self-harm / suicidal-ideation coverage. Coverage-first: a
# MISS here is a safety failure; a false-positive is only a UX cost.
_RU_CRISIS = [
    "хочу умереть",
    "не хочу жить",
    "не хочу больше жить",
    "жить больше не хочу",
    "покончу с собой",
    "покончить с собой",
    "убью себя",
    "убьюсь",
    "свести счёты с жизнью",
    "нет смысла жить",
    "не вижу смысла жить",
    "лучше бы я умер",
    "лучше бы я умерла",
    "причиню себе вред",
    "навредить себе",
    "режу себя",
    "резать себя",
    "вскрою вены",
    "повешусь",
    "повеситься",
    "наглотаюсь таблеток",
    "хочу исчезнуть навсегда",
    "хочу сдохнуть",
]
_EN_CRISIS = [
    "I want to die",
    "wanna die",
    "kill myself",
    "killing myself",
    "end my life",
    "end it all",
    "suicide",
    "suicidal",
    "self-harm",
    "self harm",
    "hurt myself",
    "cut myself",
    "no reason to live",
    "don't want to live",
    "better off dead",
]


class TestExpandedCrisisCoverage:
    @pytest.mark.parametrize("text", _RU_CRISIS + _EN_CRISIS)
    def test_new_phrase_routes_to_crisis(self, text):
        outcome = evaluate_inbound(text)
        assert outcome.allowed is False, f"MISS (safety fail): {text!r} not caught"
        assert outcome.verdict == "handoff"
        assert outcome.reply_text == CRISIS_REPLY_TEXT

    @pytest.mark.parametrize(
        "text",
        [
            "хочу записаться на массаж",
            "маникюр в Пензе завтра",
            "сколько стоит окрашивание",
            "убить время за чашкой кофе",
            "порезала палец, есть пластырь?",
            "хочу подстричься коротко",
            "запишите меня к мастеру",
        ],
    )
    def test_happy_beauty_phrase_not_crisis(self, text):
        # Regression: ordinary beauty traffic must not trip the crisis net.
        outcome = evaluate_inbound(text)
        assert outcome.allowed is True
        assert outcome.verdict != "handoff"


class TestBlock:
    @pytest.mark.parametrize(
        "text",
        [
            "посоветуйте ибупрофен от боли",
            "хочу подать в суд на салон",
            "поставьте диагноз пожалуйста",
        ],
    )
    def test_block_phrase_returns_block_reply(self, text):
        outcome = evaluate_inbound(text)
        assert outcome.allowed is False
        assert outcome.verdict == "block"
        assert outcome.reply_text == BLOCK_REPLY_TEXT


class TestProceeds:
    def test_happy_message_allowed(self):
        outcome = evaluate_inbound("хочу массаж завтра в Пензе")
        assert outcome.allowed is True
        assert outcome.verdict == "allow"
        assert outcome.reply_text == ""

    def test_clarify_proceeds_not_short_circuited(self):
        # «почему болит» → CLARIFY; on a beauty marketplace we deliberately let it
        # proceed to normal handling rather than interrupt with a canned reply.
        outcome = evaluate_inbound("почему болит спина после массажа")
        assert outcome.allowed is True
        assert outcome.verdict == "clarify"

    def test_empty_text_allowed(self):
        outcome = evaluate_inbound("")
        assert outcome.allowed is True
        assert outcome.verdict == "allow"

    def test_callback_payload_allowed(self):
        # Button taps (folded into text) must not trip the gate.
        outcome = evaluate_inbound("cb:welcome:consent_yes")
        assert outcome.allowed is True
