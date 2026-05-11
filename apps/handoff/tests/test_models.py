"""AdminTask model tests (DRF-464 / Sprint 3 / C1)."""

from __future__ import annotations

import pytest

from apps.conversations.models import Conversation
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="ht-a", name="HA")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="ht-bot-1")


@pytest.fixture
def conversation(tenant, bot_user) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


class TestAdminTaskShape:
    def test_defaults(self, tenant, bot_user, conversation, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            task = AdminTask.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                conversation=conversation,
                task_type=AdminTask.TaskType.HANDOFF,
            )
        task.refresh_from_db()
        assert task.priority == AdminTask.Priority.NORMAL
        assert task.status == AdminTask.Status.OPEN
        assert task.assigned_to is None
        assert task.transcript_snapshot == {}
        assert task.resolved_at is None
        assert task.reason == ""
        assert task.resolution_note == ""

    def test_task_type_choices_cover_phase0(self, tenant, bot_user, conversation, settings):
        """4 Phase-0 trigger sources: HANDOFF, COMPLAINT, MEDICAL_RED_FLAG, MANUAL."""

        settings.STRICT_TENANT_SCOPE = "strict"
        types = {
            AdminTask.TaskType.HANDOFF,
            AdminTask.TaskType.COMPLAINT,
            AdminTask.TaskType.MEDICAL_RED_FLAG,
            AdminTask.TaskType.MANUAL,
        }
        with tenant_scope(tenant):
            for t in types:
                AdminTask.objects.create(
                    tenant=tenant,
                    bot_user=bot_user,
                    conversation=conversation,
                    task_type=t,
                )
        assert AdminTask.all_tenants.count() == 4

    def test_indexes_present(self):
        index_fields = {tuple(idx.fields) for idx in AdminTask._meta.indexes}
        assert ("tenant", "status", "-created_at") in index_fields
        assert ("assigned_to", "status") in index_fields

    def test_str_carries_type_priority_status(self, tenant, bot_user, conversation):
        task = AdminTask(
            tenant=tenant,
            bot_user=bot_user,
            conversation=conversation,
            task_type=AdminTask.TaskType.COMPLAINT,
            priority=AdminTask.Priority.HIGH,
            status=AdminTask.Status.IN_PROGRESS,
        )
        s = str(task)
        assert "complaint" in s
        assert "high" in s
        assert "in_progress" in s


class TestTenantScoping:
    def test_default_manager_filters_to_current_tenant(
        self, tenant, bot_user, conversation, settings
    ):
        other = Tenant.objects.create(slug="ht-b", name="HB")
        other_bu = BotUser.all_tenants.create(
            tenant=other, channel="max", channel_user_id="ht-other"
        )
        other_conv = Conversation.all_tenants.create(tenant=other, bot_user=other_bu)

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            AdminTask.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                conversation=conversation,
                task_type=AdminTask.TaskType.HANDOFF,
            )
        with tenant_scope(other):
            AdminTask.objects.create(
                tenant=other,
                bot_user=other_bu,
                conversation=other_conv,
                task_type=AdminTask.TaskType.MANUAL,
            )

        with tenant_scope(tenant):
            assert AdminTask.objects.count() == 1
            assert AdminTask.objects.first().task_type == AdminTask.TaskType.HANDOFF
        with tenant_scope(other):
            assert AdminTask.objects.count() == 1
            assert AdminTask.objects.first().task_type == AdminTask.TaskType.MANUAL

        # Escape hatch: all_tenants sees both.
        assert AdminTask.all_tenants.count() == 2


class TestFKProtect:
    def test_bot_user_protect_blocks_delete(self, tenant, bot_user, conversation, settings):
        """BotUser PROTECT — open AdminTask must block hard-delete."""

        from django.db.models import ProtectedError

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            AdminTask.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                conversation=conversation,
                task_type=AdminTask.TaskType.HANDOFF,
            )
        with pytest.raises(ProtectedError):
            bot_user.delete()

    def test_tenant_protect_blocks_delete(self, tenant, bot_user, conversation, settings):
        from django.db.models import ProtectedError

        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            AdminTask.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                conversation=conversation,
                task_type=AdminTask.TaskType.HANDOFF,
            )
        with pytest.raises(ProtectedError):
            tenant.delete()
