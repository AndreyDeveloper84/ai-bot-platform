"""HelpSkill tests (DRF-963).

HelpSkill overrides FAQ for a closed set of phrases. The tests below pin
both halves of that bargain: the override fires for real help requests,
and it stays out of the way of everything else.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from apps.skills.base import SkillContext
from apps.skills.menu.help_skill import HelpSkill
from apps.skills.menu.matching import CALLBACK_MENU_HELP, main_menu_buttons
from apps.skills.menu.replies import HELP_TEXT


def _ctx(text: str, *, intent=None) -> SkillContext:
    return SkillContext(
        conversation=MagicMock(),
        bot_user=MagicMock(),
        message_text=text,
        intent=intent,
    )


class TestMatches:
    @pytest.mark.parametrize(
        "text",
        [
            "помощь",
            "Помощь",
            "Помощь!",
            "  помощь  ",
            "помоги",
            "меню",
            "справка",
            "что ты умеешь",
            "Что ты умеешь?",
            "что умеешь",
            "/help",
            "/menu",
            CALLBACK_MENU_HELP,
        ],
    )
    def test_claims_help_requests(self, text):
        assert HelpSkill().matches(_ctx(text)) is True

    @pytest.mark.parametrize(
        "text",
        [
            # Belongs to booking — a help WORD inside a real request must
            # not be hijacked by the FAQ override.
            "помогите подобрать массаж",
            "помоги записаться на маникюр",
            # Belongs to FAQ — a genuine salon question.
            "какие у вас есть услуги?",
            "сколько стоит массаж?",
            "что входит в спа-программу?",
            # Belongs to welcome.
            "/start",
            "",
            "   ",
        ],
    )
    def test_does_not_claim_anything_else(self, text):
        assert HelpSkill().matches(_ctx(text)) is False

    def test_stands_down_for_orchestrator_driven_turns(self):
        assert HelpSkill().matches(_ctx("помощь", intent=MagicMock())) is False


class TestHandle:
    def test_returns_capability_list_with_menu(self):
        result = HelpSkill().handle(_ctx("помощь"))
        assert result.reply_text == HELP_TEXT
        assert result.action_type == "menu_help"
        assert result.meta["reply_kind"] == "menu_help"
        assert result.action_data["attachments"][0]["payload"]["buttons"] == main_menu_buttons()

    def test_names_the_handoff_escape_hatch(self):
        """The operator doc tells staff customers can type «оператор»;
        the help text must actually say so."""
        assert "оператор" in HELP_TEXT
