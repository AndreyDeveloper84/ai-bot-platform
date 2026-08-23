"""MAX channel handler integration tests (DRF-444 / Sprint 2 / D5).

Mocks `outbound.send_message` (D2) + Redis (`short_term._redis_client`)
+ uses the real DB for identity + conversations + audit. Caller must
enter `tenant_scope` before invoking `handle_max_event` — these tests
do that explicitly to mirror what the consumer's TenantAwareTask
base class does in production.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.channels.max.outbound import MaxAPIError
from apps.conversations.models import Conversation, Message
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _payload(*, text="Привет", user_id=12345, chat_id=67890, mid="m-1", attachments=None):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {
                "mid": mid,
                "seq": 1,
                "text": text,
                "attachments": attachments or [],
            },
        },
    }


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="handler-a", name="A")


@pytest.fixture
def mock_send(monkeypatch):
    calls = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    """Reuse the same _FakeRedis pattern as C1's tests."""

    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


def _mark_welcomed(*, user_id="12345", chat_id="67890"):
    """Pre-create the BotUser as already-welcomed (must run inside tenant_scope).

    WelcomeSkill's task-#85 auto-trigger fires on the FIRST message from any
    BotUser with ``welcomed_at IS NULL``, intercepting every skill below it. The
    tests below pre-date that behaviour and exercise the POST-welcome pipeline
    (echo / food_scanner / empty-fallback / outbound error). Stamping
    ``welcomed_at`` up front isolates them from the auto-welcome so they test
    what they were written to test — not the welcome interception.
    """
    from django.utils import timezone

    from apps.identity.services import resolve_or_create_bot_user

    bu = resolve_or_create_bot_user(channel="max", channel_user_id=user_id, chat_id=chat_id)
    bu.welcomed_at = timezone.now()
    bu.save(update_fields=["welcomed_at"])
    return bu


class TestHappyPath:
    def test_unrecognised_text_creates_full_chain(self, tenant_a, mock_send, fake_redis, settings):
        """DRF-963 (U-5): an unrecognised turn no longer echoes — it gets
        the honest fallback + main menu. The persistence chain is unchanged."""
        from apps.skills.menu.replies import FALLBACK_TEXT

        settings.STRICT_TENANT_SCOPE = "strict"
        trace = uuid4()
        with tenant_scope(tenant_a), trace_id_scope(str(trace)):
            _mark_welcomed()  # isolate from the #85 auto-welcome
            max_handler.handle_max_event(_payload(text="Привет"), trace_id=trace)

        # 1 BotUser created.
        bots = BotUser.all_tenants.filter(channel="max", channel_user_id="12345")
        assert bots.count() == 1
        bot = bots.first()
        assert bot.tenant_id == tenant_a.id
        assert bot.chat_id == "67890"

        # 1 Conversation, active, tenant matches.
        convs = Conversation.all_tenants.filter(bot_user=bot, is_active=True)
        assert convs.count() == 1
        conv = convs.first()
        assert conv.tenant_id == tenant_a.id

        # 2 Messages: user + assistant, same conversation, same trace.
        msgs = list(Message.all_tenants.filter(conversation=conv).order_by("created_at"))
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "Привет"
        assert msgs[0].trace_id == trace
        assert msgs[1].role == "assistant"
        assert msgs[1].content == FALLBACK_TEXT
        assert msgs[1].trace_id == trace

        # send_message called once with the honest fallback — never an echo.
        assert len(mock_send) == 1
        assert mock_send[0]["chat_id"] == "67890"
        assert mock_send[0]["text"] == FALLBACK_TEXT
        assert mock_send[0]["text"] != "Привет"
        # …and it carries the main menu so the user has a way forward.
        assert mock_send[0]["attachments"]


