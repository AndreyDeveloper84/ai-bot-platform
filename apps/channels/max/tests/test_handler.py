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


class TestHappyPath:
    def test_echo_text_creates_full_chain(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        trace = uuid4()
        with tenant_scope(tenant_a), trace_id_scope(str(trace)):
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
        assert msgs[1].content == "Привет"
        assert msgs[1].trace_id == trace

        # send_message called once with the echoed text.
        assert len(mock_send) == 1
        assert mock_send[0]["chat_id"] == "67890"
        assert mock_send[0]["text"] == "Привет"


class TestWelcomeBranch:
    def test_slash_start_triggers_welcome(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(_payload(text="/start"))

        sent = mock_send[0]["text"]
        assert "Формула тела" in sent
        assert "Помогу записаться" in sent
        # Assistant message body matches what we sent.
        assistant_msg = Message.all_tenants.get(role="assistant")
        assert assistant_msg.content == sent
        assert assistant_msg.rendered_text == sent


class TestAttachmentOnly:
    def test_attachment_only_replies_with_no_echo_fallback(
        self, tenant_a, mock_send, fake_redis, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            max_handler.handle_max_event(
                _payload(
                    text="",
                    attachments=[{"type": "image", "payload": {"url": "x"}}],
                )
            )
        assert mock_send[0]["text"] == "(нечем эхом) 🙂"


class TestEmptyMessage:
    def test_empty_text_no_attachments_returns_question_mark(
        self, tenant_a, mock_send, fake_redis, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
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

        with tenant_scope(tenant_a), trace_id_scope(str(uuid4())):
            with pytest.raises(MaxAPIError):
                max_handler.handle_max_event(_payload(text="hi"))

        # Both user AND assistant message rows exist — assistant was
        # persisted before send, so the record stands even though
        # outbound failed.
        msgs = list(Message.all_tenants.all().order_by("created_at"))
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert msgs[1].content == "hi"
