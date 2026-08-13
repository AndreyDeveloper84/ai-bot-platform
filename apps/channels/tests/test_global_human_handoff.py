"""Global-path human handoff (DRF-1015).

On the tenant-less discovery path a request for a human («оператор») used to
fall through to the concierge LLM — no AdminTask, no HUMAN_HANDOFF, the user
had no emergency exit to a person. These tests pin the fix:

* a deterministic trigger BEFORE the concierge LLM creates an AdminTask and
  replies with the handoff line;
* queue addressing (§3 of the brief): with a per-tenant context the task
  lands on the MOST RECENT tenant's conversation; without one it lands on
  the global (sentinel) conversation — the platform queue;
* after escalation the bot stays silent on BOTH dialogs until the task is
  closed (DRF-980 release), then answers again;
* a negated request («мне не нужен оператор») must NOT escalate.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import uuid

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.handoff.services import resolve_admin_task
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser
from apps.orchestrator.memory import short_term
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _payload(*, text: str, user_id: int, chat_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Иван"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run_global(text: str, *, user_id: int = 222, chat_id: int = 222, mid: str) -> None:
    max_handler.handle_global_max_event(
        _payload(text=text, user_id=user_id, chat_id=chat_id, mid=mid),
        trace_id=str(uuid.uuid4()),
    )


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
def spy_concierge(monkeypatch):
    from unittest.mock import MagicMock

    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Какая услуга интересует?"))
    monkeypatch.setattr(max_handler, "generate_concierge_reply", spy)
    return spy


def _global_conversation(user_id: int = 222) -> Conversation:
    bot_user = BotUser.all_tenants.get(
        channel="max",
        channel_user_id=str(user_id),
        tenant__slug=GLOBAL_BOT_TENANT_SLUG,
    )
    return Conversation.all_tenants.get(bot_user=bot_user, tenant__slug=GLOBAL_BOT_TENANT_SLUG)


def _make_tenant_context(slug: str, *, user_id: int = 222, last_message_at) -> Conversation:
    """An active per-tenant dialog for the same channel user (prior salon chat)."""
    tenant = Tenant.objects.create(slug=slug, name=slug.upper())
    with tenant_scope(tenant):
        bot_user = BotUser.objects.create(
            tenant=tenant, channel="max", channel_user_id=str(user_id)
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    Conversation.all_tenants.filter(pk=conv.pk).update(last_message_at=last_message_at)
    conv.refresh_from_db()
    return conv


# --------------------------------------------------------------------------- #
# Acceptance: escalation fires on the global path                              #
# --------------------------------------------------------------------------- #
class TestGlobalHandoffTrigger:
    def test_operator_request_creates_task_and_replies(self, mock_send, fake_redis, spy_concierge):
        _run_global("оператор", mid="h1")

        task = AdminTask.all_tenants.get()
        assert task.task_type == AdminTask.TaskType.HANDOFF
        assert mock_send[-1]["text"] == ("Передаю менеджеру — ответят в течение 30 минут.")
        # The concierge LLM must NOT have answered this turn.
        spy_concierge.assert_not_called()

    def test_no_tenant_context_lands_on_platform_queue(self, mock_send, fake_redis, spy_concierge):
        _run_global("позовите оператора", mid="h2")

        task = AdminTask.all_tenants.get()
        assert task.tenant.slug == GLOBAL_BOT_TENANT_SLUG
        # …and the task anchors the GLOBAL conversation, which is now muted.
        conv = _global_conversation()
        assert task.conversation_id == conv.id
        assert conv.state == Conversation.State.HUMAN_HANDOFF


# --------------------------------------------------------------------------- #
# Acceptance: the bot actually shuts up, on both dialogs                       #
# --------------------------------------------------------------------------- #
class TestGlobalHandoffMute:
    def test_bot_silent_after_escalation_until_task_closed(
        self, mock_send, fake_redis, spy_concierge
    ):
        _run_global("оператор", mid="m1")
        assert len(mock_send) == 1  # the handoff confirmation

        _run_global("вы тут?", mid="m2")
        assert len(mock_send) == 1  # silent — operator is driving
        spy_concierge.assert_not_called()

        # DRF-980 close → bot answers again (end-to-end with release).
        task = AdminTask.all_tenants.get()
        with tenant_scope(task.tenant):
            resolve_admin_task(task, resolution_note="done")

        _run_global("спасибо, уже не надо", mid="m3")
        assert len(mock_send) == 2
        assert mock_send[-1]["text"] == "Какая услуга интересует?"
        assert spy_concierge.call_count == 1

    def test_repeat_operator_request_during_handoff_creates_no_duplicate(
        self, mock_send, fake_redis, spy_concierge
    ):
        _run_global("оператор", mid="d1")
        _run_global("оператор!!", mid="d2")
        assert AdminTask.all_tenants.count() == 1
        assert len(mock_send) == 1


# --------------------------------------------------------------------------- #
# Acceptance: §3 queue addressing with a tenant context                        #
# --------------------------------------------------------------------------- #
class TestQueueAddressing:
    def test_task_goes_to_most_recent_tenant_context(
        self, mock_send, fake_redis, spy_concierge, settings
    ):

        older = _make_tenant_context(
            "salon-old", last_message_at=datetime(2026, 8, 1, tzinfo=dt_timezone.utc)
        )
        newer = _make_tenant_context(
            "salon-new", last_message_at=datetime(2026, 8, 10, tzinfo=dt_timezone.utc)
        )

        _run_global("оператор", mid="t1")

        task = AdminTask.all_tenants.get()
        assert task.tenant_id == newer.tenant_id
        assert task.conversation_id == newer.id
        # The tenant dialog is flipped by create_admin_task…
        newer.refresh_from_db()
        older.refresh_from_db()
        assert newer.state == Conversation.State.HUMAN_HANDOFF
        assert older.state != Conversation.State.HUMAN_HANDOFF
        # …and the user gets the handoff reply on the global chat.
        assert mock_send[-1]["text"] == ("Передаю менеджеру — ответят в течение 30 минут.")

    def test_global_dialog_muted_when_task_went_to_tenant(
        self, mock_send, fake_redis, spy_concierge, settings
    ):

        conv = _make_tenant_context(
            "salon-mute", last_message_at=datetime(2026, 8, 10, tzinfo=dt_timezone.utc)
        )
        _run_global("оператор", mid="u1")
        assert len(mock_send) == 1

        # The GLOBAL conversation itself is not in HUMAN_HANDOFF (the task
        # anchors the tenant dialog) — the mute comes from the open task.
        assert _global_conversation().state != Conversation.State.HUMAN_HANDOFF

        _run_global("алло", mid="u2")
        assert len(mock_send) == 1  # still silent on the global path
        spy_concierge.assert_not_called()

        # Closing the TENANT task releases the global path too.
        task = AdminTask.all_tenants.get()
        with tenant_scope(task.tenant):
            resolve_admin_task(task, resolution_note="done")
        conv.refresh_from_db()
        assert conv.state != Conversation.State.HUMAN_HANDOFF

        _run_global("привет", mid="u3")
        assert len(mock_send) == 2
        assert spy_concierge.call_count == 1


# --------------------------------------------------------------------------- #
# Acceptance: false-positive guard                                             #
# --------------------------------------------------------------------------- #
class TestFalsePositives:
    def test_negated_request_does_not_escalate(self, mock_send, fake_redis, spy_concierge):
        _run_global("мне не нужен оператор", mid="n1")

        assert AdminTask.all_tenants.count() == 0
        assert mock_send[-1]["text"] == "Какая услуга интересует?"
        spy_concierge.assert_called_once()


# --------------------------------------------------------------------------- #
# Acceptance: DRF-972 — русские формулировки эскалируют на глобальном пути     #
# --------------------------------------------------------------------------- #
class TestRussianKeywordsOnGlobalPath:
    """``matches_human_handoff_request`` imports ``_HANDOFF_KEYWORDS`` from the
    tenant skill (never a copy) — DRF-972's dictionary extension must reach
    this route for free. These pin that the shared import actually works end
    to end, not just at the unit level."""

    def test_menedzher_escalates(self, mock_send, fake_redis, spy_concierge):
        _run_global("позовите менеджера", mid="g1")

        assert AdminTask.all_tenants.count() == 1
        assert mock_send[-1]["text"] == ("Передаю менеджеру — ответят в течение 30 минут.")
        spy_concierge.assert_not_called()

    def test_administrator_escalates(self, mock_send, fake_redis, spy_concierge):
        _run_global("нужен администратор", mid="g2")

        assert AdminTask.all_tenants.count() == 1
        spy_concierge.assert_not_called()

    def test_human_word_not_added_no_false_escalation(self, mock_send, fake_redis, spy_concierge):
        """«человек» сознательно не в словаре — бытовая фраза не должна
        создавать задачу оператору."""
        _run_global("я человек занятой, давайте по делу", mid="g3")

        assert AdminTask.all_tenants.count() == 0
        spy_concierge.assert_called_once()
