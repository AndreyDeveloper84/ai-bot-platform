"""End-to-end tenant-less discovery turn (#1026 / EPIC #1014).

A client messages the global bot at current_tenant()=None → identity resolved
under the sentinel → user turn persisted → discovery reply (mocked LLM) → reply
persisted + sent → re-read on a second turn confirms cross-turn persistence,
all without leaking a tenant scope. The LLM + outbound + Redis are mocked.
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation, Message
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import current_tenant

pytestmark = pytest.mark.django_db


def _payload(*, text: str, user_id: int, chat_id: int, mid: str = "m-1") -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _raw_entry(payload: dict) -> dict:
    return {
        "data": json.dumps(payload),
        "trace_id": str(uuid.uuid4()),
        "resolved_tenant_id": "",
    }


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


@pytest.fixture
def mock_discovery(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    replies = iter(["Привет! Какая услуга интересует?", "Поняла — подберу мастера."])

    def fake_reply(message_text, **_kwargs):
        try:
            return DiscoveryReply(text=next(replies))
        except StopIteration:
            return DiscoveryReply(text="…")

    monkeypatch.setattr(max_handler, "generate_concierge_reply", fake_reply)


def test_discovery_turn_persists_under_sentinel_and_rereads(
    settings, mock_send, fake_redis, mock_discovery
) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    uid, cid = 9999, 5555

    # ── Turn 1 ──
    GlobalMaxHandler()(
        _raw_entry(_payload(text="Привет из открытого мира", user_id=uid, chat_id=cid, mid="t1"))
    )

    bot_user = BotUser.all_tenants.get(channel="max", channel_user_id=str(uid))
    assert bot_user.tenant.slug == GLOBAL_BOT_TENANT_SLUG

    conv = Conversation.all_tenants.get(bot_user=bot_user, is_active=True)
    assert conv.tenant.slug == GLOBAL_BOT_TENANT_SLUG  # sentinel-scoped

    msgs = list(Message.all_tenants.filter(conversation=conv).order_by("created_at"))
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "Привет из открытого мира"
    assert msgs[1].content == "Привет! Какая услуга интересует?"
    assert all(m.tenant_id == bot_user.tenant_id for m in msgs)  # message tenant == sentinel
    assert len(mock_send) == 1 and mock_send[0]["chat_id"] == str(cid)
    assert current_tenant() is None  # no scope leaked

    # ── Turn 2 (same user) → same conversation, appended ──
    mock_send.clear()
    GlobalMaxHandler()(_raw_entry(_payload(text="Маникюр", user_id=uid, chat_id=cid, mid="t2")))

    conv2 = Conversation.all_tenants.get(bot_user=bot_user, is_active=True)
    assert conv2.id == conv.id  # re-used, not a new thread
    all_msgs = list(Message.all_tenants.filter(conversation=conv2).order_by("created_at"))
    assert len(all_msgs) == 4
    assert all_msgs[2].role == "user" and all_msgs[2].content == "Маникюр"
    assert len(mock_send) == 1


def test_discovery_turn_is_idempotent_on_redelivery(
    settings, mock_send, fake_redis, mock_discovery
) -> None:
    """PEL retry (same mid) → no double-send, no duplicate messages."""
    settings.STRICT_TENANT_SCOPE = "strict"
    uid, cid = 8888, 4444
    payload = _payload(text="Привет", user_id=uid, chat_id=cid, mid="dup-1")

    GlobalMaxHandler()(_raw_entry(payload))
    GlobalMaxHandler()(_raw_entry(payload))  # redelivery → AlreadyClaimed short-circuit

    assert len(mock_send) == 1
    bot_user = BotUser.all_tenants.get(channel="max", channel_user_id=str(uid))
    conv = Conversation.all_tenants.get(bot_user=bot_user)
    assert Message.all_tenants.filter(conversation=conv).count() == 2
