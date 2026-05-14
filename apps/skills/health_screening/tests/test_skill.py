"""HealthScreeningSkill match + handle tests (DRF-824 / Sprint 9 / P7)."""

from __future__ import annotations

from unittest.mock import Mock

from apps.skills.base import SkillContext
from apps.skills.health_screening.skill import (
    RED_FLAG_REPLY,
    SOFT_PAIN_REPLY,
    HealthScreeningSkill,
)


def _context(text: str) -> SkillContext:
    return SkillContext(
        conversation=Mock(id="conv-1"),
        bot_user=Mock(),
        message_text=text,
    )


class TestMatches:
    def test_pain_mention_matches(self) -> None:
        assert HealthScreeningSkill().matches(_context("Болит спина"))

    def test_red_flag_matches(self) -> None:
        assert HealthScreeningSkill().matches(_context("Онемение в руке"))

    def test_non_pain_does_not_match(self) -> None:
        assert not HealthScreeningSkill().matches(_context("Запишите меня"))

    def test_empty_does_not_match(self) -> None:
        assert not HealthScreeningSkill().matches(_context(""))


class TestHandle:
    def test_soft_pain_asks_diagnostic_questions(self) -> None:
        """The reply must ASK a question, not recommend a service.

        DRF-358 T04 incident: bot was answering 'вот наши услуги' to
        pain mentions. The fix: 1-2 clarifying questions first.
        """
        result = HealthScreeningSkill().handle(_context("Болит спина"))
        assert result.reply_text == SOFT_PAIN_REPLY
        # Sentinel: must contain question marks (diagnostic-first).
        assert "?" in result.reply_text
        # Must NOT recommend a service.
        assert "услуг" not in result.reply_text.lower()
        assert "запис" not in result.reply_text.lower()

    def test_red_flag_redirects_to_doctor(self) -> None:
        result = HealthScreeningSkill().handle(_context("Онемение в ноге"))
        assert result.reply_text == RED_FLAG_REPLY
        assert "врач" in result.reply_text.lower()

    def test_radiation_pattern_redirects(self) -> None:
        """DRF-358 voice-example case: «отдаёт в руку» → к неврологу."""
        result = HealthScreeningSkill().handle(_context("Болит шея, отдаёт в руку"))
        assert "врач" in result.reply_text.lower()


class TestRegistration:
    def test_health_screening_registered_before_faq(self) -> None:
        """Pain detection must run BEFORE faq so a "болит спина" never
        reaches the LLM-driven service answer.
        """
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "health_screening" in names
        assert "faq" in names
        assert names.index("health_screening") < names.index("faq"), (
            f"health_screening must precede faq; got {names}"
        )

    def test_health_screening_registered_before_water(self) -> None:
        """Pain wins over water — 'болит, выпью кофе' is a pain message
        the LLM should clarify, not a kofe log.
        """
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert names.index("health_screening") < names.index("water"), (
            f"health_screening must precede water; got {names}"
        )
