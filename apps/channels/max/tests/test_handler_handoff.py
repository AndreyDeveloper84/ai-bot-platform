"""Per-tenant MAX handler should_handoff wiring (#1047, S1-C).

Proves the handler now reads ``SkillResult.should_handoff`` (it previously dropped
it — escalations vanished silently): a skill requesting handoff → AdminTask
created + state HUMAN_HANDOFF + the skill's line sent once + bot silent afterwards.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.orchestrator.memory import short_term
from apps.skills.base import SkillResult
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
    return Tenant.objects.create(slug="handoff-a", name="A")


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
    import apps.channels.max.outbound as outbound

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)


@pytest.fixture
def booking_handoff_dispatch(monkeypatch):
    """Patch the skill registry to return a booking-style handoff SkillResult —
    the exact shape apps/skills/booking/skill.py::_handoff emits on a booking
    failure (should_handoff=True + a «переключаю на менеджера» line)."""
    import apps.skills.registry as registry

    def fake_dispatch(ctx):
        return SkillResult(
            reply_text="Секунду, переключаю на менеджера — он поможет с записью.",
            action_type="booking",
            should_handoff=True,
            handoff_reason="booking_unknown_master",
            meta={"skill": "booking"},
        )

    monkeypatch.setattr(registry, "dispatch", fake_dispatch)


def _run(tenant, text, *, mid="m-1"):
    trace = uuid4()
    with tenant_scope(tenant), trace_id_scope(str(trace)):
        max_handler.handle_max_event(_payload(text=text, mid=mid), trace_id=trace)


class TestShouldHandoff:
    def test_handoff_creates_admin_task_flips_state_sends_line(
        self, tenant_a, mock_send, fake_redis, booking_handoff_dispatch, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        _run(tenant_a, "хочу отменить запись")

        # AdminTask created, type HANDOFF, reason carried from the skill.
        tasks = AdminTask.all_tenants.filter(tenant=tenant_a)
        assert tasks.count() == 1
        task = tasks.first()
        assert task.task_type == AdminTask.TaskType.HANDOFF
        assert task.reason == "booking_unknown_master"

        # Conversation flipped to HUMAN_HANDOFF.
        conv = Conversation.all_tenants.get(tenant=tenant_a)
        assert conv.state == Conversation.State.HUMAN_HANDOFF

        # The skill's user-facing line was sent (once).
        assert len(mock_send) == 1
        assert mock_send[0]["text"] == "Секунду, переключаю на менеджера — он поможет с записью."

    def test_second_turn_is_muted_after_handoff(self, tenant_a, mock_send, fake_redis, settings):
        # After state == HUMAN_HANDOFF the bot must stay silent (real dispatch
        # short-circuits with should_send=False; handler's D3 silence path returns).
        settings.STRICT_TENANT_SCOPE = "strict"
        _run(tenant_a, "привет", mid="m1")  # first contact → creates conversation
        conv = Conversation.all_tenants.get(tenant=tenant_a)
        Conversation.all_tenants.filter(pk=conv.pk).update(state=Conversation.State.HUMAN_HANDOFF)
        mock_send.clear()

        _run(tenant_a, "ещё вопрос", mid="m2")

        assert mock_send == []  # bot silent while operator drives


class TestNoRegression:
    def test_normal_reply_creates_no_task(self, tenant_a, mock_send, fake_redis, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        _run(tenant_a, "привет")
        # Ordinary turn → a reply is sent and NO handoff task appears.
        assert len(mock_send) == 1
        assert AdminTask.all_tenants.count() == 0
