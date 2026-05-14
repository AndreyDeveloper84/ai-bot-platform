"""WaterSkill match + handle tests (DRF-819 / Sprint 9 / P2).

Mocks the Ayla client at module boundary — keeps tests offline. The
parser already has its own thorough coverage in ``test_parser.py``.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

from apps.integrations.ayla import (
    NutritionUnavailableError,
    WaterEntryResponse,
)
from apps.skills.base import SkillContext
from apps.skills.water.skill import WaterSkill


def _context(text: str, channel: str = "max", channel_user_id: str = "12345") -> SkillContext:
    bot_user = Mock()
    bot_user.channel = channel
    bot_user.channel_user_id = channel_user_id
    return SkillContext(
        conversation=Mock(),
        bot_user=bot_user,
        message_text=text,
    )


def _ayla_response(ml: int = 250, water_ml: int = 250) -> WaterEntryResponse:
    return WaterEntryResponse(
        entry_id="e-1",
        ml=ml,
        water_ml=water_ml,
        kcal=0,
        milestone_text=None,
        today_total_ml=1500,
        today_norm_ml=2000,
        alcohol_recovery_hint=False,
        raw={},
    )


# ─── matches ──────────────────────────────────────────────────────────────


class TestMatches:
    def test_short_beverage_matches(self) -> None:
        assert WaterSkill().matches(_context("стакан воды"))

    def test_long_text_does_not_match(self) -> None:
        """30-char cap: long messages are questions, not log attempts."""
        long_text = "Хочу выпить чашку кофе с молоком и сахаром"
        assert not WaterSkill().matches(_context(long_text))

    def test_non_beverage_does_not_match(self) -> None:
        assert not WaterSkill().matches(_context("когда вы работаете?"))

    def test_empty_does_not_match(self) -> None:
        assert not WaterSkill().matches(_context(""))


# ─── handle: happy path ───────────────────────────────────────────────────


class TestHandleHappyPath:
    def test_water_glass_logs_and_replies(self) -> None:
        client = Mock()

        async def _add_water(**kwargs):
            return _ayla_response(ml=250, water_ml=250)

        client.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=client):
            result = WaterSkill().handle(_context("стакан воды"))

        assert "Записала 250 мл" in result.reply_text
        assert "Сегодня: 1500 из 2000" in result.reply_text
        assert result.action_type == "water_logged"
        assert result.action_data is not None
        assert result.action_data["slug"] == "voda"
        assert result.action_data["ml"] == 250

    def test_coffee_shows_water_coefficient(self) -> None:
        """coffee water_ml < ml — reply explicitly mentions the diff."""
        client = Mock()

        async def _add_water(**kwargs):
            # Ayla applies water_coefficient=0.95 server-side.
            return _ayla_response(ml=250, water_ml=237)

        client.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=client):
            result = WaterSkill().handle(_context("чашка кофе"))

        assert "237 мл в счёт воды" in result.reply_text

    def test_alcohol_hint_appended(self) -> None:
        client = Mock()

        async def _add_water(**kwargs):
            # All response fields are frozen — build from scratch.
            return WaterEntryResponse(
                entry_id="e-2",
                ml=150,
                water_ml=0,
                kcal=120,
                milestone_text=None,
                today_total_ml=1500,
                today_norm_ml=2000,
                alcohol_recovery_hint=True,
                raw={},
            )

        client.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=client):
            result = WaterSkill().handle(_context("бокал вина"))

        assert "стакан воды" in result.reply_text


# ─── error paths ──────────────────────────────────────────────────────────


class TestErrorPaths:
    def test_ayla_unavailable_returns_graceful_fallback(self) -> None:
        client = Mock()

        async def _add_water(**kwargs):
            raise NutritionUnavailableError("circuit_open")

        client.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=client):
            result = WaterSkill().handle(_context("стакан воды"))

        assert "попробуй через минуту" in result.reply_text.lower()
        # No internal codes leak.
        assert "circuit" not in result.reply_text.lower()
        assert "error" not in result.reply_text.lower()
        assert result.action_type == ""  # no UI hint

    def test_external_user_id_uses_bot_user_channel_pair(self) -> None:
        """Verify the I2 mapping is wired correctly — MAX user 12345 must
        come out as ``bot:max:12345``, not the legacy ``bot:12345``."""
        client = Mock()
        captured = {}

        async def _add_water(**kwargs):
            captured.update(kwargs)
            return _ayla_response()

        client.add_water = _add_water
        with patch("apps.skills.water.skill.get_nutrition_client", return_value=client):
            WaterSkill().handle(_context("стакан воды", "max", "12345"))

        assert captured["external_user_id"] == "bot:max:12345"


# ─── registration ────────────────────────────────────────────────────────


class TestRegistration:
    def test_water_registered_before_food_clarify(self) -> None:
        """Water fast-lane must precede food_clarify so "стакан воды"
        lands an Ayla log instead of the diary-or-typo card."""
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "water" in names
        assert "food_clarify" in names
        assert names.index("water") < names.index("food_clarify"), (
            f"water must precede food_clarify; got order {names}"
        )
