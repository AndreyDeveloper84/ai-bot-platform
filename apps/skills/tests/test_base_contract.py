"""SkillContext / SkillResult contract tests (DRF-559 / Sprint 7 / O1).

Verifies the post-revision contract extension:

* ``SkillContext.intent`` defaults to ``None`` (backward-compat).
* ``SkillResult.should_handoff/handoff_reason/tool_calls_made/confidence``
  default correctly; existing Sprint 3 callers compile unchanged.

These tests intentionally avoid Django ORM — the context/result types
hold only plain Python data. Conversation/BotUser are typed as forward
refs.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import Mock

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.llm.protocol import ToolCall
from apps.orchestrator.intent_router import IntentDecision
from apps.skills.base import SkillContext, SkillResult


def _fake_conversation() -> Conversation:
    return cast(Conversation, Mock(spec=Conversation))


def _fake_bot_user() -> BotUser:
    return cast(BotUser, Mock(spec=BotUser))


class TestSkillContextDefaults:
    def test_intent_defaults_to_none(self) -> None:
        ctx = SkillContext(
            conversation=_fake_conversation(),
            bot_user=_fake_bot_user(),
            message_text="hi",
        )
        assert ctx.intent is None
        assert ctx.has_attachments is False
        assert ctx.trace_id == ""

    def test_intent_can_be_set(self) -> None:
        decision = IntentDecision(
            intent="faq",
            skill="faq",
            confidence=0.92,
            risk_level="low",
        )
        ctx = SkillContext(
            conversation=_fake_conversation(),
            bot_user=_fake_bot_user(),
            message_text="часы работы?",
            intent=decision,
        )
        assert ctx.intent is decision
        assert ctx.intent.intent == "faq"

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        ctx = SkillContext(
            conversation=_fake_conversation(),
            bot_user=_fake_bot_user(),
            message_text="x",
        )
        try:
            ctx.message_text = "tampered"  # type: ignore[misc]
        except FrozenInstanceError:
            return
        raise AssertionError("SkillContext should be frozen")


class TestSkillResultDefaults:
    """Sprint 3 callers MUST continue compiling without setting new fields."""

    def test_minimal_construction_back_compat(self) -> None:
        # Sprint 3 echo skill style — only reply_text + action_type.
        result = SkillResult(reply_text="hi", action_type="echo")
        assert result.should_handoff is False
        assert result.handoff_reason == ""
        assert result.tool_calls_made == []
        assert result.confidence is None
        # Pre-existing fields unchanged.
        assert result.should_send is True
        assert result.should_close_conversation is False
        assert result.new_state is None

    def test_handoff_request(self) -> None:
        result = SkillResult(
            reply_text="переключаю на менеджера",
            should_handoff=True,
            handoff_reason="faq_low_confidence",
            confidence=0.3,
        )
        assert result.should_handoff is True
        assert result.handoff_reason == "faq_low_confidence"
        assert result.confidence == 0.3

    def test_tool_calls_made_isolated_per_instance(self) -> None:
        a = SkillResult()
        b = SkillResult()
        a.tool_calls_made.append(ToolCall(id="x", name="search_kb"))
        assert b.tool_calls_made == []  # default_factory isolation

    def test_tool_calls_carry_arguments(self) -> None:
        tc = ToolCall(id="c1", name="search_kb", arguments={"query": "цена"})
        result = SkillResult(tool_calls_made=[tc])
        assert result.tool_calls_made[0].arguments == {"query": "цена"}
