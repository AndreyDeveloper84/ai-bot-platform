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
        # No salon buttons; 4 wellness/FAQ + 1 S1 «Начать» = 5.
        assert len(buttons) == 5
        callbacks = [b["callback"] for b in buttons]
        assert callbacks == [
            "cb:welcome:food",
            "cb:welcome:water",
            "cb:anketa:start",
            "cb:welcome:ask",
            "cb:welcome:start_s2",
        ]

    def test_web_app_config_emits_open_app_buttons(self, settings):
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        # 3 salon nav + 4 wellness/FAQ + 1 S1 «Начать» = 8 total.
        assert len(buttons) == 8
        nav = buttons[:3]
        # Flat slug payloads — MAX rejects open_app payloads with `=`
        # (HTTP 400 proto.payload). Mini App's parseStartRoute resolves
        # these by direct lookup.
        expected_payloads = ["open_catalog", "open_visits", "open_profile"]
        for btn, expected in zip(nav, expected_payloads):
            assert btn["web_app"] == "id583_bot"
            assert btn["callback"] == expected
        # Wellness + FAQ + S1 row: never carries web_app.
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
        # 3 salon nav + 4 wellness/FAQ + 1 S1 «Начать» = 8 total.
        assert len(buttons) == 8
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


# ───────────────────────────────────────────────────────────────────────
# S1 onboarding auto-trigger (task #85, 2026-05-26)
# ───────────────────────────────────────────────────────────────────────


import pytest  # noqa: E402

from apps.skills.welcome.skill import START_S2_PLACEHOLDER_TEXT  # noqa: E402


@pytest.fixture
def unwelcomed_bot_user(db):
    """Real BotUser с ``welcomed_at=None`` — для testing S1 auto-trigger
    path. Existing tests используют MagicMock (truthy attr) и поэтому
    не попадают в auto-trigger branch."""
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="welcome-s1-test", name="S1 Test")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="s1-user-1",
        chat_id="max-s1-1",
        welcomed_at=None,
    )


@pytest.fixture
def welcomed_bot_user(db):
    """Real BotUser с ``welcomed_at`` set — auto-trigger ВЫКЛЮЧЕН."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="welcome-returning-test", name="Returning")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="s1-user-2",
        chat_id="max-s1-2",
        welcomed_at=timezone.now() - timedelta(days=1),
    )


def _ctx_with_botuser(text: str, bot_user) -> SkillContext:
    """Build SkillContext с REAL bot_user (не MagicMock)."""
    return SkillContext(
        conversation=MagicMock(),
        bot_user=bot_user,
        message_text=text,
    )


class TestS1AutoTrigger:
    """S1 spec (tech-lead inline 2026-05-26):
    - First message от unwelcomed bot_user → welcome triggered
    - BotUser.welcomed_at stamped after first trigger
    - Subsequent messages — welcome НЕ re-fires
    """

    @pytest.mark.django_db
    def test_any_text_matches_when_welcomed_at_is_null(self, unwelcomed_bot_user):
        """Любой текст (включая «Привет», «болит спина», noise) → welcome
        matches when bot_user.welcomed_at IS NULL."""
        skill = WelcomeSkill()
        assert skill.matches(_ctx_with_botuser("Привет", unwelcomed_bot_user)) is True
        assert skill.matches(_ctx_with_botuser("болит спина", unwelcomed_bot_user)) is True
        assert skill.matches(_ctx_with_botuser("12345", unwelcomed_bot_user)) is True

    @pytest.mark.django_db
    def test_any_text_does_not_match_when_welcomed_at_set(self, welcomed_bot_user):
        """Returning user — welcome НЕ activates, dispatcher идёт дальше."""
        skill = WelcomeSkill()
        assert skill.matches(_ctx_with_botuser("Привет", welcomed_bot_user)) is False
        assert skill.matches(_ctx_with_botuser("болит спина", welcomed_bot_user)) is False

    @pytest.mark.django_db
    def test_handle_stamps_welcomed_at(self, unwelcomed_bot_user):
        """First welcome delivery → welcomed_at установлен в DB."""
        skill = WelcomeSkill()
        assert unwelcomed_bot_user.welcomed_at is None

        result = skill.handle(_ctx_with_botuser("Привет", unwelcomed_bot_user))
        assert result.reply_text == WELCOME_TEXT
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.welcomed_at is not None

    @pytest.mark.django_db
    def test_second_message_does_not_re_trigger(self, unwelcomed_bot_user):
        """End-to-end idempotency: first message → welcome. Refresh user
        → second message → matches() returns False (auto-trigger off).
        /start всё ещё matches — отдельный branch."""
        skill = WelcomeSkill()
        skill.handle(_ctx_with_botuser("Привет", unwelcomed_bot_user))
        unwelcomed_bot_user.refresh_from_db()

        assert skill.matches(_ctx_with_botuser("ещё вопрос", unwelcomed_bot_user)) is False

    @pytest.mark.django_db
    def test_start_still_matches_after_welcomed(self, welcomed_bot_user):
        """``/start`` — explicit reset gesture, должен всегда matches."""
        skill = WelcomeSkill()
        assert skill.matches(_ctx_with_botuser("/start", welcomed_bot_user)) is True

    @pytest.mark.django_db
    def test_start_s2_callback_returns_placeholder(self, unwelcomed_bot_user):
        """[▶️ Начать] tap → cb:welcome:start_s2 → placeholder text +
        re-show menu. Final S2 (privacy consent) lands в Tau's PR."""
        skill = WelcomeSkill()
        skill.handle(_ctx_with_botuser("/start", unwelcomed_bot_user))
        unwelcomed_bot_user.refresh_from_db()

        result = skill.handle(
            _ctx_with_botuser("cb:welcome:start_s2", unwelcomed_bot_user),
        )
        assert result.reply_text == START_S2_PLACEHOLDER_TEXT
        assert result.meta["reply_kind"] == "welcome_start_s2_placeholder"

    @pytest.mark.django_db
    def test_save_failure_does_not_block_welcome(
        self,
        unwelcomed_bot_user,
        monkeypatch,
        caplog,
    ):
        """Если bot_user.save() throws (DB connection drop, etc.), welcome
        всё равно доставляется. Худший случай: welcome re-fires на
        следующем msg. ERROR log для systematic detection."""
        import logging as _logging

        def _explode(*args, **kwargs):
            raise RuntimeError("DB write fail")

        monkeypatch.setattr(unwelcomed_bot_user, "save", _explode)
        skill = WelcomeSkill()
        with caplog.at_level(_logging.ERROR, logger="apps.skills.welcome.skill"):
            result = skill.handle(_ctx_with_botuser("Привет", unwelcomed_bot_user))

        assert result.reply_text == WELCOME_TEXT
        assert any("welcomed_at_save_failed" in r.message for r in caplog.records)


class TestS1Buttons:
    """«Начать» button добавлена в keyboard (task #85)."""

    def test_start_button_added(self):
        """Welcome menu теперь содержит [▶️ Начать] callback."""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        labels = [b["label"] for b in buttons]
        callbacks = [b.get("callback") for b in buttons]
        assert "▶️ Начать" in labels
        assert "cb:welcome:start_s2" in callbacks
