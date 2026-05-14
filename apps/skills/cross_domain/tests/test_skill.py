"""CrossDomainSkill tests (DRF-823 / Sprint 9 / P6)."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import NutritionUnavailableError
from apps.skills.base import SkillContext
from apps.skills.cross_domain.skill import (
    CONVERT_ACK,
    DISMISS_ACK,
    QUIET_ACK,
    CrossDomainSkill,
)


def _context(text: str) -> SkillContext:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "12345"
    return SkillContext(
        conversation=Mock(id="conv-1"),
        bot_user=bot_user,
        message_text=text,
    )


# ─── matches ──────────────────────────────────────────────────────────────


class TestMatches:
    @pytest.mark.parametrize(
        "callback",
        [
            "cb:cross:seen:shown-1",
            "cb:cross:dismiss:shown-2",
            "cb:cross:convert:shown-3",
        ],
    )
    def test_three_actions_match(self, callback: str) -> None:
        assert CrossDomainSkill().matches(_context(callback))

    def test_unknown_action_does_not_match(self) -> None:
        assert not CrossDomainSkill().matches(_context("cb:cross:click:shown-1"))

    def test_missing_shown_id_does_not_match(self) -> None:
        assert not CrossDomainSkill().matches(_context("cb:cross:seen"))

    def test_other_domain_callbacks_not_claimed(self) -> None:
        for cb in [
            "cb:food:to_diary:scan-1",
            "cb:anketa:choice:gender:female",
            "cb:request_contact",
        ]:
            assert not CrossDomainSkill().matches(_context(cb))


# ─── seen (silent telemetry) ──────────────────────────────────────────────


class TestSeen:
    def test_seen_emits_silent_skill_result(self) -> None:
        client = Mock()

        async def _seen(**kwargs):
            return True

        client.post_cross_domain_seen = _seen
        with patch("apps.skills.cross_domain.skill.get_nutrition_client", return_value=client):
            result = CrossDomainSkill().handle(_context("cb:cross:seen:shown-1"))

        assert result.reply_text == ""
        assert result.should_send is False

    def test_seen_swallows_ayla_errors(self) -> None:
        """Telemetry must never raise to user-visible level."""
        client = Mock()

        async def _seen(**kwargs):
            raise NutritionUnavailableError("down")

        client.post_cross_domain_seen = _seen
        with patch("apps.skills.cross_domain.skill.get_nutrition_client", return_value=client):
            # Must not raise.
            result = CrossDomainSkill().handle(_context("cb:cross:seen:shown-1"))
        assert result.should_send is False


# ─── dismiss ──────────────────────────────────────────────────────────────


class TestDismiss:
    def test_dismiss_posts_and_acks(self) -> None:
        client = Mock()
        captured: list[dict] = []

        async def _dismiss(**kwargs):
            captured.append(kwargs)
            return True

        client.post_cross_domain_dismiss = _dismiss
        with patch("apps.skills.cross_domain.skill.get_nutrition_client", return_value=client):
            result = CrossDomainSkill().handle(_context("cb:cross:dismiss:shown-9"))

        assert result.reply_text == DISMISS_ACK
        assert captured[0]["shown_id"] == "shown-9"
        assert captured[0]["external_user_id"] == "bot:max:12345"

    def test_dismiss_fallback_when_ayla_down(self) -> None:
        client = Mock()

        async def _dismiss(**kwargs):
            raise NutritionUnavailableError("circuit_open")

        client.post_cross_domain_dismiss = _dismiss
        with patch("apps.skills.cross_domain.skill.get_nutrition_client", return_value=client):
            result = CrossDomainSkill().handle(_context("cb:cross:dismiss:shown-9"))

        # Soft ack — user shouldn't see a stack trace OR feel ignored.
        assert result.reply_text == QUIET_ACK
        assert "ошибка" not in result.reply_text.lower()


# ─── convert (Sprint 9 cut: ack only) ─────────────────────────────────────


class TestConvert:
    def test_convert_does_not_call_ayla(self) -> None:
        """Sprint 9 cut: no appointment_id without booking skill, so
        we don't POST convert. Telemetry stays accurate."""
        client = Mock()
        with patch("apps.skills.cross_domain.skill.get_nutrition_client", return_value=client):
            result = CrossDomainSkill().handle(_context("cb:cross:convert:shown-1"))

        # Assert convert was NOT called on the client.
        client.post_cross_domain_convert.assert_not_called() if hasattr(
            client, "post_cross_domain_convert"
        ) else None

        assert result.reply_text == CONVERT_ACK
        assert result.action_type == "cross_domain_convert_pending"
        assert result.action_data == {"shown_id": "shown-1"}


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_cross_domain_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "cross_domain" in names
