"""AdminTaskAdmin.save_model close-path tests (DRF-980).

Before DRF-980 the admin stamped ``resolved_at`` on a RESOLVED flip but
never called the handoff service — the conversation stayed in
HUMAN_HANDOFF and the bot was muted forever. These tests pin the
service-layer path:

* RESOLVED via admin → conversation back to IDLE (+ stamps + audit).
* CANCELLED via admin → conversation back to IDLE (resolved_at stays NULL).
* Re-saving an already-closed task heals a stuck-muted conversation
  (the exact trap hit when the status was flipped directly via ORM).
* Closing works cross-tenant (admin spans tenants) without leaking the
  tenant scope.
* A second open task on the same conversation keeps the bot muted until
  the LAST task closes.
"""

from __future__ import annotations

import pytest
from django.contrib import admin as dj_admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.audit.models import AuditLog
from apps.conversations.models import Conversation
from apps.handoff.admin import AdminTaskAdmin
from apps.handoff.models import AdminTask
from apps.handoff.services import create_admin_task
from apps.identity.models import BotUser
from apps.tenancy.context import current_tenant, tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="hs-admin", name="HAdmin")


@pytest.fixture
def bot_user(tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="hs-admin-bot",
        display_name="Ivan Test",
        phone="+79991234567",
    )


@pytest.fixture
def conversation(tenant, bot_user) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


@pytest.fixture
def open_task(tenant, conversation, settings) -> AdminTask:
    """A live handoff: task created through the service, conversation muted."""
    settings.STRICT_TENANT_SCOPE = "strict"
    with tenant_scope(tenant):
        task = create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)
    conversation.refresh_from_db()
    assert conversation.state == Conversation.State.HUMAN_HANDOFF
    return task


@pytest.fixture
def admin_instance() -> AdminTaskAdmin:
    return AdminTaskAdmin(AdminTask, dj_admin.site)


@pytest.fixture
def operator_request(db):
    user = get_user_model().objects.create_superuser(
        username="op", email="op@example.com", password="x"
    )
    request = RequestFactory().post("/admin/handoff/admintask/1/change/")
    request.user = user
    return request


def _save(admin_instance, request, task: AdminTask, *, status: str, note: str = "") -> AdminTask:
    """Simulate the admin change-form save: form binds new values to obj."""
    obj = AdminTask.all_tenants.get(pk=task.pk)
    obj.status = status
    obj.resolution_note = note
    admin_instance.save_model(request, obj, form=None, change=True)
    return obj


class TestResolveViaAdmin:
    def test_returns_conversation_to_bot(
        self, admin_instance, operator_request, open_task, conversation
    ):
        _save(
            admin_instance,
            operator_request,
            open_task,
            status=AdminTask.Status.RESOLVED,
            note="handled",
        )
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.IDLE

        open_task.refresh_from_db()
        assert open_task.status == AdminTask.Status.RESOLVED
        assert open_task.resolved_at is not None
        assert open_task.resolution_note == "handled"

    def test_writes_resolve_audit(self, admin_instance, operator_request, open_task, tenant):
        _save(admin_instance, operator_request, open_task, status=AdminTask.Status.RESOLVED)
        assert AuditLog.all_tenants.filter(
            tenant=tenant, action="handoff.resolved", target_id=open_task.id
        ).exists()


class TestCancelViaAdmin:
    def test_returns_conversation_to_bot_without_resolved_at(
        self, admin_instance, operator_request, open_task, conversation
    ):
        _save(
            admin_instance,
            operator_request,
            open_task,
            status=AdminTask.Status.CANCELLED,
            note="duplicate",
        )
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.IDLE

        open_task.refresh_from_db()
        assert open_task.status == AdminTask.Status.CANCELLED
        # CANCELLED = no completed work — resolved_at stays NULL.
        assert open_task.resolved_at is None
        assert open_task.resolution_note == "duplicate"


class TestResaveClosedTaskHealsConversation:
    """The DRF-980 trap: status flipped out-of-band (ORM/shell) leaves the
    dialog muted; the next admin save of that closed task must heal it."""

    def test_resave_of_resolved_task_unmutes(
        self, admin_instance, operator_request, open_task, conversation
    ):
        _save(admin_instance, operator_request, open_task, status=AdminTask.Status.RESOLVED)
        # Out-of-band re-mute (e.g. a fresh handoff written directly, or the
        # manual prod fix that flipped status without the service).
        Conversation.all_tenants.filter(pk=conversation.pk).update(
            state=Conversation.State.HUMAN_HANDOFF
        )
        _save(admin_instance, operator_request, open_task, status=AdminTask.Status.RESOLVED)
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.IDLE

    def test_resave_preserves_first_resolve_stamps(
        self, admin_instance, operator_request, open_task
    ):
        _save(
            admin_instance,
            operator_request,
            open_task,
            status=AdminTask.Status.RESOLVED,
            note="first",
        )
        open_task.refresh_from_db()
        first_resolved_at = open_task.resolved_at

        _save(
            admin_instance,
            operator_request,
            open_task,
            status=AdminTask.Status.RESOLVED,
            note="second clobber",
        )
        open_task.refresh_from_db()
        assert open_task.resolved_at == first_resolved_at
        assert open_task.resolution_note == "first"


class TestCrossTenantClose:
    def test_close_without_request_tenant_scope(
        self, admin_instance, operator_request, open_task, conversation
    ):
        """The admin is cross-tenant: no tenant in the request context.
        The close must still work (scope taken from the task) and must
        not leak the scope afterwards."""
        assert current_tenant() is None
        _save(admin_instance, operator_request, open_task, status=AdminTask.Status.RESOLVED)
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.IDLE
        assert current_tenant() is None

    def test_close_from_foreign_tenant_scope(
        self, admin_instance, operator_request, open_task, conversation, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        other = Tenant.objects.create(slug="hs-admin-other", name="Other")
        with tenant_scope(other):
            _save(
                admin_instance,
                operator_request,
                open_task,
                status=AdminTask.Status.RESOLVED,
            )
            # Scope restored to the request's tenant, not the task's.
            assert current_tenant() == other
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.IDLE


class TestMultipleOpenTasks:
    def test_conversation_released_only_when_last_task_closes(
        self, admin_instance, operator_request, tenant, conversation, settings
    ):
        settings.STRICT_TENANT_SCOPE = "strict"
        with tenant_scope(tenant):
            task_a = create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF)
            task_b = create_admin_task(conversation, task_type=AdminTask.TaskType.COMPLAINT)

        _save(admin_instance, operator_request, task_a, status=AdminTask.Status.RESOLVED)
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.HUMAN_HANDOFF

        _save(admin_instance, operator_request, task_b, status=AdminTask.Status.CANCELLED)
        conversation.refresh_from_db()
        assert conversation.state == Conversation.State.IDLE
