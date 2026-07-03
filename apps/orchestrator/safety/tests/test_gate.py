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
