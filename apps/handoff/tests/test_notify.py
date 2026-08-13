"""Handoff → MAX escalation notification tests (DRF-1029).

Covers the acceptance criteria from the DRF-1029 brief §3:

* configured → send happened, recipient + text match the setting;
* setting empty → no send at all, no warning-level log noise;
* send failure (MaxAPIError) → task still created, conversation still
  HUMAN_HANDOFF, exception never escapes;
* send happens AFTER the transaction commits (rollback → no send);
* text carries NO client phone and NO transcript;
* multiple recipients → each gets the message; one failing recipient
  does not cancel the others;
* HIGH/URGENT priority is visually distinct in the text.
"""

from __future__ import annotations

import pytest

from apps.audit.models import AuditLog
from apps.channels.max.outbound import MaxAPIError
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask
from apps.handoff.notify import build_admin_task_notification
from apps.handoff.services import create_admin_task
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

from django.db import transaction

pytestmark = pytest.mark.django_db(transaction=True)

NOTIFY_SEND = "apps.handoff.notify.send_message"


class SendRecorder:
    """Stand-in for channels.max.outbound.send_message.

    Records every call; replays ``side_effects`` (list, one per call, or
    a single exception instance) so tests can simulate MAX failures.
    """

    def __init__(self, side_effects=None):
        self.calls: list[dict] = []
        self.side_effects = side_effects

    def __call__(self, *, chat_id, text, attachments=None, timeout=10.0):
        self.calls.append({"chat_id": chat_id, "text": text, "timeout": timeout})
        effects = self.side_effects
        if isinstance(effects, list):
            effect = effects[min(len(self.calls), len(effects)) - 1]
            if isinstance(effect, Exception):
                raise effect
            return effect
        if isinstance(effects, Exception):
            raise effects
        return {}


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="hs-ntf", name="HSvc Notify Salon")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="hs-ntf-bot",
        display_name="Ivan Test",
        phone="+79991234567",
    )


@pytest.fixture
def conversation(tenant, bot_user) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


@pytest.fixture
def send(monkeypatch):
    recorder = SendRecorder()
    monkeypatch.setattr(NOTIFY_SEND, recorder)
    return recorder


def _configure(settings, chat_ids, admin_base="https://admin.example"):
    settings.HANDOFF_NOTIFY_MAX_CHAT_IDS = chat_ids
    settings.HANDOFF_ADMIN_BASE_URL = admin_base
    settings.STRICT_TENANT_SCOPE = "strict"


def _create(tenant, conversation, **kwargs):
    kwargs.setdefault("task_type", AdminTask.TaskType.HANDOFF)
    with tenant_scope(tenant):
        return create_admin_task(conversation, **kwargs)


class TestNotifyDisabled:
    def test_no_send_when_setting_empty(self, tenant, bot_user, conversation, settings, send):
        """Empty HANDOFF_NOTIFY_MAX_CHAT_IDS → mechanism fully off (§3.1)."""

        _configure(settings, [])
        _create(tenant, conversation, reason="user asked")
        assert send.calls == []

    def test_no_warning_logs_when_disabled(
        self, tenant, bot_user, conversation, settings, send, caplog
    ):
        _configure(settings, [])
        with caplog.at_level("DEBUG", logger="apps.handoff.notify"):
            _create(tenant, conversation, reason="user asked")
        noisy = [
            r
            for r in caplog.records
            if r.name == "apps.handoff.notify" and r.levelno >= 30  # WARNING+
        ]
        assert noisy == []


