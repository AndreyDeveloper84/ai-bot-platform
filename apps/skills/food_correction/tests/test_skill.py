"""FoodCorrectionSkill tests (DRF-822 / Sprint 9 / P5)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from apps.skills.base import SkillContext
from apps.skills.food_correction.skill import FoodCorrectionSkill


def _context(text: str) -> SkillContext:
    return SkillContext(
        conversation=Mock(id="conv-1"),
        bot_user=Mock(),
        message_text=text,
    )


# ─── matches ──────────────────────────────────────────────────────────────


class TestMatches:
    @pytest.mark.parametrize(
        "callback",
        [
            "cb:food:correct:grams:scan-1",
            "cb:food:correct:name:scan-1",
            "cb:food:correct:macros:scan-1",
        ],
    )
    def test_three_correction_callbacks_match(self, callback: str) -> None:
        assert FoodCorrectionSkill().matches(_context(callback))

    def test_unknown_field_does_not_match(self) -> None:
        """Misroute guard — only the 3 known fields are claimed."""
        assert not FoodCorrectionSkill().matches(_context("cb:food:correct:portion:scan-1"))

    def test_food_scanner_callbacks_not_claimed(self) -> None:
        """P5 must NOT claim P1 (food_scanner) callbacks even though
        both start with cb:food:*."""
        for cb in [
            "cb:food:to_diary:scan-1",
            "cb:food:clarify:scan-1",
            "cb:food:reject:scan-1",
        ]:
            assert not FoodCorrectionSkill().matches(_context(cb))

    def test_food_clarify_callbacks_not_claimed(self) -> None:
        """P5 must NOT claim P4 (food_clarify) callbacks."""
        assert not FoodCorrectionSkill().matches(_context("cb:food:diary"))
        assert not FoodCorrectionSkill().matches(_context("cb:food:typo"))

    def test_plain_text_does_not_match(self) -> None:
        assert not FoodCorrectionSkill().matches(_context("250"))


# ─── handle ──────────────────────────────────────────────────────────────


class TestHandle:
    def test_grams_emits_grams_prompt(self) -> None:
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:grams:scan-1"))
        assert "грамм" in result.reply_text.lower()
        assert result.action_type == "food_correction_prompt"
        assert result.action_data == {
            "field": "grams",
            "scan_id": "scan-1",
            # DRF-1454 — the memory-aware prompt flag; False with nothing stored.
            "remembered": False,
        }

    def test_name_emits_name_prompt(self) -> None:
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:name:scan-1"))
        assert "напиши" in result.reply_text.lower() or "что было" in result.reply_text.lower()
        assert result.action_data == {
            "field": "name",
            "scan_id": "scan-1",
            # DRF-1454 — the memory-aware prompt flag; False with nothing stored.
            "remembered": False,
        }

    def test_macros_emits_macros_prompt(self) -> None:
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:macros:scan-1"))
        assert "макрос" in result.reply_text.lower()
        assert result.action_data == {
            "field": "macros",
            "scan_id": "scan-1",
            # DRF-1454 — the memory-aware prompt flag; False with nothing stored.
            "remembered": False,
        }

    def test_scan_id_propagated_to_action_data(self) -> None:
        """The follow-up text input from the user must be tied back to
        this scan_id when the apply path lands in Phase 1."""
        result = FoodCorrectionSkill().handle(_context("cb:food:correct:grams:abc-xyz-789"))
        assert result.action_data is not None
        assert result.action_data["scan_id"] == "abc-xyz-789"

    def test_each_prompt_differs(self) -> None:
        """All three replies must be distinguishable — no copy-paste accident."""
        replies = {
            FoodCorrectionSkill().handle(_context(f"cb:food:correct:{field}:s-1")).reply_text
            for field in ("grams", "name", "macros")
        }
        assert len(replies) == 3


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_food_correction_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "food_correction" in names
        # Both P1 and P5 present; their callbacks are disjoint
        # (to_diary/clarify/reject vs correct:*).
        assert "food_scanner" in names
