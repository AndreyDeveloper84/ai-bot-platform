"""Handoff services tests (DRF-465 / Sprint 3 / C2)."""

from __future__ import annotations

import hashlib

import pytest

from apps.audit.models import AuditLog
from apps.conversations.models import Conversation, Message
from apps.events.models import Event
from apps.handoff.models import AdminTask
from apps.handoff.services import (
    create_admin_task,
    package_transcript,
    resolve_admin_task,
)
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="hs-svc", name="HSvc")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="hs-svc-bot",
        display_name="Ivan Test",
        phone="+79991234567",
    )


@pytest.fixture
def conversation(tenant, bot_user) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


def _make_message(tenant, conversation, role, content):
    return Message.all_tenants.create(
        tenant=tenant, conversation=conversation, role=role, content=content
    )


class TestPackageTranscript:
    def test_snapshot_has_required_shape(self, tenant, bot_user, conversation):
        _make_message(tenant, conversation, "user", "hi")
        _make_message(tenant, conversation, "assistant", "hello")
        snap = package_transcript(conversation, max_messages=5)
        assert "captured_at" in snap
        assert snap["max_messages"] == 5
        assert snap["conversation"]["id"] == str(conversation.id)
        assert snap["conversation"]["state"] == "idle"
        assert snap["bot_user"]["channel"] == "max"
        assert snap["bot_user"]["channel_user_id"] == "hs-svc-bot"
        assert snap["bot_user"]["display_name"] == "Ivan Test"
        assert len(snap["messages"]) == 2

    def test_phone_hashed_never_raw(self, tenant, bot_user, conversation):
        """The PII rule: raw phone must not appear anywhere in the snapshot."""

        snap = package_transcript(conversation)
        # Hash matches sha256 of the raw phone.
        expected_hash = hashlib.sha256(b"+79991234567").hexdigest()
        assert snap["bot_user"]["phone_hash"] == expected_hash
        # And raw phone does not leak.
        assert "+79991234567" not in str(snap)

    def test_empty_phone_yields_empty_hash(self, tenant):
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="no-phone", phone=""
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
        snap = package_transcript(conv)
        # Honestly empty, not hash of empty string.
        assert snap["bot_user"]["phone_hash"] == ""

    def test_message_order_oldest_to_newest(self, tenant, bot_user, conversation):
        _make_message(tenant, conversation, "user", "first")
        _make_message(tenant, conversation, "assistant", "second")
        _make_message(tenant, conversation, "user", "third")
        snap = package_transcript(conversation, max_messages=10)
        contents = [m["content"] for m in snap["messages"]]
        assert contents == ["first", "second", "third"]

    def test_max_messages_trims(self, tenant, bot_user, conversation):
        for i in range(8):
            _make_message(tenant, conversation, "user", f"msg-{i}")
        snap = package_transcript(conversation, max_messages=3)
        # Last 3 chronologically.
        assert [m["content"] for m in snap["messages"]] == [
            "msg-5",
            "msg-6",
            "msg-7",
        ]


class TestCreateAdminTask:
    def test_flips_conversation_state_to_handoff(self, tenant, bot_user, conversation, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        assert conversation.state == Conversation.State.IDLE
        with tenant_scope(tenant):
            task = create_admin_task(
                conversation, task_type=AdminTask.TaskType.HANDOFF, reason="user asked"
            )
        conversation.refresh_from_db()
        assert conversation.state == "human_handoff"
        assert task.transcript_snapshot["conversation"]["id"] == str(conversation.id)
        assert task.reason == "user asked"

    def test_emits_handoff_initiated_event_and_audit(
        self, tenant, bot_user, conversation, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            task = create_admin_task(conversation, task_type=AdminTask.TaskType.COMPLAINT)
        # Canonical event landed.
        ev = Event.objects.get(tenant=tenant, event_name="handoff_initiated")
        assert ev.properties["task_id"] == str(task.id)
        assert ev.properties["task_type"] == "complaint"
        # Audit row written.
        assert AuditLog.all_tenants.filter(tenant=tenant, action="handoff.created").exists()

    def test_snapshot_pii_safe(self, tenant, bot_user, conversation, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            task = create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)
        # Raw phone NEVER in the snapshot or in the event row.
        snap_str = str(task.transcript_snapshot)
        assert "+79991234567" not in snap_str
        ev = Event.objects.get(tenant=tenant, event_name="handoff_initiated")
        assert "+79991234567" not in str(ev.properties)

    def test_missing_tenant_context_raises(self, conversation, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        with pytest.raises(ValueError, match="tenant in scope"):
            create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)


class TestResolveAdminTask:
    def test_flips_state_back_to_idle(self, tenant, bot_user, conversation, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            task = create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)
            resolve_admin_task(task, resolution_note="ok, handled")
        conversation.refresh_from_db()
        assert conversation.state == "idle"
        task.refresh_from_db()
        assert task.status == AdminTask.Status.RESOLVED
        assert task.resolved_at is not None
        assert task.resolution_note == "ok, handled"

    def test_idempotent_on_already_resolved(self, tenant, bot_user, conversation, settings):
        """Second resolve must not clobber resolution_note or resolved_at."""

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            task = create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)
            resolve_admin_task(task, resolution_note="first")
        first_resolved_at = task.resolved_at
        first_note = task.resolution_note

        with tenant_scope(tenant):
            resolve_admin_task(task, resolution_note="second clobber")
        task.refresh_from_db()
        # Note + timestamp from the first call survive.
        assert task.resolution_note == first_note
        assert task.resolved_at == first_resolved_at

    def test_writes_handoff_resolved_audit(self, tenant, bot_user, conversation, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            task = create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)
            resolve_admin_task(task, resolution_note="done")
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="handoff.resolved", target_id=task.id
        ).exists()
