"""MAX handler ↔ skill dispatcher e2e tests (DRF-471 / Sprint 3 / D4).

Drives the same handler entry point as Sprint 2 D5 tests, but
exercises the new skill-routed paths:
  - "оператор" → HumanHandoffSkill → AdminTask + state flipped
  - HUMAN_HANDOFF state → bot silent (no record, no send)
  - resolve_admin_task → bot resumes echo
  - skill new_state propagates onto Conversation.state via UPDATE

Reuses the Sprint 2 mocks (_FakeRedis, fake_send) so we keep the
same shape across handler-level tests.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask
from apps.handoff.services import resolve_admin_task
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


def _payload(*, text, user_id=10001, chat_id=20001, mid="hs-1"):
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ivan"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="hh-handler", name="HH")


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


class TestHandoffTrigger:
    def test_operator_phrase_creates_admin_task_flips_state(
        self, tenant, mock_send, fake_redis, settings, mark_welcomed
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        trace = uuid4()
        with tenant_scope(tenant), trace_id_scope(str(trace)):
            mark_welcomed(user_id=10001, chat_id=20001)  # isolate from #85 auto-welcome
            max_handler.handle_max_event(_payload(text="оператор"), trace_id=trace)

        # AdminTask materialised.
        bu = BotUser.all_tenants.get(channel="max", channel_user_id="10001")
        conv = Conversation.all_tenants.get(bot_user=bu)
        assert AdminTask.all_tenants.filter(conversation=conv).count() == 1
        # State flipped.
        conv.refresh_from_db()
        assert conv.state == "human_handoff"
        # Reply sent once with the handoff text.
        assert len(mock_send) == 1
        assert "менеджеру" in mock_send[0]["text"]


class TestSilenceUnderHandoff:
    def test_followup_message_in_handoff_state_silent(
        self, tenant, mock_send, fake_redis, settings, mark_welcomed
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=10001, chat_id=20001)  # isolate from #85 auto-welcome
            # Trigger handoff first.
            max_handler.handle_max_event(_payload(text="оператор", mid="m1"))
            assert len(mock_send) == 1

            # Second message in mid-handoff — no record, no send.
            max_handler.handle_max_event(_payload(text="есть кто живой?", mid="m2"))

        bu = BotUser.all_tenants.get(channel="max", channel_user_id="10001")
        conv = Conversation.all_tenants.get(bot_user=bu)
        # 3 messages from the first turn (user + assistant — handoff reply),
        # PLUS the second turn's user message (recorded BEFORE the silent
        # short-circuit in the handler) — but NO assistant for the second.
        msgs = list(Message.all_tenants.filter(conversation=conv).order_by("created_at"))
        # turn 1: user("оператор") + assistant(handoff reply) = 2
        # turn 2: user("есть кто живой?") only = 1 → total 3
        assert len(msgs) == 3
        roles = [m.role for m in msgs]
        # Only one assistant message — the handoff reply.
        assert roles.count("assistant") == 1
        # Send was called exactly once (handoff reply, not the silent followup).
        assert len(mock_send) == 1


class TestResumeAfterResolve:
    def test_bot_resumes_after_resolve_admin_task(
        self, tenant, mock_send, fake_redis, settings, mark_welcomed
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant), trace_id_scope(str(uuid4())):
            mark_welcomed(user_id=10001, chat_id=20001)  # isolate from #85 auto-welcome
            max_handler.handle_max_event(_payload(text="оператор", mid="r1"))
            bu = BotUser.all_tenants.get(channel="max", channel_user_id="10001")
            conv = Conversation.all_tenants.get(bot_user=bu)
            task = AdminTask.all_tenants.get(conversation=conv)
            resolve_admin_task(task, resolution_note="done")
            # Next message — should echo normally now.
            max_handler.handle_max_event(_payload(text="привет снова", mid="r2"))

        # 2 sends: handoff reply + echo of "привет снова".
        assert len(mock_send) == 2
        assert mock_send[1]["text"] == "привет снова"
        # State back to idle.
        conv.refresh_from_db()
        assert conv.state == "idle"
