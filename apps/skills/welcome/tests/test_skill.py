"""Welcome skill tests.

Pins the keyboard contract — channel-agnostic ``[{label, callback}]`` —
plus the match predicate (/start + cb:welcome:*) and the
config-driven button-type fallback ladder.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


from apps.skills.base import SkillContext
from apps.skills.welcome.skill import (
    ASK_PROMPT,
    FOOD_PROMPT,
    MINIAPP_ROUTES,
    WATER_PROMPT,
    WELCOME_TEXT,
    WelcomeSkill,
    _s5_first_action_buttons,
    _welcome_buttons,
)
from apps.tenancy.context import tenant_scope


def _ctx(text: str) -> SkillContext:
    """Build a SkillContext stub with a MagicMock bot_user.

    ``welcomed_at`` on a MagicMock is a truthy attribute, so this context
    reads as a **Returning User** for the DRF-1202 state resolver — which
    is what the keyboard-contract tests want (they assert the button
    ladder, and the ladder is the same in every greeting state)."""
    return SkillContext(
        conversation=MagicMock(),
        bot_user=MagicMock(),
        message_text=text,
    )


def _ctx_new_user(text: str) -> SkillContext:
    """Same stub, but the user has never been greeted → New User state."""
    bot_user = MagicMock()
    bot_user.welcomed_at = None
    conversation = MagicMock()
    conversation.skill_state = {}
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
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
        result = WelcomeSkill().handle(_ctx_new_user("/start"))
        assert result.reply_text == WELCOME_TEXT
        assert result.action_type == "welcome_menu"
        assert result.meta["reply_kind"] == "welcome"

    def test_zero_config_still_ships_the_booking_entry(self, settings):
        """DRF-963: «Записаться» / «Мои записи» are bot-native, so a
        deployment with no Mini App config is no longer left without any
        booking entry point. Only «Профиль» (Mini-App-only) drops out.

        Pinned as a composition, not as a count — the count is the canon
        BOUNDARY and lives in :class:`TestCanonQuickActionCeilingAC42`.
        Asserting a bare ``len(buttons) == N`` here is what let the
        violation survive two rounds of «сократили и поправили число»."""

        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        callbacks = [b["callback"] for b in buttons]
        # DRF-1200: дневник еды / вода / «Задать вопрос» ушли с первого
        # экрана — обычным текстом они по-прежнему доступны.
        assert callbacks == [
            "cb:menu:book",
            "cb:menu:my_bookings",
            "cb:menu:help",
            "cb:welcome:start_s2",
        ]
        # Everything is a bot callback — nothing needs a Mini App.
        assert all("web_app" not in b and "url" not in b for b in buttons)

    def test_web_app_config_emits_open_app_profile_button(self, settings):
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        # Composition, not a count — the ceiling is guarded separately in
        # :class:`TestCanonQuickActionCeilingAC42`.
        assert [b["callback"] for b in buttons] == [
            "cb:menu:book",
            "cb:menu:my_bookings",
            "open_profile",
            "cb:menu:help",
            "cb:welcome:start_s2",
        ]
        # DRF-963: booking actions route into the bot, not the Mini App.
        assert "web_app" not in buttons[0] and "web_app" not in buttons[1]
        # Flat slug payload — MAX rejects open_app payloads with `=`
        # (HTTP 400 proto.payload). Mini App's parseStartRoute resolves
        # these by direct lookup.
        assert buttons[2]["web_app"] == "id583_bot"
        # Help + S1 rows: never carry web_app.
        for btn in buttons[3:]:
            assert "web_app" not in btn
            assert btn["callback"].startswith("cb:")

    def test_miniapp_url_fallback_emits_link_profile_button(self, settings):
        """When the bot has no web_app username but a base URL is
        configured, fall back to a link button for the Mini App nav.
        Routes are appended cleanly regardless of trailing/leading slashes."""

        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        assert [b["label"] for b in buttons] == [
            "📅 Записаться",
            "📋 Мои записи",
            "👤 Профиль",
            "❓ Помощь",
            "▶️ Начать",
        ]
        # DRF-1326: the whole client path, joined onto a bare-domain base.
        # Was ``/profile`` — not a route; the client screen is
        # ``/customer/profile``. Route existence is enforced against
        # App.tsx by tests/test_miniapp_routes.py.
        assert buttons[2]["url"] == "https://miniapp-dev.example/customer/profile"

    def test_web_app_takes_precedence_over_miniapp_url(self, settings):
        """If both are set, the native ``open_app`` UX wins."""
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"
        result = WelcomeSkill().handle(_ctx("/start"))
        buttons = result.action_data["buttons"]
        assert "web_app" in buttons[2]
        assert "url" not in buttons[2]

    def test_brief_minimum_actions_always_present(self, settings):
        """DRF-963 brief: welcome must offer «Записаться», «Мои записи»
        and «Помощь» regardless of Mini App configuration."""
        for web_app, miniapp_url in (("", ""), ("id583_bot", ""), ("", "https://m.example/")):
            settings.MAX_BOT_WEB_APP = web_app
            settings.MAX_MINIAPP_URL = miniapp_url
            buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]
            # Link-type buttons carry ``url`` instead of ``callback``.
            callbacks = {b.get("callback") for b in buttons}
            assert {"cb:menu:book", "cb:menu:my_bookings", "cb:menu:help"} <= callbacks

    @pytest.mark.parametrize(
        ("web_app", "miniapp_url"),
        [("", ""), ("id583_bot", ""), ("", "https://m.example/")],
    )
    def test_no_anketa_entry_point_on_first_screen(self, settings, web_app, miniapp_url):
        """DRF-1199 — BOT-001 §13.3: «The following MUST NOT appear in
        First Contact: a standalone "Анкета" object or button». Тот же
        запрет в non-goal #2 («No standalone questionnaire»), в AC-5.1
        и в анти-паттерне CDP «Questionnaire as entry price».

        Проверяем при всех трёх конфигурациях клавиатуры — запрет не
        зависит от того, настроен ли Mini App."""
        settings.MAX_BOT_WEB_APP = web_app
        settings.MAX_MINIAPP_URL = miniapp_url
        buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]
        assert all(b.get("callback") != "cb:anketa:start" for b in buttons)
        assert all("нкет" not in b["label"] for b in buttons)

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

        result = WelcomeSkill().handle(_ctx_new_user("cb:welcome:book"))
        assert result.reply_text == WELCOME_TEXT
        assert result.action_type == "welcome_menu"


# ───────────────────────────────────────────────────────────────────────
# S1 onboarding auto-trigger (task #85, 2026-05-26)
# ───────────────────────────────────────────────────────────────────────


from apps.skills.welcome.skill import (  # noqa: E402
    S1_MULTITENANT_TEXT_TEMPLATE,
    S2_CONSENT_TEXT,
    S2_REFUSED_TEXT,
    S2A_DETAILS_TEXT,
    S3_POSITIONING_TEXT,
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

        # #1074 — the per-tenant dispatch runs inside tenant_scope; WelcomeSkill
        # stamps consent_at only when a tenant is in scope (on the global path
        # record_global_consent stamps it atomically instead).
        with tenant_scope(unwelcomed_bot_user.tenant):
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
    def test_consent_yes_does_not_stamp_on_global_path(self, unwelcomed_bot_user):
        """#1074 — at ``current_tenant() is None`` (tenant-less GLOBAL path)
        WelcomeSkill does NOT stamp consent_at itself; record_global_consent stamps
        it ATOMICALLY with the ConsentRecord. Locks the guard's skip branch."""
        skill = WelcomeSkill()
        assert unwelcomed_bot_user.consent_at is None

        # No tenant_scope entered → current_tenant() is None → guard skips the stamp.
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.consent_at is None  # NOT stamped on the global path
        assert result.meta["reply_kind"] == "welcome_s5_first_action"  # S5 still renders

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

        with tenant_scope(unwelcomed_bot_user.tenant):
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
        with (
            tenant_scope(unwelcomed_bot_user.tenant),
            caplog.at_level(_logging.ERROR, logger="apps.skills.welcome.skill"),
        ):
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
    """S3 conditional positioning + S5 5-button grid (Tau §6 + §8)."""

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

    @pytest.mark.django_db
    def test_s2a_path_also_stamps_consent_at(self, unwelcomed_bot_user):
        """Same idempotent stamping helper для обоих consent paths."""
        skill = WelcomeSkill()
        assert unwelcomed_bot_user.consent_at is None
        with tenant_scope(unwelcomed_bot_user.tenant):
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

        with tenant_scope(unwelcomed_bot_user.tenant):
            skill.handle(
                _ctx_with_botuser("cb:welcome:consent_yes_via_s2a", unwelcomed_bot_user),
            )
        unwelcomed_bot_user.refresh_from_db()
        assert unwelcomed_bot_user.consent_at == original_consent_at

    @pytest.mark.django_db
    def test_s5_grid_zero_config_ships_no_buttons_but_invites_free_text(
        self, unwelcomed_bot_user, settings
    ):
        """DRF-1199 — zero-config: анкета была ЕДИНСТВЕННОЙ кнопкой этого
        экрана, и она запрещена каноном. Пустого экрана не остаётся:
        клавиатура опциональна (BOT-001 AC-4.1 «Quick Actions are
        optional»), а копия приглашает сказать своими словами — §6.1
        «The greeting SHOULD offer help or invite the user to state their
        goal in free text»."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        assert result.action_data["buttons"] == []
        assert "своими словами" in result.reply_text
        assert S3_POSITIONING_TEXT in result.reply_text

    @pytest.mark.django_db
    def test_s5_grid_web_app_emits_4_open_app_buttons(self, unwelcomed_bot_user, settings):
        """Mini App configured: 4 кнопки — 3 primary actions
        (open_water_add_250 / open_goal_select / open_catalog) +
        open_home («Просто посмотреть»). Tau §8 routing table минус
        анкета (DRF-1199) минус «📸 Сфотографировать еду»: тот экран
        падает в момент использования, и владелец решил снять кнопку —
        обоснование и границы в :class:`TestFoodScanButtonRemoved`."""
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        buttons = result.action_data["buttons"]
        # Composition, not a bare count — the «not more than five» boundary
        # is guarded once, in TestCanonQuickActionCeilingAC42 (AC-4.2).
        callbacks = [b["callback"] for b in buttons]
        assert callbacks == [
            "open_water_add_250",
            "open_goal_select",
            "open_catalog",
            "open_home",
        ]
        for btn in buttons:
            assert btn["web_app"] == "id583_bot"

    @pytest.mark.django_db
    def test_s5_grid_uses_2_column_layout(self, unwelcomed_bot_user, settings):
        """Tau §8 Variant A = Grid 2×2 + exit valve → button_columns=2."""
        settings.MAX_BOT_WEB_APP = "id583_bot"
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        assert result.action_data["button_columns"] == 2

    @pytest.mark.django_db
    def test_s5_grid_miniapp_url_fallback_emits_link_buttons(self, unwelcomed_bot_user, settings):
        """No MAX_BOT_WEB_APP but MAX_MINIAPP_URL set → link buttons
        for the 3 primary actions + «Просто посмотреть» exit."""
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        buttons = result.action_data["buttons"]
        # 3 primary URL + «просто посмотреть» URL (DRF-1199 убрал анкету,
        # снятие «📸 Сфотографировать еду» — четвёртую).
        # Пинуется составом, не числом — потолок AC-4.2 сторожит
        # TestCanonQuickActionCeilingAC42.
        urls = [b.get("url") for b in buttons if "url" in b]
        assert len(urls) == len(buttons)
        # DRF-1167: link fallback points at the same Mini App routes the
        # slugs resolve to in max-sdk.ts::_ROUTE_MAP (previously /food_scan,
        # /goal_select etc. — none of which exist as routes).
        # DRF-1326: «Найти услугу» was the last bare slug left here
        # (``catalog``) — one table, one form, all of it under
        # ``customer/``. Existence of each route is enforced against
        # App.tsx by tests/test_miniapp_routes.py; this list only pins
        # which screen each button opens.
        assert urls == [
            "https://miniapp-dev.example/customer/wellness",
            "https://miniapp-dev.example/customer/goal-select",
            "https://miniapp-dev.example/customer/catalog",
            "https://miniapp-dev.example/customer/main",
        ]


class TestFoodScanButtonRemoved:
    """«📸 Сфотографировать еду» снята со стартовой сетки.

    ## Почему снята

    Кнопка открывала ``/customer/food-scanner/capture`` — экран мини-аппа,
    который **открывается, выглядит рабочим и падает в момент
    использования**: все четыре эндпоинта еды закрыты ``guardProd`` в
    ``apps/miniapp/src/lib/food-scanner.ts``, вне DEV он бросает
    ``StubNotWiredError``.

    Ошибка формально поймана, но в общую ветку: человек, уже выбравший
    приём пищи и сделавший фото, читает «Сервис недоступен · Попробуй
    через минуту» — а через минуту не заработает, потому что не
    подключено вовсе. «Переснять» и «Написать вручную» упираются в тот
    же guard. Кнопка из первой сетки вела человека в тупик.

    Решение владельца: экраны он готовит отдельно, подключение
    эндпоинтов — отдельная задача, а до тех пор **указателя на поломку в
    стартовой сетке быть не должно**.

    ## Что НЕ тронуто, и почему это не дыра

    * Отправка фото боту — рабочий путь и остаётся: ``FoodScannerSkill``
      сам забирает ход с вложением, а :data:`FOOD_PROMPT` по-прежнему
      зовёт прислать фото в чат. Дневник еды не стал недоступен, он
      перестал быть доступен **через поломанный экран**.
    * ``MINIAPP_ROUTES["open_food_scan"]`` оставлен в силе намеренно.
      Таблица маршрутов ничего не показывает человеку — нажать можно
      только кнопку. Экраны готовятся, кнопка вернётся; снос маршрута
      означал бы его восстановление через неделю плюс синхронный правки
      в двух зеркалах (``App.tsx``, ``max-sdk.ts``), которые мини-апп
      всё равно держит. Целостность пары «маршрут ↔ оба зеркала»
      продолжает сторожить ``tests/test_miniapp_routes.py``.

    ## Почему проверка именно такая

    Проверяется **назначение**, а не подпись: «нет кнопки с текстом про
    еду» запретило бы и будущую рабочую кнопку. Ловится ровно то, что
    ведёт в поломанный экран — слаг ``open_food_scan`` и путь
    ``food-scanner`` — под каждой конфигурацией мини-аппа и под обоими
    состояниями флага отката, потому что кнопку строят три разные ветки.
    """

    #: Все три состояния лестницы мини-аппа: open_app, link-fallback,
    #: zero-config. Кнопку строит своя ветка в каждом.
    _CONFIGS = [
        pytest.param("id583_bot", "", id="web-app"),
        pytest.param("", "https://miniapp-dev.example/", id="link-fallback"),
        pytest.param("", "", id="zero-config"),
    ]

    #: Флаг отката строит ДРУГУЮ клавиатуру приветствия — если бы
    #: кнопка осталась в этой ветке, снятие было бы половинчатым.
    _UX_FLAGS = [pytest.param(True, id="pilot-ux-on"), pytest.param(False, id="pilot-ux-off")]

    @pytest.mark.parametrize("pilot_ux", _UX_FLAGS)
    @pytest.mark.parametrize("web_app,miniapp_url", _CONFIGS)
    def test_no_welcome_button_opens_the_food_scanner(
        self, settings, web_app, miniapp_url, pilot_ux
    ):
        settings.MAX_BOT_WEB_APP = web_app
        settings.MAX_MINIAPP_URL = miniapp_url
        settings.PILOT_CONVERSATIONAL_UX = pilot_ux

        for button in _welcome_buttons() + _s5_first_action_buttons():
            assert button.get("callback") != "open_food_scan", (
                f"«{button['label']}» снова отправляет open_food_scan. Мини-апп "
                "резолвит его в /customer/food-scanner/capture, где guardProd "
                "бросает StubNotWiredError — экран открывается и падает в "
                "момент использования."
            )
            assert "food-scanner" not in button.get("url", ""), (
                f"«{button['label']}» снова ведёт на {button.get('url')!r} — это "
                "экран, который падает в момент использования (StubNotWiredError)."
            )

    def test_s5_grid_keeps_the_three_remaining_actions(self, settings):
        """Снята одна кнопка, а не сетка.

        Состав, а не число: «стало на одну меньше» прошло бы и на
        случайно выпавшей «Найти услугу».
        """
        settings.MAX_BOT_WEB_APP = "id583_bot"
        settings.MAX_MINIAPP_URL = ""
        assert [b["callback"] for b in _s5_first_action_buttons()] == [
            "open_water_add_250",
            "open_goal_select",
            "open_catalog",
            "open_home",
        ]

    def test_route_stays_in_the_table_for_when_the_button_returns(self):
        """Маршрут жив — на него просто больше никто не указывает.

        Пинуется по имени, чтобы «заодно почистить таблицу» стало
        осознанным решением, а не побочным эффектом.
        """
        assert MINIAPP_ROUTES["open_food_scan"] == "customer/food-scanner/capture"

    def test_photo_to_the_bot_is_still_the_offered_path(self):
        """Дневник еды не стал недоступен.

        Копия по-прежнему зовёт прислать фото в чат — это тот путь,
        который работает. Сам роутинг «фото → FoodScannerSkill» пинует
        ``apps/channels/max/tests/test_handler.py``.
        """
        assert "фото" in FOOD_PROMPT
        result = WelcomeSkill().handle(_ctx("cb:welcome:food"))
        assert result.reply_text == FOOD_PROMPT


# ───────────────────────────────────────────────────────────────────────
# S1 multi-tenant variant — task #85 part 4, 2026-05-26
# ───────────────────────────────────────────────────────────────────────


class TestS1MultiTenantVariant:
    """S1 multi-tenant detection (Tau §4): folded ``/start <payload>``
    text. Recognised prefixes ref_/qr_/ig_post_ → multi-tenant render
    с salon name. Standard /start + unparseable payloads → fall through
    to existing WELCOME_TEXT (strictly additive, no regression)."""

    def test_matches_start_with_payload(self):
        """``matches()`` accepts /start with deeplink suffix (PR 3)."""
        skill = WelcomeSkill()
        assert skill.matches(_ctx("/start ref_user_42")) is True
        assert skill.matches(_ctx("/start qr_99_window")) is True
        assert skill.matches(_ctx("/start ig_post_123")) is True

    def test_matches_baseline_start_unchanged(self):
        """Baseline ``/start`` still matches — backward compat with
        empty-payload bot_started events + Mini App entry points."""
        skill = WelcomeSkill()
        assert skill.matches(_ctx("/start")) is True

    @pytest.mark.django_db
    def test_ref_payload_renders_multitenant_text_with_salon_name(self, unwelcomed_bot_user):
        """``ref_<user_id>`` → multi-tenant text c salon name из tenant.
        Test fixture's tenant.name = «S1 Test» (fixture name)."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("/start ref_user_42", unwelcomed_bot_user),
        )
        assert result.meta["reply_kind"] == "welcome_s1_multitenant"
        assert result.meta["start_param"] == "ref_user_42"
        # Tenant name substituted into template.
        expected_text = S1_MULTITENANT_TEXT_TEMPLATE.format(
            salon_name=unwelcomed_bot_user.tenant.name
        )
        assert result.reply_text == expected_text

    @pytest.mark.django_db
    def test_qr_payload_recognised_as_multitenant(self, unwelcomed_bot_user):
        """``qr_<salon_id>_<placement>`` → multi-tenant variant."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("/start qr_99_window", unwelcomed_bot_user),
        )
        assert result.meta["reply_kind"] == "welcome_s1_multitenant"
        assert result.meta["start_param"] == "qr_99_window"

    @pytest.mark.django_db
    def test_ig_post_payload_recognised_as_multitenant(self, unwelcomed_bot_user):
        """``ig_post_<id>`` → multi-tenant variant."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("/start ig_post_123", unwelcomed_bot_user),
        )
        assert result.meta["reply_kind"] == "welcome_s1_multitenant"

    @pytest.mark.django_db
    def test_baseline_start_renders_standard_welcome(self, unwelcomed_bot_user):
        """No payload → existing WELCOME_TEXT. Strict backward compat."""
        skill = WelcomeSkill()
        result = skill.handle(_ctx_with_botuser("/start", unwelcomed_bot_user))
        assert result.reply_text == WELCOME_TEXT
        assert result.meta["reply_kind"] == "welcome"

    @pytest.mark.django_db
    def test_unparseable_payload_falls_through_to_standard_welcome(self, unwelcomed_bot_user):
        """Unknown prefix (e.g. ``utm_campaign``, ``promo_xyz``) is NOT
        a multi-tenant variant — falls through к standard WELCOME_TEXT
        rather than 500 или rendering empty {salon_name}."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser("/start utm_summer2026", unwelcomed_bot_user),
        )
        assert result.reply_text == WELCOME_TEXT
        assert result.meta["reply_kind"] == "welcome"

    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "weird_payload",
        ["кириллица", "🎉_emoji_payload", "ref-hyphen-not-underscore", "Ref_capitalised"],
    )
    def test_non_ascii_or_malformed_payload_falls_through_safely(
        self, unwelcomed_bot_user, weird_payload
    ):
        """Defensive audit (CR #810 nit #6): Cyrillic / emoji / hyphen-
        instead-of-underscore / capitalised prefix payloads NEVER trigger
        multi-tenant variant — case-sensitive ASCII prefix match. All
        fall through к standard WELCOME_TEXT without 500 / panic."""
        skill = WelcomeSkill()
        result = skill.handle(
            _ctx_with_botuser(f"/start {weird_payload}", unwelcomed_bot_user),
        )
        assert result.reply_text == WELCOME_TEXT
        assert result.meta["reply_kind"] == "welcome"


# ───────────────────────────────────────────────────────────────────────
# DRF-1207 — приветствие не просыпается посреди разговора
# ───────────────────────────────────────────────────────────────────────


def _conversation_for(bot_user):
    from apps.conversations.models import Conversation

    return Conversation.all_tenants.create(tenant=bot_user.tenant, bot_user=bot_user)


def _record(conversation, role: str, content: str):
    from apps.conversations.models import Message

    return Message.all_tenants.create(
        conversation=conversation,
        tenant=conversation.tenant,
        role=role,
        content=content,
    )


def _ctx_in(text: str, bot_user, conversation) -> SkillContext:
    return SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
    )


class TestMidConversationWakeUp:
    """DRF-1207 / BOT-001 P1 + CDP «Amnesia».

    Когда первый ход перехватил другой навык, ``welcomed_at`` остаётся
    NULL. Раньше guard исключал ВСЕ сообщения текущего разговора, поэтому
    на втором ходу приветствие всплывало посреди диалога. Теперь
    считается: ровно одна строка (входящее, записанное каналом до
    диспетчеризации) — это первый контакт, больше — разговор уже идёт.
    """

    @pytest.mark.django_db
    def test_first_contact_still_auto_welcomes(self, unwelcomed_bot_user):
        conversation = _conversation_for(unwelcomed_bot_user)
        # Канал записывает входящее ДО dispatch — ровно одна строка.
        _record(conversation, "user", "Привет")

        assert WelcomeSkill().matches(_ctx_in("Привет", unwelcomed_bot_user, conversation)) is True

    @pytest.mark.django_db
    def test_does_not_wake_up_after_another_skill_took_the_first_turn(self, unwelcomed_bot_user):
        conversation = _conversation_for(unwelcomed_bot_user)
        # Ход 1 достался booking: входящее + ответ бота.
        _record(conversation, "user", "хочу записаться на маникюр")
        _record(conversation, "assistant", "На какое время вас записать?")
        # Ход 2 — новое входящее, записанное каналом до dispatch.
        _record(conversation, "user", "спасибо")

        assert (
            WelcomeSkill().matches(_ctx_in("спасибо", unwelcomed_bot_user, conversation)) is False
        )

    @pytest.mark.django_db
    def test_messages_in_another_conversation_still_block(self, unwelcomed_bot_user):
        older = _conversation_for(unwelcomed_bot_user)
        _record(older, "user", "прошлый разговор")
        # Активный разговор ровно один (constraint) — прошлый закрыт.
        older.is_active = False
        older.save(update_fields=["is_active"])
        current = _conversation_for(unwelcomed_bot_user)
        _record(current, "user", "привет снова")

        assert (
            WelcomeSkill().matches(_ctx_in("привет снова", unwelcomed_bot_user, current)) is False
        )

    @pytest.mark.django_db
    def test_explicit_start_is_not_affected(self, unwelcomed_bot_user):
        """``/start`` — явный жест пользователя, guard его не касается."""
        conversation = _conversation_for(unwelcomed_bot_user)
        _record(conversation, "user", "хочу записаться")
        _record(conversation, "assistant", "На какое время?")
        _record(conversation, "user", "/start")

        assert WelcomeSkill().matches(_ctx_in("/start", unwelcomed_bot_user, conversation)) is True


# ───────────────────────────────────────────────────────────────────────
# DRF-1206 — любая админская задача снимает автоприветствие (закрепление)
# ───────────────────────────────────────────────────────────────────────


def _admin_task(conversation, status):
    from apps.handoff.models import AdminTask

    return AdminTask.all_tenants.create(
        tenant=conversation.tenant,
        bot_user=conversation.bot_user,
        conversation=conversation,
        task_type=AdminTask.TaskType.HANDOFF,
        status=status,
    )


class TestAnyAdminTaskBlocksAutoWelcome:
    """DRF-1206 — постановка не подтвердилась; тест закрепляет ФАКТ.

    Проверка `AdminTask…exists()` в `_flow_already_established` — это не
    детектор состояния «User with an Active Task» (его роль исполняет
    `_greeting_state` по `Conversation.skill_state`, DRF-1202), а ответ на
    вопрос «это вообще первый контакт?». Тикет, который когда-либо
    существовал, доказывает, что нет: BOT-001 §8 определяет New User как
    человека «with no prior recognized interaction».

    Попытка сузить арму до активных статусов ломает
    `human_handoff…TestDispatcherGuard::test_resume_after_resolve` —
    сообщение после закрытого хендофа возвращается полным приветствием
    новичка вместо продолжения. Тест ниже держит арму на месте.
    """

    @pytest.mark.django_db
    @pytest.mark.parametrize("status", ["open", "in_progress", "resolved", "cancelled"])
    def test_admin_task_of_any_status_blocks_auto_trigger(self, unwelcomed_bot_user, status):
        conversation = _conversation_for(unwelcomed_bot_user)
        _record(conversation, "user", "Привет")
        _admin_task(conversation, status)

        assert WelcomeSkill().matches(_ctx_in("Привет", unwelcomed_bot_user, conversation)) is False

    @pytest.mark.django_db
    def test_explicit_start_still_greets_such_a_user(self, welcomed_bot_user):
        """И это не «приветствие пропало навсегда»: по явному `/start`
        человек получает приветствие вернувшегося (DRF-1202, §9.1)."""
        from apps.skills.welcome.skill import RETURNING_TEXT

        conversation = _conversation_for(welcomed_bot_user)
        _admin_task(conversation, "resolved")

        result = WelcomeSkill().handle(_ctx_in("/start", welcomed_bot_user, conversation))
        assert result.reply_text == RETURNING_TEXT


# ───────────────────────────────────────────────────────────────────────
# DRF-1202 — ровно три состояния приветствия (BOT-001 P8 / AC-3.1)
# ───────────────────────────────────────────────────────────────────────


from apps.skills.welcome.skill import (  # noqa: E402
    ACTIVE_TASK_TEXT,
    GREETING_STATE_ACTIVE_TASK,
    GREETING_STATE_NEW,
    GREETING_STATE_RETURNING,
    RETURNING_TEXT,
    _greeting_state,
)


class TestThreeGreetingStates:
    """BOT-001 P8: «MVP First Contact MUST use exactly three greeting
    states: New User, Returning User, User with an Active Task.»
    AC-3.1 требует, чтобы эти три состояния были проверяемы.

    До правки реализовано было одно: `/start` от вернувшегося отдавал
    копию для новичка, а любой другой его текст навык не брал вовсе.
    """

    @pytest.mark.django_db
    def test_new_user_state(self, unwelcomed_bot_user):
        conversation = _conversation_for(unwelcomed_bot_user)
        ctx = _ctx_in("/start", unwelcomed_bot_user, conversation)
        assert _greeting_state(ctx) == GREETING_STATE_NEW
        assert WelcomeSkill().handle(ctx).reply_text == WELCOME_TEXT

    @pytest.mark.django_db
    def test_returning_user_state(self, welcomed_bot_user):
        """§9.1 «The greeting SHOULD acknowledge the return»; §9.3 — новое
        намерение свободным текстом доступно всегда, продолжение прошлой
        темы не навязывается."""
        conversation = _conversation_for(welcomed_bot_user)
        ctx = _ctx_in("/start", welcomed_bot_user, conversation)
        assert _greeting_state(ctx) == GREETING_STATE_RETURNING

        result = WelcomeSkill().handle(ctx)
        assert result.reply_text == RETURNING_TEXT
        assert result.meta["reply_kind"] == "welcome_returning"
        assert result.reply_text != WELCOME_TEXT

    @pytest.mark.django_db
    def test_active_task_state(self, welcomed_bot_user):
        """§10.1 «Ayla MAY offer to continue the Active Task … MUST NOT
        force continuation». Обнаружение — рантайм-вопрос (§10.3), берём
        собственную запись рантайма о незавершённой работе."""
        conversation = _conversation_for(welcomed_bot_user)
        conversation.skill_state = {"booking_flow": {"stage": "awaiting_selection"}}
        conversation.save(update_fields=["skill_state"])
        ctx = _ctx_in("/start", welcomed_bot_user, conversation)
        assert _greeting_state(ctx) == GREETING_STATE_ACTIVE_TASK

        result = WelcomeSkill().handle(ctx)
        assert result.reply_text == ACTIVE_TASK_TEXT
        assert result.meta["reply_kind"] == "welcome_active_task"
        # Продолжение предлагается, но не навязывается — свободный текст
        # назван прямо.
        assert "своими словами" in result.reply_text

    @pytest.mark.django_db
    def test_empty_skill_state_is_not_an_active_task(self, welcomed_bot_user):
        """Очищенный FSM (``write_skill_state(conv, key, None)`` кладёт
        None, а не удаляет ключ) не должен считаться активной задачей."""
        conversation = _conversation_for(welcomed_bot_user)
        conversation.skill_state = {"nutrition_anketa": None}
        conversation.save(update_fields=["skill_state"])
        ctx = _ctx_in("/start", welcomed_bot_user, conversation)
        assert _greeting_state(ctx) == GREETING_STATE_RETURNING

    @pytest.mark.django_db
    def test_deeplink_variant_is_not_a_fourth_state(self, unwelcomed_bot_user):
        """Вариант по deeplink — entry context внутри состояния New User,
        а не четвёртое состояние. Канон велит его учитывать: P3
        «Greeting behavior MUST adapt to entry context: user state,
        channel and available context», §17 шаг 1 «Determine entry
        context: channel, user state, trigger/deep link»."""
        conversation = _conversation_for(unwelcomed_bot_user)
        ctx = _ctx_in("/start ref_42", unwelcomed_bot_user, conversation)
        assert _greeting_state(ctx) == GREETING_STATE_NEW

        result = WelcomeSkill().handle(ctx)
        assert result.meta["reply_kind"] == "welcome_s1_multitenant"


# ───────────────────────────────────────────────────────────────────────
# BOT-001 AC-4.2 — постоянный страж границы канона (DRF-1200)
# ───────────────────────────────────────────────────────────────────────

#: BOT-001 AC-4.2: «Quick Actions, when present, MUST NOT exceed 5».
#: Это потолок канона, а не «текущее число кнопок». Если экран сократили
#: ещё — тесты ниже остаются зелёными; если кто-то добавил шестую —
#: падают. Менять эту константу можно только вместе с каноном.
CANON_MAX_QUICK_ACTIONS = 5

#: Все конфигурации Mini App, которые меняют состав клавиатуры:
#: пилотная (``MAX_BOT_WEB_APP`` задан → open_app), внешняя ссылка
#: (``MAX_MINIAPP_URL`` → link) и пустая (zero-config).
_MINIAPP_CONFIGS = [
    pytest.param("", "", id="zero-config"),
    pytest.param("id583_bot", "", id="pilot-web-app"),
    pytest.param("", "https://m.example/", id="miniapp-url"),
]


class TestCanonQuickActionCeilingAC42:
    """AC-4.2: набор быстрых действий первого экрана не больше пяти.

    Страж, а не снимок. Предыдущая правка (DRF-1200, первая попытка)
    сократила девять кнопок до восьми и переписала утверждение
    ``== 9`` на ``== 8`` — нарушение канона осталось зацементировано
    зелёным тестом, просто на другом числе. Здесь закреплена ГРАНИЦА:
    ``<= 5``. Добавление шестой кнопки роняет тест при любой
    конфигурации Mini App; дальнейшее сокращение — нет.

    Канон намеренно не фиксирует ни текст, ни состав кнопок
    (BOT-001 §8.2: «Quick Action copy MUST NOT be canonicalized… examples
    only»), поэтому проверяется ровно количество.
    """

    @pytest.mark.parametrize(("web_app", "miniapp_url"), _MINIAPP_CONFIGS)
    def test_s1_welcome_keyboard_within_canon_ceiling(self, settings, web_app, miniapp_url):
        """S1 (первый экран, ``/start``) — не больше пяти быстрых действий."""
        settings.PILOT_CONVERSATIONAL_UX = True
        settings.MAX_BOT_WEB_APP = web_app
        settings.MAX_MINIAPP_URL = miniapp_url
        buttons = WelcomeSkill().handle(_ctx("/start")).action_data["buttons"]
        assert len(buttons) <= CANON_MAX_QUICK_ACTIONS, (
            f"AC-4.2 нарушен: {len(buttons)} кнопок на S1 "
            f"(web_app={web_app!r}, miniapp_url={miniapp_url!r}): "
            f"{[b['label'] for b in buttons]}"
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize(("web_app", "miniapp_url"), _MINIAPP_CONFIGS)
    def test_s5_first_action_grid_within_canon_ceiling(
        self, unwelcomed_bot_user, settings, web_app, miniapp_url
    ):
        """S5 (сетка первого действия после согласия) — тот же потолок."""
        settings.PILOT_CONVERSATIONAL_UX = True
        settings.MAX_BOT_WEB_APP = web_app
        settings.MAX_MINIAPP_URL = miniapp_url
        result = WelcomeSkill().handle(
            _ctx_with_botuser("cb:welcome:consent_yes", unwelcomed_bot_user),
        )
        buttons = result.action_data["buttons"]
        assert len(buttons) <= CANON_MAX_QUICK_ACTIONS, (
            f"AC-4.2 нарушен: {len(buttons)} кнопок на S5 "
            f"(web_app={web_app!r}, miniapp_url={miniapp_url!r}): "
            f"{[b['label'] for b in buttons]}"
        )
