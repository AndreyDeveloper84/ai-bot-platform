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

from apps.skills.welcome.skill import (  # noqa: E402
    S2_CONSENT_TEXT,
    S2_REFUSED_TEXT,
    S2A_DETAILS_TEXT,
    S3_POSITIONING_TEXT,
    S5_FOLLOWUP_TEXT,
    S5_PROMPT_TEXT,
)


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
    def test_start_s2_callback_returns_consent_prompt(self, unwelcomed_bot_user):
        """[▶️ Начать] tap → cb:welcome:start_s2 → S2 privacy consent
        prompt с 3 buttons («Да, продолжим» / «Узнать что хранится» /
        «Не сейчас»). Tau's customer-onboarding-flow.md §5 verbatim."""
        skill = WelcomeSkill()
        skill.handle(_ctx_with_botuser("/start", unwelcomed_bot_user))
        unwelcomed_bot_user.refresh_from_db()

        result = skill.handle(
            _ctx_with_botuser("cb:welcome:start_s2", unwelcomed_bot_user),
        )
        assert result.reply_text == S2_CONSENT_TEXT
        assert result.action_type == "welcome_consent_prompt"
        assert result.meta["reply_kind"] == "welcome_s2_consent_prompt"
        callbacks = [b["callback"] for b in result.action_data["buttons"]]
        assert callbacks == [
            "cb:welcome:consent_yes",
            "cb:welcome:consent_details",
            "cb:welcome:consent_refuse",
        ]

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


# ───────────────────────────────────────────────────────────────────────
# S2 privacy consent (152-ФЗ) — task #85 part 2, 2026-05-26
# ───────────────────────────────────────────────────────────────────────


