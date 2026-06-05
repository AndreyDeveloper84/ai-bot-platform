"""Model-layer tests — invariants, defaults, indexes, cascades."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.internal_chat.models import (
    LINKED_ARTIFACT_TYPES,
    MasterAdminMessage,
    MasterAdminMessageAttachment,
    MasterAdminThread,
    PiiScanStatusChoices,
    SENSITIVE_TOPICS,
    SenderRoleChoices,
    StatusChoices,
    TopicChoices,
    sla_hours_for_topic,
)
from apps.tenancy.context import tenant_scope


pytestmark = pytest.mark.django_db


# --- sensitive topic auto-flag -------------------------------------------


def test_thread_auto_flags_offboarding_as_sensitive(tenant, master, master_bot_user):
    with tenant_scope(tenant):
        thread = MasterAdminThread.all_tenants.create(
            tenant=tenant,
            master=master,
            topic=TopicChoices.OFFBOARDING_DISCUSSION,
        )
    assert thread.is_sensitive is True
    assert thread.topic in SENSITIVE_TOPICS


def test_thread_auto_flags_other_master_complaint_as_sensitive(tenant, master):
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.OTHER_MASTER_COMPLAINT,
    )
    assert thread.is_sensitive is True


def test_thread_general_topic_not_auto_sensitive(tenant, master):
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    assert thread.is_sensitive is False


# --- SLA defaults --------------------------------------------------------


@pytest.mark.parametrize(
    "topic,expected_hours",
    [
        (TopicChoices.EARNINGS_DISPUTE, 48),
        (TopicChoices.REVIEW_CONCERN, 48),
        (TopicChoices.LEAVE_REQUEST, 24),
        (TopicChoices.SCHEDULE_CHANGE, 48),
        (TopicChoices.OFFBOARDING_DISCUSSION, 48),
        (TopicChoices.OTHER_MASTER_COMPLAINT, 72),
        (TopicChoices.GENERAL, 72),
    ],
)
def test_sla_hours_per_topic(topic, expected_hours):
    assert sla_hours_for_topic(topic.value) == expected_hours


def test_thread_default_sla_due_at_set_on_save(tenant, master):
    before = timezone.now()
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.LEAVE_REQUEST,
    )
    after = timezone.now()
    # The fallback uses created_at (or now() if it's not set yet); the
    # 24h delta is what matters.
    delta = thread.sla_due_at - before
    assert delta >= timedelta(hours=24, seconds=-5)
    assert thread.sla_due_at <= after + timedelta(hours=24, seconds=5)


# --- linked artifact placeholder -----------------------------------------


def test_linked_artifact_accepts_any_known_type(tenant, master):
    for artifact_type in LINKED_ARTIFACT_TYPES:
        thread = MasterAdminThread.all_tenants.create(
            tenant=tenant,
            master=master,
            topic=TopicChoices.GENERAL,
            linked_artifact_type=artifact_type,
            linked_artifact_id=uuid.uuid4(),
        )
        assert thread.linked_artifact_type == artifact_type
        assert thread.linked_artifact_id is not None


def test_linked_artifact_id_no_fk_constraint(tenant, master):
    """No FK constraint — a random UUID is accepted."""

    random_uuid = uuid.uuid4()
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        linked_artifact_type="earning_dispute",
        linked_artifact_id=random_uuid,
    )
    assert thread.linked_artifact_id == random_uuid


def test_linked_artifact_default_empty(tenant, master):
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    assert thread.linked_artifact_type == ""
    assert thread.linked_artifact_id is None


# --- cross-tenant isolation -----------------------------------------------


def test_threads_scoped_to_tenant(tenant, other_tenant, master):
    """TenantScopedManager filters by current_tenant — cross-tenant rows hidden."""

    other_master = type(master).all_tenants.create(
        tenant=other_tenant,
        external_id=999,
        external_updated_at=timezone.now(),
        name="Кто-то",
        specialization="X",
        is_active=True,
        invite_status=master.invite_status,
        invited_at=timezone.now(),
    )
    MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    MasterAdminThread.all_tenants.create(
        tenant=other_tenant,
        master=other_master,
        topic=TopicChoices.GENERAL,
    )

    with tenant_scope(tenant):
        assert MasterAdminThread.objects.count() == 1
        assert MasterAdminThread.objects.first().tenant_id == tenant.id

    with tenant_scope(other_tenant):
        assert MasterAdminThread.objects.count() == 1
        assert MasterAdminThread.objects.first().tenant_id == other_tenant.id


def test_all_tenants_sees_every_row(tenant, other_tenant, master):
    other_master = type(master).all_tenants.create(
        tenant=other_tenant,
        external_id=998,
        external_updated_at=timezone.now(),
        name="Кто-то",
        specialization="X",
        is_active=True,
        invite_status=master.invite_status,
        invited_at=timezone.now(),
    )
    MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    MasterAdminThread.all_tenants.create(
        tenant=other_tenant,
        master=other_master,
        topic=TopicChoices.GENERAL,
    )
    assert MasterAdminThread.all_tenants.count() == 2


# --- cascade delete -------------------------------------------------------


def test_deleting_master_cascades_threads_and_messages(tenant, master, master_bot_user):
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    MasterAdminMessage.objects.create(
        thread=thread,
        sender_role=SenderRoleChoices.MASTER,
        sender_user=master_bot_user,
        body="hello",
    )
    assert MasterAdminThread.all_tenants.filter(id=thread.id).count() == 1
    assert MasterAdminMessage.objects.filter(thread=thread).count() == 1

    master.delete()
    assert MasterAdminThread.all_tenants.filter(id=thread.id).count() == 0
    assert MasterAdminMessage.objects.filter(thread=thread).count() == 0


def test_deleting_thread_cascades_messages(tenant, master, master_bot_user):
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    MasterAdminMessage.objects.create(
        thread=thread,
        sender_role=SenderRoleChoices.MASTER,
        sender_user=master_bot_user,
        body="hello",
    )
    thread.delete()
    assert MasterAdminMessage.objects.filter(thread_id=thread.id).count() == 0


# --- attachment defaults --------------------------------------------------


def test_attachment_defaults_to_pending_scan(tenant, master, master_bot_user):
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    message = MasterAdminMessage.objects.create(
        thread=thread,
        sender_role=SenderRoleChoices.MASTER,
        sender_user=master_bot_user,
        body="see attached",
    )
    attachment = MasterAdminMessageAttachment.objects.create(
        message=message,
        attachment_type="image",
        file="fake.jpg",
        file_size_bytes=1024,
        file_sha256="0" * 64,
    )
    assert attachment.pii_scan_status == PiiScanStatusChoices.PENDING


# --- check constraint defence-in-depth ------------------------------------


def test_constraint_blocks_sensitive_topic_without_flag(tenant, master):
    """Defence-in-depth: bypassing save() via direct .update() to clear the
    auto-flag should fail the DB-level CHECK constraint."""

    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.OFFBOARDING_DISCUSSION,
    )
    # The save() override flipped is_sensitive=True. Bypass it.
    with pytest.raises(IntegrityError):
        MasterAdminThread.all_tenants.filter(id=thread.id).update(is_sensitive=False)


# --- status default -------------------------------------------------------


def test_thread_status_defaults_to_open(tenant, master):
    thread = MasterAdminThread.all_tenants.create(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
    )
    assert thread.status == StatusChoices.OPEN
    assert thread.resolved_at is None
    assert thread.archived_at is None
    assert thread.founder_added_at is None
