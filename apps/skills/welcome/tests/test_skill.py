"""Welcome skill tests.

Pins the keyboard contract — channel-agnostic ``[{label, callback}]`` —
plus the match predicate (/start + cb:welcome:*) and the
config-driven button-type fallback ladder.
"""

from __future__ import annotations

from unittest.mock import MagicMock


from apps.skills.base import SkillContext
from apps.skills.welcome.skill import ASK_PROMPT, WELCOME_TEXT, WelcomeSkill


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

    def test_zero_config_only_ships_ask_button(self, settings):
        """When neither MAX_BOT_WEB_APP nor MAX_MINIAPP_URL is set, the
        keyboard collapses to a single «❓ Задать вопрос» callback —
        zero-config fallback for tests + early dev."""

        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        assert len(buttons) == 1
        assert buttons[0]["label"].endswith("Задать вопрос")
        assert buttons[0]["callback"] == "cb:welcome:ask"

    def test_web_app_config_emits_open_app_buttons(self, settings):
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        # 3 nav buttons + 1 ask = 4 total.
        assert len(buttons) == 4
        nav = buttons[:3]
        for btn in nav:
            assert btn["web_app"] == "id583_bot"
            assert "callback" in btn  # repurposed as MAX open_app payload
        # Ask button stays a plain callback.
        assert buttons[3]["callback"] == "cb:welcome:ask"
        assert "web_app" not in buttons[3]

    def test_miniapp_url_fallback_emits_link_buttons(self, settings):
        """When the bot has no web_app username but a base URL is
        configured, fall back to link buttons. Routes are appended cleanly
        regardless of trailing/leading slashes."""

        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        assert len(buttons) == 4
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

    def test_unknown_welcome_callback_falls_back_to_menu(self):
        """Defensive: an unknown ``cb:welcome:*`` (e.g. an open_app
        button payload that came back as a callback for some reason)
        falls back to re-showing the menu rather than 500-ing."""

        result = WelcomeSkill().handle(_ctx("cb:welcome:book"))
        assert result.reply_text == WELCOME_TEXT
        assert result.action_type == "welcome_menu"