class TestWelcomeBranch:
    def test_slash_start_triggers_welcome(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(_payload(text="/start"))

        sent = mock_send[0]["text"]
        assert "Формула тела" in sent
        # DRF-1203 — копия состояние-первая: приглашение сказать своими
        # словами вместо перечня услуг и «Выберите раздел:».
        assert "своими словами" in sent
        assert "Выберите раздел" not in sent
        # Assistant message body matches what we sent.
        assistant_msg = Message.all_tenants.get(role="assistant")
        assert assistant_msg.content == sent
        assert assistant_msg.rendered_text == sent


class TestAttachmentOnly:
    """Sprint 9 / P1 (DRF-818) — food_scanner skill now owns attachment-only
    turns and runs the Ayla recognition path. The Sprint 2 ``_FALLBACK_NO_ECHO``
    string survives only as the registry-empty defensive fallback in
    ``_echo_text`` — it does not fire in the normal pipeline.

    Verify food_scanner claims the turn and emits its photo-bytes prompt
    (no real Ayla call — channel adapter hasn't stashed bytes on the
    conversation, so food_scanner returns ``PHOTO_NO_BYTES``).
    """

    def test_attachment_only_routes_to_food_scanner(
        self, tenant_a, mock_send, fake_redis, settings, monkeypatch
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        # food_scanner Веха-1 gate (NUTRITION_ENABLED → FOOD_PHOTO_SCAN_ENABLED →
        # per-user consent) must pass to reach the no-bytes path; without it the
        # skill returns a feature-off/consent refusal instead of PHOTO_NO_BYTES.
        settings.NUTRITION_ENABLED = True
        settings.FOOD_PHOTO_SCAN_ENABLED = True

        # Веха 2 of photo adapter port now wires the download — mock it
        # to raise so this test continues to exercise the «no bytes
        # stashed» graceful path the food_scanner skill handles. The
        # dedicated photo-download integration tests live in
        # test_handler_photo.py.
        from apps.channels.max import handler as _h
        from apps.channels.max.photo import PhotoDownloadError

        def _raise_download(_url):
            raise PhotoDownloadError("mocked — bytes deliberately absent")

        monkeypatch.setattr(_h, "download_photo", _raise_download)

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            from django.utils import timezone

            bu = _mark_welcomed()  # isolate from the #85 auto-welcome → reach food_scanner
            bu.food_scanner_consent_at = timezone.now()  # pass the feature-consent gate
            bu.save(update_fields=["food_scanner_consent_at"])
            max_handler.handle_max_event(
                _payload(
                    text="",
                    attachments=[{"type": "image", "payload": {"url": "x"}}],
                )
            )
        # Photo-bytes path: download mocked to raise, so adapter sets
        # `conversation.last_photo_bytes = None` and food_scanner
        # returns the graceful "не получилось скачать" prompt.
        from apps.skills.food_scanner.skill import PHOTO_NO_BYTES

        assert mock_send[0]["text"] == PHOTO_NO_BYTES


class TestEmptyMessage:
    def test_empty_text_no_attachments_returns_question_mark(
        self, tenant_a, mock_send, fake_redis, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            _mark_welcomed()  # isolate from the #85 auto-welcome → exercise empty→"?"
            max_handler.handle_max_event(_payload(text="", attachments=[]))
        assert mock_send[0]["text"] == "?"


class TestReuseExistingBotUser:
    def test_second_message_same_user_id_uses_same_botuser_and_conversation(
        self, tenant_a, mock_send, fake_redis, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        trace = uuid4()
        with tenant_scope(tenant_a), trace_id_scope(str(trace)):
            max_handler.handle_max_event(_payload(text="первое", mid="m-1"))
            max_handler.handle_max_event(_payload(text="второе", mid="m-2"))

        # Exactly 1 BotUser, 1 Conversation, 4 Messages.
        assert BotUser.all_tenants.count() == 1
        assert Conversation.all_tenants.count() == 1
        assert Message.all_tenants.count() == 4
        assert len(mock_send) == 2


class TestTraceIdPropagation:
    def test_same_trace_id_on_all_records(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        trace = uuid4()
        with tenant_scope(tenant_a), trace_id_scope(str(trace)):
            max_handler.handle_max_event(_payload(text="hi"), trace_id=trace)

        # All Message rows for this conversation share the trace.
        msgs = Message.all_tenants.all()
        for m in msgs:
            assert m.trace_id == trace

        # `channels.max.outbound.sent` event also under same trace.
        sent_event = Event.objects.filter(
            tenant=tenant_a, event_type="channels.max.outbound.sent"
        ).first()
        assert sent_event is not None
        assert sent_event.trace_id == str(trace)


class TestIdempotencyDedup:
    """Sprint 2.5 H4 regression: handler wrap in with_idempotency prevents
    duplicate Message rows / outbound sends when the consumer's PEL
    retries the same event (e.g. handler crashed mid-send).
    """

    def test_same_channel_message_id_twice_dedup(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        trace = uuid4()
        with tenant_scope(tenant_a), trace_id_scope(str(trace)):
            # First call — full pipeline runs.
            max_handler.handle_max_event(_payload(text="hi", mid="same-mid-1"))
            # Second call with same mid — must short-circuit on AlreadyClaimed.
            max_handler.handle_max_event(_payload(text="hi", mid="same-mid-1"))

        # Exactly 1 BotUser + 1 Conversation + 2 Messages (user + assistant).
        # Second call did NOT add another set of rows.
        assert BotUser.all_tenants.count() == 1
        assert Conversation.all_tenants.count() == 1
        assert Message.all_tenants.count() == 2

        # Outbound send called once, not twice.
        assert len(mock_send) == 1

        # Dedup event emitted.
        dedup_events = Event.objects.filter(
            tenant=tenant_a, event_type="channels.max.handler.dedup"
        )
        assert dedup_events.exists()


def _callback_payload(
    *, callback_id="cb-1", payload="cb:welcome:ask", user_id=12345, chat_id=67890
):
    """MAX `message_callback` shape — matches the parser DTO contract."""
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000500,
            "callback_id": callback_id,
            "payload": payload,
            "user": {"user_id": user_id, "name": "Иван", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": "m-orig", "seq": 1, "text": "Выберите раздел"},
        },
        "user_locale": "ru",
    }


class TestBookingEnvelopeKeyboard:
    """The booking skill emits action_data in the *platform-canonical*
    envelope shape (see apps/skills/booking/skill.py:_action_data_for_pending):

        {"attachments": [{"type": "inline_keyboard",
                          "payload": {"buttons": [{"label": ..., "callback": ...}]}}]}

    Telegram reads this directly. MAX must too — otherwise booking
    confirm/cancel/reschedule keyboards silently vanish on MAX.
    Regression guard for Code Reviewer finding #1 (2026-05-21).
    """

    def test_envelope_flat_buttons_pass_through(
        self, tenant_a, mock_send, fake_redis, settings, monkeypatch
    ):
        settings.STRICT_TENANT_SCOPE = "strict"

        # Inject a fake skill that emits the booking envelope shape, so we
        # don't have to spin up the whole booking tool harness.
        from apps.skills import registry as skill_registry
        from apps.skills.base import SkillResult

        class _FakeBookingSkill:
            name = "fake-booking"

            def matches(self, ctx):
                return ctx.message_text == "/booking"

            def handle(self, ctx):
                return SkillResult(
                    reply_text="Подтвердить запись?",
                    action_type="booking_pending",
                    action_data={
                        "attachments": [
                            {
                                "type": "inline_keyboard",
                                "payload": {
                                    "buttons": [
                                        {"label": "✅ Да", "callback": "cb:book:confirm:tok-1"},
                                        {"label": "❌ Нет", "callback": "cb:book:cancel:tok-1"},
                                    ],
                                },
                            }
                        ],
                        "pending_action": {"kind": "confirm", "token": "tok-1"},
                    },
                )

        monkeypatch.setattr(
            skill_registry, "_skills", [_FakeBookingSkill()] + skill_registry._skills
        )

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(_payload(text="/booking"))

        attachments = mock_send[0]["attachments"]
        assert attachments is not None and len(attachments) == 1
        att = attachments[0]
        assert att["type"] == "inline_keyboard"
        # Two callback buttons, MAX wire format, in order.
        buttons = att["payload"]["buttons"]
        texts = [row[0]["text"] for row in buttons]
        assert texts == ["✅ Да", "❌ Нет"]
        payloads = [row[0]["payload"] for row in buttons]
        assert payloads == ["cb:book:confirm:tok-1", "cb:book:cancel:tok-1"]

    def test_envelope_pre_shaped_rows_pass_through(
        self, tenant_a, mock_send, fake_redis, settings, monkeypatch
    ):
        """Same envelope but buttons is already a 2-D matrix (rare —
        reminder tasks emit one row per CTA). Pass through unchanged."""
        settings.STRICT_TENANT_SCOPE = "strict"

        from apps.skills import registry as skill_registry
        from apps.skills.base import SkillResult

        class _FakeReminderSkill:
            name = "fake-reminder"

            def matches(self, ctx):
                return ctx.message_text == "/reminder"

            def handle(self, ctx):
                return SkillResult(
                    reply_text="Напомнить?",
                    action_data={
                        "attachments": [
                            {
                                "type": "inline_keyboard",
                                "payload": {
                                    "buttons": [
                                        [{"label": "Да", "callback": "cb:rem:yes"}],
                                        [{"label": "Нет", "callback": "cb:rem:no"}],
                                    ],
                                },
                            }
                        ],
                    },
                )

        monkeypatch.setattr(
            skill_registry, "_skills", [_FakeReminderSkill()] + skill_registry._skills
        )

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(_payload(text="/reminder"))

        rows = mock_send[0]["attachments"][0]["payload"]["buttons"]
        # 2 rows × 1 button preserved.
        assert [b[0]["text"] for b in rows] == ["Да", "Нет"]


class TestKeyboardPassThrough:
    """Inline-keyboard restoration (2026-05-20). When a skill emits
    ``action_data["buttons"]``, the MAX adapter must convert that to the
    native ``inline_keyboard`` attachment and pass it to ``send_message``.
    Regression guard for the post-cutover «пропали кнопки» bug.
    """

    def test_welcome_keyboard_attached_to_outbound(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        # Force the link-button branch — predictable + asserts the URL layout.
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = "https://miniapp-dev.example/"

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(_payload(text="/start"))

        sent = mock_send[0]
        attachments = sent["attachments"]
        assert attachments is not None
        # Exactly one attachment, of type inline_keyboard.
        assert len(attachments) == 1
        att = attachments[0]
        assert att["type"] == "inline_keyboard"
        buttons = att["payload"]["buttons"]
        # One row per button (default columns=1). Pinned as a composition,
        # not as a count: the «сколько кнопок» boundary is guarded once, in
        # apps/skills/welcome/tests/test_skill.py::
        # TestCanonQuickActionCeilingAC42 (BOT-001 AC-4.2, <= 5). This test
        # owns the MAX-side rendering — that each button reaches the wire
        # with the right native type.
        #
        # DRF-1200 cut «🍽 Дневник еды» / «💧 Вода» / «❓ Задать вопрос»
        # from the first screen; DRF-1199 had already cut «📊 Анкета».
        assert [row[0]["text"] for row in buttons] == [
            "📅 Записаться",
            "📋 Мои записи",
            "👤 Профиль",
            "❓ Помощь",
            "▶️ Начать",
        ]
        # DRF-963: booking entry is a bot callback, not a Mini App link.
        assert buttons[0][0]["type"] == "callback"
        assert buttons[0][0]["payload"] == "cb:menu:book"
        # Third button is the surviving Mini App link to the profile screen.
        # DRF-1326: was ``/profile``, which is not a route — the client
        # screen is ``/customer/profile``. The base stays a bare domain and
        # the whole path comes from ``MINIAPP_ROUTES``; existence is enforced
        # against App.tsx in apps/skills/welcome/tests/test_miniapp_routes.py.
        assert buttons[2][0]["type"] == "link"
        assert buttons[2][0]["url"] == "https://miniapp-dev.example/customer/profile"
        # The «❓ Помощь» callback — second-to-last (the #85 «Начать»
        # ack button is appended last).
        assert buttons[-2][0]["type"] == "callback"
        assert buttons[-2][0]["payload"] == "cb:menu:help"
        # Last button is the S1→S2 ack «▶️ Начать» (task #85).
        assert buttons[-1][0]["payload"] == "cb:welcome:start_s2"

    def test_callback_tap_runs_welcome_ask_prompt(self, tenant_a, mock_send, fake_redis, settings):
        """User taps «❓ Задать вопрос» — bot replies with the prompt text
        and no keyboard (the FAQ skill takes the next turn)."""
        settings.STRICT_TENANT_SCOPE = "strict"

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(_callback_payload(payload="cb:welcome:ask"))

        sent = mock_send[0]
        # The ASK_PROMPT text is on the reply.
        assert "Спросите" in sent["text"]
        # No keyboard on this turn.
        assert sent["attachments"] is None

    def test_callback_idempotency_keys_off_callback_id(
        self, tenant_a, mock_send, fake_redis, settings
    ):
        """Two webhook deliveries for the same callback_id collapse to
        one outbound — even if PEL retries push the same payload twice."""
        settings.STRICT_TENANT_SCOPE = "strict"

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(_callback_payload(callback_id="same-cb-1"))
            max_handler.handle_max_event(_callback_payload(callback_id="same-cb-1"))

        # Second call short-circuits on AlreadyClaimed.
        assert len(mock_send) == 1


class TestUnsupportedUpdateTypeSkipped:
    """ParseError from unknown update types must NOT poison the PEL.
    Handler logs + emits a skip event + returns cleanly so the consumer
    ACKs. Regression guard for dev incident 2026-05-21 where bot_started
    blocked all subsequent inbound until manual PEL drain.
    """

    def test_bot_started_does_not_raise(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            # Must not raise — handler returns cleanly.
            max_handler.handle_max_event({"update_type": "bot_started"})

        # No outbound, no rows — handler short-circuited.
        assert len(mock_send) == 0

    def test_unknown_update_type_emits_skip_event(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event({"update_type": "message_edited", "message": {}})

        ev = Event.objects.filter(event_type="channels.max.handler.skipped").first()
        assert ev is not None
        assert ev.payload["update_type"] == "message_edited"


class TestOutboundFailure:
    def test_max_api_error_propagates_after_persisting_assistant_message(
        self, tenant_a, fake_redis, settings, monkeypatch
    ):
        """Per D3 docstring: the assistant message is persisted BEFORE
        the send, so a network failure on send doesn't lose the
        intended reply. Test pins both halves: send-fails-raises AND
        assistant message still on record.
        """

        settings.STRICT_TENANT_SCOPE = "strict"

        def fake_send_raises(*, chat_id, text, attachments=None, timeout=10.0):
            raise MaxAPIError(502, "upstream down")

        monkeypatch.setattr(max_handler, "send_message", fake_send_raises)

        from apps.skills.menu.replies import FALLBACK_TEXT

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            _mark_welcomed()  # isolate from the #85 auto-welcome
            with pytest.raises(MaxAPIError):
                max_handler.handle_max_event(_payload(text="hi"))

        # Both user AND assistant message rows exist — assistant was
        # persisted before send, so the record stands even though
        # outbound failed.
        msgs = list(Message.all_tenants.all().order_by("created_at"))
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].content == FALLBACK_TEXT
