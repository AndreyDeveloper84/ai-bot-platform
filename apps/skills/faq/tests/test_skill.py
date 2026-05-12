"""FAQ stub skill tests (DRF-544 / Sprint 6 / I1)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.skills.base import SkillContext
from apps.skills.faq.skill import FAQSkill


class _FakeBotUser:
    def __init__(self):
        self.id = uuid4()


class _FakeConversation:
    def __init__(self):
        self.id = uuid4()


def _ctx(text: str) -> SkillContext:
    return SkillContext(
        conversation=_FakeConversation(),  # type: ignore[arg-type]
        bot_user=_FakeBotUser(),  # type: ignore[arg-type]
        message_text=text,
    )


class TestMatches:
    @pytest.mark.parametrize(
        "text",
        [
            "сколько стоит массаж?",
            "когда вы работаете",
            "Где находитесь?",
            "Почему так дорого",
            "Какие услуги есть",
            "что такое лимфодренаж",
            "Скажите, как записаться",
            "How much is the massage?",
            "When do you open",
            "where is the salon",
            "Why such price",
        ],
    )
    def test_question_signals_match(self, text):
        assert FAQSkill().matches(_ctx(text)) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Привет",
            "оператор",
            "удали мои данные",
            "ok",
        ],
    )
    def test_non_question_does_not_match(self, text):
        assert FAQSkill().matches(_ctx(text)) is False


class TestHandle:
    def test_returns_canned_reply(self):
        with patch("apps.skills.faq.skill.emit"):
            result = FAQSkill().handle(_ctx("сколько стоит массаж?"))
        assert "Спасибо за вопрос" in result.reply_text
        assert result.should_send is True
        assert result.meta["skill"] == "faq"
        assert result.meta["stub"] is True

    def test_emits_skill_dispatched_event(self):
        with patch("apps.skills.faq.skill.emit") as mock_emit:
            FAQSkill().handle(_ctx("когда работаете?"))
        mock_emit.assert_called_once()
        # Event vocabulary constant is the positional arg.
        args, kwargs = mock_emit.call_args
        assert kwargs.get("properties", {}).get("skill") == "faq"
        assert kwargs.get("properties", {}).get("stub") is True


class TestRegistration:
    """FAQ must register BEFORE echo so echo doesn't shadow it."""

    def test_faq_registered_before_echo(self):
        from apps.skills.registry import registered

        skills = registered()
        names = [s.name for s in skills]
        assert "faq" in names
        assert "echo" in names
        assert names.index("faq") < names.index("echo")