class TestS2ConsentFlow:
    """S2 / S2a / refused consent flows (Tau customer-onboarding-flow.md
    §5 + §11 State 3)."""

    @pytest.mark.django_db
    def test_consent_details_returns_s2a_expanded(self, unwelcomed_bot_user):
        """«Узнать что хранится» → S2a fold disclosing scope.

        Two buttons: «Понятно, продолжим» (=consent_yes) + «Не сейчас»
        (=consent_refuse). Same outcomes as S2's first/third buttons —
        single source of truth для consent stamping."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_details", unwelcomed_bot_user),
        )
        assert result.reply_text == S2A_DETAILS_TEXT
        assert result.action_type == "welcome_consent_details"
        assert result.meta["reply_kind"] == "welcome_s2a_details"
        callbacks = [b["callback"] for b in result.action_data["buttons"]]
        # «Понятно, продолжим» использует distinct callback (PR 2 / Tau §6
        # S3-skip conditional): consent_yes_via_s2a flag's «user уже видел
        # scope disclosure → skip S3 repositioning».
        assert callbacks == [
            "cb:welcome:consent_yes_via_s2a",
            "cb:welcome:consent_refuse",
        ]

    @pytest.mark.django_db
    def test_consent_yes_stamps_consent_at(self, unwelcomed_bot_user):
        """«Да, продолжим» → BotUser.consent_at установлен в DB + bot
        renders S3 + S5 combined bubble (direct path, S3 shown)."""
        skill = WelcomeSkill()
        assert unwelcomed_bot_user.consent_at is None

        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.consent_at is not None
        assert result.meta["reply_kind"] == "welcome_s5_first_action"
        assert result.meta["s3_shown"] is True
        assert S3_POSITIONING_TEXT in result.reply_text
        assert S5_PROMPT_TEXT in result.reply_text

    @pytest.mark.django_db
    def test_consent_yes_idempotent_does_not_overwrite(self, unwelcomed_bot_user):
        """Double-tap (или S2 → S2a → consent_yes) НЕ overwrites
        original consent_at timestamp. Audit-trail integrity:
        «когда впервые согласилась» = source of truth."""
        from django.utils import timezone

        skill = WelcomeSkill()
        original_consent_at = timezone.now() - __import__("datetime").timedelta(hours=1)
        unwelcomed_bot_user.consent_at = original_consent_at
        unwelcomed_bot_user.save(update_fields=["consent_at"])

        skill.handle(_ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user))
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.consent_at == original_consent_at

    @pytest.mark.django_db
    def test_consent_refuse_returns_goodbye_no_keyboard(self, unwelcomed_bot_user, caplog):
        """«Не сейчас» → State 3 graceful exit. Tau §11: «six words,
        dignity preserved, door open». consent_at остаётся NULL.

        152-ФЗ refusal audit-logged (CR Y3 fix on #776). INFO level —
        normal user flow, not failure.
        """
        import logging as _logging

        skill = WelcomeSkill()
        with caplog.at_level(_logging.INFO, logger="apps.skills.welcome.skill"):
            result = skill.handle(
                _ctx_with_botuser("cb:welcome:consent_refuse", unwelcomed_bot_user),
            )
        assert result.reply_text == S2_REFUSED_TEXT
        assert result.action_data is None
        assert result.meta["reply_kind"] == "welcome_consent_refused"
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.consent_at is None
        assert any("welcome.consent_refused" in r.message for r in caplog.records)

    @pytest.mark.django_db
    def test_consent_yes_save_failure_does_not_block_response(
        self, unwelcomed_bot_user, monkeypatch, caplog
    ):
        """Mirror welcomed_at pattern: DB write fail → log ERROR +
        deliver placeholder. Худший случай: consent re-asked на следующем
        entry в S2; not data-loss since user IS giving consent right now."""
        import logging as _logging

        def _explode(*args, **kwargs):
            raise RuntimeError("DB write fail")

        monkeypatch.setattr(unwelcomed_bot_user, "save", _explode)
        skill = WelcomeSkill()
        with caplog.at_level(_logging.ERROR, logger="apps.skills.welcome.skill"):
            result = skill.handle(
                _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
            )
        # S5 grid still rendered — flow continues despite DB write fail.
        assert S5_PROMPT_TEXT in result.reply_text
        assert any("consent_at_save_failed" in r.message for r in caplog.records)


# ───────────────────────────────────────────────────────────────────────
# S3 positioning + S5 first-action grid — task #85 part 3, 2026-05-26
# ───────────────────────────────────────────────────────────────────────


class TestS3S5Flow:
    """S3 conditional positioning + S5 6-button grid (Tau §6 + §8)."""

    @pytest.mark.django_db
    def test_direct_consent_path_shows_s3(self, unwelcomed_bot_user):
        """Direct S1→S2→S3 path: consent_yes → S3 positioning prepended
        к S5 prompt. Tau §6: SHOW S3 когда positioning ещё не была."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        assert result.meta["s3_shown"] is True
        assert result.reply_text.startswith(S3_POSITIONING_TEXT)
        assert S5_PROMPT_TEXT in result.reply_text
        assert S5_FOLLOWUP_TEXT in result.reply_text

    @pytest.mark.django_db
    def test_s2a_path_skips_s3(self, unwelcomed_bot_user):
        """S2a fold path: consent_yes_via_s2a → S5 only, S3 SKIPPED.
        Tau §6 conditional: S2a уже disclosed scope, repositioning
        would feel repetitive."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes_via_s2a", unwelcomed_bot_user),
        )
        assert result.meta["s3_shown"] is False
        assert S3_POSITIONING_TEXT not in result.reply_text
        assert result.reply_text.startswith(S5_PROMPT_TEXT)
        assert S5_FOLLOWUP_TEXT in result.reply_text

    @pytest.mark.django_db
    def test_s2a_path_also_stamps_consent_at(self, unwelcomed_bot_user):
        """Same idempotent stamping helper для обоих consent paths."""
        skill = WelcomeSkill()
        assert unwelcomed_bot_user.consent_at is None
        skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes_via_s2a", unwelcomed_bot_user),
        )
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.consent_at is not None

    @pytest.mark.django_db
    def test_consent_yes_via_s2a_idempotent_does_not_overwrite(self, unwelcomed_bot_user):
        """Stale-keyboard scenario (S2 direct → later S2a tap): second
        consent helper invocation MUST NOT overwrite original timestamp.
        Mirrors test_consent_yes_idempotent_does_not_overwrite from
        direct path — both callbacks share idempotency guard (CR #789
        follow-up #1)."""
        from datetime import timedelta

        from django.utils import timezone

        skill = WelcomeSkill()
        original_consent_at = timezone.now() - timedelta(hours=1)
        unwelcomed_bot_user.consent_at = original_consent_at
        unwelcomed_bot_user.save(update_fields=["consent_at"])

        skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes_via_s2a", unwelcomed_bot_user),
        )
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.consent_at == original_consent_at

    @pytest.mark.django_db
    def test_s5_grid_zero_config_ships_anketa_only(self, unwelcomed_bot_user, settings):
        """Zero-config (no Mini App): только anketa ship'ится — это
        единственная кнопка которая работает без Mini App config.
        «Просто посмотреть» drops (Dashboard = Mini App only)."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        buttons = result.action_data["buttons"]
        callbacks = [b.get("callback") for b in buttons]
        assert callbacks == ["cb:anketa:start"]

    @pytest.mark.django_db
    def test_s5_grid_web_app_emits_6_open_app_buttons(self, unwelcomed_bot_user, settings):
        """Mini App configured: 6 кнопок — 4 primary actions
        (open_food_scan / open_water_add_250 / open_goal_select /
        open_catalog) + anketa (bot skill) + open_home («Просто
        посмотреть»). Tau §8 routing table."""
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        buttons = result.action_data["buttons"]
        assert len(buttons) == 6
        callbacks = [b["callback"] for b in buttons]
        assert callbacks == [
            "open_food_scan",
            "open_water_add_250",
            "open_goal_select",
            "open_catalog",
            "cb:anketa:start",
            "open_home",
        ]
        # Mini App buttons carry web_app payload; anketa stays bot-only.
        for btn in buttons:
            if btn["callback"].startswith("open_"):
                assert btn["web_app"] == "id583_bot"
            else:
                assert "web_app" not in btn

    @pytest.mark.django_db
    def test_s5_grid_uses_2_column_layout(self, unwelcomed_bot_user, settings):
        """Tau §8 Variant A = Grid 2×2 + anketa pair → button_columns=2."""
        settings.MAX_BOT_WEB_APP = "id583_bot"
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        assert result.action_data["button_columns"] == 2

    @pytest.mark.django_db
    def test_s5_grid_miniapp_url_fallback_emits_link_buttons(self, unwelcomed_bot_user, settings):
        """No MAX_BOT_WEB_APP but MAX_MINIAPP_URL set → link buttons
        for the 4 primary actions + «Просто посмотреть» exit. Anketa
        callback unaffected."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        buttons = result.action_data["buttons"]
        # 4 primary URL + anketa callback + просто посмотреть URL = 6
        assert len(buttons) == 6
        urls = [b.get("url") for b in buttons if "url" in b]
        assert urls == [
            "https://miniapp-dev.example/food_scan",
            "https://miniapp-dev.example/water_add_250",
            "https://miniapp-dev.example/goal_select",
            "https://miniapp-dev.example/catalog",
            "https://miniapp-dev.example/home",
        ]
