"""``_reply_kind`` label contract (DRF-963, ось `tests` finding F2).

This label is the pilot's headline conversational metric — «how often does
the bot still miss?» is answered by counting ``menu_fallback`` against the
rest on ``channels.max.outbound.sent``. DRF-963 changed how it is derived
(skill ``meta`` first, positional guess second), which widens the value set
for EVERY surface, not just menu. Both halves are pinned here: the new
preference, and the legacy branches that still answer when a skill sets no
meta at all.
"""

from __future__ import annotations

import pytest

from apps.channels.max.handler import _WELCOME_TEXT, _reply_kind
from apps.channels.max.parser import CanonicalEvent
from apps.skills.base import SkillResult


def _event(*, text: str = "", attachments: list | None = None) -> CanonicalEvent:
    return CanonicalEvent(
        channel="max",
        channel_user_id="u1",
        chat_id="c1",
        channel_message_id="m1",
        text=text,
        attachments=attachments or [],
        raw={},
    )


class TestSkillMetaWins:
    @pytest.mark.parametrize(
        "kind",
        ["menu_fallback", "menu_help", "welcome", "food_scanner_card", "anketa_goal"],
    )
    def test_meta_is_preferred_over_the_positional_guess(self, kind):
        result = SkillResult(reply_text="…", meta={"reply_kind": kind})
        assert _reply_kind(_event(text="что-то"), result, "…") == kind

    def test_attachment_turn_reports_the_skill_not_no_echo(self):
        """The documented widening: a photo turn used to report «no_echo»."""
        result = SkillResult(reply_text="карточка", meta={"reply_kind": "food_scanner_card"})
        event = _event(text="", attachments=[{"type": "image"}])
        assert _reply_kind(event, result, "карточка") == "food_scanner_card"


class TestLegacyBranchesStillAnswer:
    """Reached when the responding skill sets no meta, or the registry is
    empty and the handler fell back to ``_echo_text``."""

    def test_welcome_text_without_meta(self):
        result = SkillResult(reply_text=_WELCOME_TEXT)
        assert _reply_kind(_event(text="/start"), result, _WELCOME_TEXT) == "welcome"

    def test_non_empty_text_without_meta(self):
        assert _reply_kind(_event(text="привет"), None, "привет") == "echo"

    def test_attachment_only_without_meta(self):
        event = _event(text="", attachments=[{"type": "image"}])
        assert _reply_kind(event, None, "(нечем эхом) 🙂") == "no_echo"

    def test_empty_without_meta(self):
        assert _reply_kind(_event(text=""), None, "?") == "empty_prompt"

    def test_empty_meta_dict_falls_through(self):
        assert _reply_kind(_event(text="привет"), SkillResult(reply_text="x"), "x") == "echo"
