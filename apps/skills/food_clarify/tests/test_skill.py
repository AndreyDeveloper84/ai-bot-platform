"""FoodClarifySkill match + handle tests (DRF-821 / Sprint 9 / P4)."""

from __future__ import annotations

from unittest.mock import Mock

from apps.skills.base import SkillContext
from apps.skills.food_clarify.skill import (
    CARD_TEXT,
    DIARY_PROMPT,
    TYPO_ACK,
    FoodClarifySkill,
)


def _context(text: str) -> SkillContext:
    """Build a minimal context — skill doesn't read conversation/bot_user."""
    return SkillContext(
        conversation=Mock(),
        bot_user=Mock(),
        message_text=text,
    )


# ─── matches ──────────────────────────────────────────────────────────────


class TestMatches:
    def test_food_text_matches(self) -> None:
        skill = FoodClarifySkill()
        assert skill.matches(_context("Борщ 300г"))

    def test_callback_diary_matches(self) -> None:
        skill = FoodClarifySkill()
        assert skill.matches(_context("cb:food:diary"))

    def test_callback_typo_matches(self) -> None:
        skill = FoodClarifySkill()
        assert skill.matches(_context("cb:food:typo"))

    def test_unrelated_text_does_not_match(self) -> None:
        skill = FoodClarifySkill()
        assert not skill.matches(_context("Хочу записаться на массаж"))

    def test_other_food_callback_does_not_match(self) -> None:
        """Only diary + typo land here; food_scanner callbacks belong to P1."""
        skill = FoodClarifySkill()
        assert not skill.matches(_context("cb:food:to_diary:abc123"))


# ─── handle ──────────────────────────────────────────────────────────────


class TestHandle:
    def test_food_text_emits_card_with_two_buttons(self) -> None:
        skill = FoodClarifySkill()
        result = skill.handle(_context("Борщ 300г"))

        assert result.reply_text == CARD_TEXT
        assert result.action_type == "food_clarify_card"
        assert result.action_data is not None
        buttons = result.action_data["buttons"]
        callbacks = [b["callback"] for b in buttons]
        assert callbacks == ["cb:food:diary", "cb:food:typo"]

    def test_diary_callback_emits_photo_prompt(self) -> None:
        skill = FoodClarifySkill()
        result = skill.handle(_context("cb:food:diary"))

        assert result.reply_text == DIARY_PROMPT
        assert result.action_type == ""  # no card on the redirect
        # Soft hand-off via reply text — no skill state needed.

    def test_typo_callback_silent_ack(self) -> None:
        skill = FoodClarifySkill()
        result = skill.handle(_context("cb:food:typo"))

        assert result.reply_text == TYPO_ACK
        assert result.action_type == ""


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_registered_before_faq(self) -> None:
        """DRF-358 fix only works if this skill runs BEFORE faq.

        Apps.ready() controls order via import sequence; this is the
        sentinel test catching a misorder.
        """
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "food_clarify" in names
        assert "faq" in names
        assert names.index("food_clarify") < names.index("faq"), (
            f"food_clarify must precede faq in registry; got order {names}"
        )