class TestNotifySent:
    def test_send_on_create_when_configured(self, tenant, bot_user, conversation, settings, send):
        _configure(settings, ["111222"])
        task = _create(tenant, conversation, reason="user asked for human")
        assert len(send.calls) == 1
        call = send.calls[0]
        assert call["chat_id"] == "111222"
        text = call["text"]
        assert "HSvc Notify Salon" in text
        assert "user asked for human" in text
        assert str(task.id) in text
        assert str(conversation.id) in text
        # Direct admin link built from the configured base (§3.5).
        assert f"https://admin.example/admin/handoff/admintask/{task.id}/change/" in text
        # Short timeout — never block the consumer (§3.4: <= 5s).
        assert call["timeout"] <= 5.0

    def test_no_link_line_when_admin_base_empty(
        self, tenant, bot_user, conversation, settings, monkeypatch
    ):
        _configure(settings, ["111222"], admin_base="")
        recorder = SendRecorder()
        monkeypatch.setattr(NOTIFY_SEND, recorder)
        _create(tenant, conversation, reason="x")
        assert "/admin/handoff/" not in recorder.calls[0]["text"]

    def test_text_has_no_phone_no_transcript(self, tenant, bot_user, conversation, settings, send):
        """Minimum-PII rule (§3.5): no client phone, no transcript lines."""

        _configure(settings, ["111222"])
        Message.all_tenants.create(
            tenant=tenant,
            conversation=conversation,
            role="user",
            content="мой секретный транскрипт",
        )
        _create(tenant, conversation, reason="escalated")
        text = send.calls[0]["text"]
        assert "+79991234567" not in text
        assert "мой секретный транскрипт" not in text

    def test_high_priority_marked(self, tenant, bot_user, conversation, settings, send):
        """HIGH-priority task must read differently from a routine one (§3.6)."""

        _configure(settings, ["111222"])
        _create(
            tenant,
            conversation,
            task_type=AdminTask.TaskType.COMPLAINT,
            priority=AdminTask.Priority.HIGH,
            reason="bad visit rating",
        )
        assert "HIGH" in send.calls[0]["text"]

    def test_normal_priority_not_marked_high(self, tenant, bot_user, conversation, settings, send):
        _configure(settings, ["111222"])
        _create(tenant, conversation, reason="routine")
        text = send.calls[0]["text"]
        assert "HIGH" not in text
        assert "URGENT" not in text

    def test_multiple_recipients_each_gets_message(
        self, tenant, bot_user, conversation, settings, send
    ):
        _configure(settings, ["111", "222", "333"])
        _create(tenant, conversation, reason="multi")
        assert [c["chat_id"] for c in send.calls] == ["111", "222", "333"]

    def test_failure_on_one_recipient_does_not_stop_others(
        self, tenant, bot_user, conversation, settings, monkeypatch
    ):
        _configure(settings, ["bad", "good"])
        recorder = SendRecorder(side_effects=[MaxAPIError(500, "boom"), {}])
        monkeypatch.setattr(NOTIFY_SEND, recorder)
        task = _create(tenant, conversation, reason="partial failure")
        assert [c["chat_id"] for c in recorder.calls] == ["bad", "good"]
        task.refresh_from_db()
        assert task.status == AdminTask.Status.OPEN


class TestNotifyBestEffort:
    def test_send_failure_does_not_break_creation(
        self, tenant, bot_user, conversation, settings, monkeypatch
    ):
        """§3.3: MaxAPIError must never break task creation or escape."""

        _configure(settings, ["111222"])
        monkeypatch.setattr(NOTIFY_SEND, SendRecorder(side_effects=MaxAPIError(500, "max is down")))
        task = _create(tenant, conversation, reason="send will fail")
        task.refresh_from_db()
        conversation.refresh_from_db()
        assert task.status == AdminTask.Status.OPEN
        assert conversation.state == "human_handoff"
        # Failure is audited for operator visibility.
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="handoff.notify_failed", target_id=task.id
        ).exists()

    def test_unexpected_exception_also_contained(
        self, tenant, bot_user, conversation, settings, monkeypatch
    ):
        _configure(settings, ["111222"])
        monkeypatch.setattr(
            NOTIFY_SEND, SendRecorder(side_effects=RuntimeError("totally unexpected"))
        )
        task = _create(tenant, conversation, reason="surprise")
        task.refresh_from_db()
        assert task.status == AdminTask.Status.OPEN


class TestNotifyAfterCommit:
    def test_no_notification_on_rollback(self, tenant, bot_user, conversation, settings, send):
        """§3.2: notification fires on_commit; a rolled-back task never notifies."""

        _configure(settings, ["111222"])
        with pytest.raises(RuntimeError, match="force rollback"):
            with transaction.atomic():
                _create(tenant, conversation, reason="will roll back")
                raise RuntimeError("force rollback")
        assert send.calls == []
        assert not AdminTask.all_tenants.filter(tenant=tenant).exists()

    def test_notification_after_successful_commit(
        self, tenant, bot_user, conversation, settings, send
    ):
        """Same shape, but the outer transaction commits → send happens."""

        _configure(settings, ["111222"])
        with transaction.atomic():
            task = _create(tenant, conversation, reason="committed")
        # Callback ran at the OUTER commit — after this line the send
        # must already have happened exactly once.
        assert len(send.calls) == 1
        assert send.calls[0]["chat_id"] == "111222"
        assert str(task.id) in send.calls[0]["text"]


class TestBuildNotificationNoQueries:
    def test_build_text_makes_zero_db_queries(
        self, tenant, bot_user, conversation, settings, django_assert_num_queries
    ):
        """Building the notification text must touch the DB ZERO times.

        The on_commit callback may run outside ``tenant_scope``, and on the
        pilot STRICT_TENANT_SCOPE is unset (audit mode), where a
        tenant-scoped queryset without context silently returns emptiness
        instead of raising (apps/tenancy/managers.py). A query added to the
        formatter would not fail loudly in production — it would quietly
        render wrong data. Every value in the text comes from attributes
        and FKs cached at AdminTask creation; this test turns that into a
        rule rather than a lucky property of the current implementation.
        """

        _configure(settings, ["111222"])
        task = _create(tenant, conversation, reason="zero queries")
        with django_assert_num_queries(0):
            text = build_admin_task_notification(task)
        assert "HSvc Notify Salon" in text
        assert str(task.id) in text
        assert str(conversation.id) in text
