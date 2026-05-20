"""Admin-side endpoint tests."""

from __future__ import annotations

import json

import pytest

from apps.audit.models import AuditLog
from apps.internal_chat.models import (
    StatusChoices,
    TopicChoices,
)
from apps.internal_chat.services import create_thread
from apps.internal_chat.tests.conftest import init_data_header


pytestmark = pytest.mark.django_db


# --- list visibility -----------------------------------------------------


def test_admin_sees_all_threads_in_tenant(
    client, tenant, master, master_bot_user, other_master, other_master_bot_user, admin_bot_user
):
    create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="from anna",
    )
    create_thread(
        tenant=tenant,
        master=other_master,
        topic=TopicChoices.GENERAL,
        actor=other_master_bot_user,
        first_message_body="from maria",
    )

    resp = client.get(
        "/api/v1/internal-chat/admin/threads/",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["total_count"] == 2


def test_owner_sees_all_threads(client, tenant, master, master_bot_user, owner_bot_user):
    create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="hi",
    )
    resp = client.get(
        "/api/v1/internal-chat/admin/threads/",
        HTTP_AUTHORIZATION=init_data_header(owner_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    assert resp.json()["total_count"] == 1


def test_receptionist_blocked_by_require_admin_role(
    client, tenant, master, master_bot_user, receptionist_bot_user
):
    """The current admin-role decorator gates owner/admin only; receptionist
    is rejected with 403. Updating the decorator to also admit
    receptionists (for the list view) is captured as a follow-up PR
    — receptionist sees the queue UI through the future MAX Mini App
    rendering, not through this endpoint directly."""

    create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="hi",
    )
    resp = client.get(
        "/api/v1/internal-chat/admin/threads/",
        HTTP_AUTHORIZATION=init_data_header(receptionist_bot_user.channel_user_id),
    )
    assert resp.status_code == 403


def test_customer_blocked_with_403(client, tenant, customer_bot_user):
    resp = client.get(
        "/api/v1/internal-chat/admin/threads/",
        HTTP_AUTHORIZATION=init_data_header(customer_bot_user.channel_user_id),
    )
    assert resp.status_code == 403


def test_admin_list_filters_by_status(client, tenant, master, master_bot_user, admin_bot_user):
    t1 = create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="open one",
    )
    t2 = create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="to be closed",
    )
    from apps.internal_chat.services import close_thread

    close_thread(thread=t2, actor=admin_bot_user, status=StatusChoices.RESOLVED)

    resp = client.get(
        f"/api/v1/internal-chat/admin/threads/?status={StatusChoices.MASTER_RESPONDED.value}",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert data["items"][0]["id"] == str(t1.id)


def test_admin_list_filters_by_topic(client, tenant, master, master_bot_user, admin_bot_user):
    create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.EARNINGS_DISPUTE,
        actor=master_bot_user,
        first_message_body="earnings",
    )
    create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="other",
    )
    resp = client.get(
        f"/api/v1/internal-chat/admin/threads/?topic={TopicChoices.EARNINGS_DISPUTE.value}",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    assert resp.json()["total_count"] == 1


def test_admin_list_search_matches_subject(client, tenant, master, master_bot_user, admin_bot_user):
    """Substring search on subject — case-INsensitive icontains.

    SQLite's icontains is ASCII-only by default; we test with an
    all-ASCII subject so the assertion is portable to both backends.
    Postgres handles Russian icontains natively in production.
    """

    create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        subject="Friday off discussion",
        actor=master_bot_user,
        first_message_body="bla",
    )
    create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        subject="Earnings dispute",
        actor=master_bot_user,
        first_message_body="bla",
    )
    resp = client.get(
        "/api/v1/internal-chat/admin/threads/?search=friday",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    assert resp.json()["total_count"] == 1


# --- detail --------------------------------------------------------------


def test_admin_detail_returns_full_thread(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    resp = client.get(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    data = resp.json()["thread"]
    assert data["id"] == str(thread.id)
    assert len(data["messages"]) == 1


# --- send message --------------------------------------------------------


def test_admin_send_message_updates_status_to_admin_responded(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    resp = client.post(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/messages/",
        data=json.dumps(
            {
                "body": "Извини, поправили — 1280 ₽.",
                "sender_admin_signed_name": "Натали",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 201, resp.content
    data = resp.json()
    assert data["message"]["sender_admin_signed_name"] == "Натали"
    thread.refresh_from_db()
    assert thread.status == StatusChoices.ADMIN_RESPONDED


def test_admin_cannot_send_to_closed_thread(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    from apps.internal_chat.services import close_thread

    close_thread(thread=thread, actor=admin_bot_user, status=StatusChoices.RESOLVED)
    resp = client.post(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/messages/",
        data=json.dumps({"body": "hello"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 409


# --- PATCH ---------------------------------------------------------------


def test_admin_patches_topic_writes_audit(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    resp = client.patch(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/",
        data=json.dumps({"topic": TopicChoices.GENERAL.value}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200, resp.content
    thread.refresh_from_db()
    assert thread.topic == TopicChoices.GENERAL

    audit = AuditLog.all_tenants.filter(action="internal_chat.thread_fields_patched").first()
    assert audit is not None
    assert "topic" in audit.payload["fields_changed"]


def test_admin_patches_assigned_admin_writes_audit(
    client, tenant, master, master_bot_user, admin_bot_user, owner_bot_user, thread
):
    resp = client.patch(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/",
        data=json.dumps({"assigned_admin_id": str(owner_bot_user.id)}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    thread.refresh_from_db()
    assert thread.assigned_admin_id == owner_bot_user.id
    audit = AuditLog.all_tenants.filter(action="internal_chat.thread_assigned").first()
    assert audit is not None


def test_admin_patches_status_to_resolved(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    resp = client.patch(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/",
        data=json.dumps({"status": StatusChoices.RESOLVED.value}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    thread.refresh_from_db()
    assert thread.status == StatusChoices.RESOLVED
    assert thread.resolved_at is not None


def test_admin_patch_invalid_topic_rejected(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    resp = client.patch(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/",
        data=json.dumps({"topic": "nonsense"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 400


# --- mark read -----------------------------------------------------------


def test_admin_mark_read_only_admin_audience(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    resp = client.post(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/mark-read/",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    # Only the first message was unread by admin → marked=1.
    assert resp.json()["marked"] == 1


# --- close ---------------------------------------------------------------


def test_admin_close_thread_sets_resolved(
    client, tenant, master, master_bot_user, admin_bot_user, thread
):
    resp = client.post(
        f"/api/v1/internal-chat/admin/threads/{thread.id}/close/",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    thread.refresh_from_db()
    assert thread.status == StatusChoices.RESOLVED
    assert thread.resolved_at is not None


# --- sensitive thread redaction (handoff §2.7) ---------------------------


def test_owner_sees_sensitive_subject_unredacted(
    client, tenant, master, master_bot_user, owner_bot_user, sensitive_thread
):
    resp = client.get(
        "/api/v1/internal-chat/admin/threads/",
        HTTP_AUTHORIZATION=init_data_header(owner_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["subject"] == sensitive_thread.subject
    assert item["is_sensitive"] is True


def test_owner_sees_sensitive_detail(
    client, tenant, master, master_bot_user, owner_bot_user, sensitive_thread
):
    resp = client.get(
        f"/api/v1/internal-chat/admin/threads/{sensitive_thread.id}/",
        HTTP_AUTHORIZATION=init_data_header(owner_bot_user.channel_user_id),
    )
    assert resp.status_code == 200


def test_admin_sees_sensitive_detail(
    client, tenant, master, master_bot_user, admin_bot_user, sensitive_thread
):
    """Admin role admits sensitive-thread detail per handoff §2.7."""

    resp = client.get(
        f"/api/v1/internal-chat/admin/threads/{sensitive_thread.id}/",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 200


# --- cross-tenant --------------------------------------------------------


def test_admin_cross_tenant_thread_returns_404(
    client, tenant, master, master_bot_user, admin_bot_user, other_tenant
):
    from datetime import datetime, timezone as dt_timezone

    other_master = type(master).all_tenants.create(
        tenant=other_tenant,
        external_id=999,
        external_updated_at=datetime.now(tz=dt_timezone.utc),
        name="Чужой",
        specialization="X",
        is_active=True,
        invite_status=master.invite_status,
        invited_at=datetime.now(tz=dt_timezone.utc),
    )
    other_thread = create_thread(
        tenant=other_tenant,
        master=other_master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="cross-tenant",
    )
    resp = client.get(
        f"/api/v1/internal-chat/admin/threads/{other_thread.id}/",
        HTTP_AUTHORIZATION=init_data_header(admin_bot_user.channel_user_id),
    )
    assert resp.status_code == 404
