"""Welcome skill tests.

Pins the keyboard contract — channel-agnostic ``[{label, callback}]`` —
plus the match predicate (/start + cb:welcome:*) and the
config-driven button-type fallback ladder.
"""

from __future__ import annotations

from unittest.mock import MagicMock


from apps.skills.base import SkillContext
from apps.skills.welcome.skill import (
    ASK_PROMPT,
    FOOD_PROMPT,
    WATER_PROMPT,
    WELCOME_TEXT,
    WelcomeSkill,
)


def _ctx(text: str) -> SkillContext:
    """Build a SkillContext stub. Welcome skill ignores conversation +
    bot_user — only ``message_text`` drives matches/handle."""
    return SkillContext(
        conversation=MagicMock(),
        bot_user=MagicMock(),
        message_text=text,
    )


class TestMatches:
    def test_start_command_matches(self):
        assert WelcomeSkill().matches(_ctx("/start")) is True

    def test_start_with_whitespace_matches(self):
        assert WelcomeSkill().matches(_ctx("  /start  ")) is True

    def test_welcome_callback_matches(self):
        assert WelcomeSkill().matches(_ctx("cb:welcome:ask")) is True
        assert WelcomeSkill().matches(_ctx("cb:welcome:book")) is True

    def test_other_callback_does_not_match(self):
        assert WelcomeSkill().matches(_ctx("cb:food:to_diary:abc")) is False

    def test_plain_text_does_not_match(self):
        assert WelcomeSkill().matches(_ctx("Привет")) is False

    def test_empty_text_does_not_match(self):
        assert WelcomeSkill().matches(_ctx("")) is False


class TestHandleStart:
    def test_greeting_text(self):
        result = WelcomeSkill().handle(_ctx("/start"))
        assert result.reply_text == WELCOME_TEXT
        assert result.action_type == "welcome_menu"
        assert result.meta["reply_kind"] == "welcome"

    def test_zero_config_ships_wellness_and_faq_only(self, settings):
        """When neither MAX_BOT_WEB_APP nor MAX_MINIAPP_URL is set, the
        salon buttons drop out but the wellness + FAQ row remains."""

        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        # No salon buttons; 4 wellness/FAQ buttons (food, water, anketa, ask).
        assert len(buttons) == 4
        callbacks = [b["callback"] for b in buttons]
        assert callbacks == [
            "cb:welcome:food",
            "cb:welcome:water",
            "cb:anketa:start",
            "cb:welcome:ask",
        ]

    def test_web_app_config_emits_open_app_buttons(self, settings):
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        # 3 salon nav + 4 wellness/FAQ = 7 total.
        assert len(buttons) == 7
        nav = buttons[:3]
        for btn in nav:
            assert btn["web_app"] == "id583_bot"
            assert "callback" in btn  # repurposed as MAX open_app payload
        # Wellness + FAQ row: never carries web_app.
        for btn in buttons[3:]:
            assert "web_app" not in btn
            assert btn["callback"].startswith("cb:")

    def test_miniapp_url_fallback_emits_link_buttons(self, settings):
        """When the bot has no web_app username but a base URL is
        configured, fall back to link buttons for the salon nav. Routes
        are appended cleanly regardless of trailing/leading slashes."""

        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        assert len(buttons) == 7
        urls = [b.get("url") for b in buttons[:3]]
        assert urls == [
            "https://miniapp-dev.example/catalog",
            "https://miniapp-dev.example/visits",
            "https://miniapp-dev.example/profile",
        ]

    def test_web_app_takes_precedence_over_miniapp_url(self, settings):
        """If both are set, the native ``open_app`` UX wins."""
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        assert all("web_app" in b for b in buttons[:3])
        assert not any("url" in b for b in buttons[:3])

    def test_anketa_button_routes_directly_to_anketa_skill(self, settings):
        """The 📊 Анкета button payload is ``cb:anketa:start`` — that lets
        the nutrition_anketa skill match it directly and kick off the
        FSM without an intermediate welcome turn."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]
        anketa = next(b for b in buttons if b["label"].startswith("📊"))
        assert anketa["callback"] == "cb:anketa:start"

    def test_button_columns_one(self, settings):
        settings.MAX_BOT_WEB_APP = "id583_bot"
        result = WelcomeSkill().handle(_ctx("/start"))
        assert result.action_data["button_columns"] == 1


class TestHandleCallback:
    def test_ask_callback_emits_prompt(self):
        result = WelcomeSkill().handle(_ctx("cb:welcome:ask"))
        assert result.reply_text == ASK_PROMPT
        assert result.meta["reply_kind"] == "welcome_ask_prompt"
        # No keyboard — the FAQ skill picks up the user's question next turn.
        assert result.action_data is None

    def test_food_callback_emits_food_prompt(self):
        result = WelcomeSkill().handle(_ctx("cb:welcome:food"))
        assert result.reply_text == FOOD_PROMPT
        assert result.meta["reply_kind"] == "welcome_food_prompt"
        # No keyboard — user sends a photo next turn → food_scanner.
        assert result.action_data is None

    def test_water_callback_emits_water_prompt(self):
        result = WelcomeSkill().handle(_ctx("cb:welcome:water"))
        assert result.reply_text == WATER_PROMPT
        assert result.meta["reply_kind"] == "welcome_water_prompt"
        # No keyboard — user types "стакан" next turn → water skill.
        assert result.action_data is None

    def test_unknown_welcome_callback_falls_back_to_menu(self):
        """Defensive: an unknown ``cb:welcome:*`` (e.g. an open_app
        button payload that came back as a callback for some reason)
        falls back to re-showing the menu rather than 500-ing."""

        result = WelcomeSkill().handle(_ctx("cb:welcome:book"))
        assert result.reply_text == WELCOME_TEXT
        assert result.action_type == "welcome_menu"
