"""Per-tenant MAX handler safety pre-check integration (#1053, S1-B).

Proves the red-flag / BLOCK short-circuit fires on the LIVE per-tenant path
(`_handle_max_event_inner`) BEFORE skill dispatch, that S1-B creates NO AdminTask
and does NOT flip HUMAN_HANDOFF (that is S1-C, #1047), and that a normal message
still flows through as before.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import BLOCK_REPLY_TEXT, CRISIS_REPLY_TEXT
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _payload(*, text, user_id=12345, chat_id=67890, mid="m-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


@pytest.fixture
def tenant_a() -> Tenant:
    return Tenant.objects.create(slug="safety-a", name="A")


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_chat_action(monkeypatch):
    # send_chat_action fires a live HTTP call at the top of the per-tenant handler.
    import apps.channels.max.outbound as outbound

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)


def _run(tenant, text):
    trace = uuid4()
    with tenant_scope(tenant), trace_id_scope(str(trace)):
        max_handler.handle_max_event(_payload(text=text), trace_id=trace)


class TestRedFlagShortCircuit:
    def test_suicide_phrase_returns_crisis_reply_not_skill(
        self, tenant_a, mock_send, fake_redis, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        _run(tenant_a, "я думаю о суициде")

        # Crisis reply sent — NOT echo / welcome / FAQ.
        assert len(mock_send) == 1
        assert mock_send[0]["text"] == CRISIS_REPLY_TEXT

        # Exactly 2 messages (user + crisis assistant); skill never ran.
        conv = Conversation.all_tenants.get(tenant=tenant_a)
        msgs = list(Message.all_tenants.filter(conversation=conv).order_by("created_at"))
        assert len(msgs) == 2
        assert msgs[1].role == "assistant"
        assert msgs[1].content == CRISIS_REPLY_TEXT
        assert msgs[1].action_type == "safety_pre_check"

    def test_no_admin_task_and_state_not_handoff(self, tenant_a, mock_send, fake_redis, settings):
        # S1-B is detection only — the AdminTask + HUMAN_HANDOFF flip is S1-C.
        settings.STRICT_TENANT_SCOPE = "strict"
        _run(tenant_a, "хочу убить себя")

        assert AdminTask.all_tenants.count() == 0
        conv = Conversation.all_tenants.get(tenant=tenant_a)
        assert conv.state == Conversation.State.IDLE

    def test_block_phrase_returns_block_reply(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        _run(tenant_a, "посоветуйте ибупрофен от боли")

        assert len(mock_send) == 1
        assert mock_send[0]["text"] == BLOCK_REPLY_TEXT
        assert AdminTask.all_tenants.count() == 0


class TestHappyPathRegression:
    def test_normal_message_not_short_circuited(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        _run(tenant_a, "хочу записаться на массаж")

        assert len(mock_send) == 1
        # Whatever the skill returns (welcome / echo), it is NOT a safety reply.
        assert mock_send[0]["text"] not in (CRISIS_REPLY_TEXT, BLOCK_REPLY_TEXT)
