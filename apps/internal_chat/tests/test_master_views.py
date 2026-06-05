"""Master-side endpoint tests.

All requests carry init-data signed by the test fixture's BOT_TOKEN.
The :func:`require_master_init_data` decorator resolves a BotUser from
``channel_user_id`` and looks up the linked CatalogMaster row.
"""

from __future__ import annotations

import json

import pytest

from apps.audit.models import AuditLog
from apps.internal_chat.models import (
    MasterAdminMessage,
    MasterAdminThread,
    StatusChoices,
    TopicChoices,
)
from apps.internal_chat.services import create_thread
from apps.internal_chat.tests.conftest import init_data_header


pytestmark = pytest.mark.django_db


# --- list ----------------------------------------------------------------


def test_master_list_returns_only_own_threads(
    client, tenant, master, master_bot_user, other_master, other_master_bot_user
):
    """Cross-master threads MUST be invisible to a different master."""

    own = create_thread(
        tenant=tenant,
        master=master,
        topic=TopicChoices.GENERAL,
        actor=master_bot_user,
        first_message_body="own",
    )
    create_thread(
        tenant=tenant,
        master=other_master,
        topic=TopicChoices.GENERAL,
        actor=other_master_bot_user,
        first_message_body="other",
    )
    resp = client.get(
        "/api/v1/internal-chat/master/threads/",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 1
    assert data["items"][0]["id"] == str(own.id)


def test_master_list_pagination(client, tenant, master, master_bot_user):
    for i in range(3):
        create_thread(
            tenant=tenant,
            master=master,
            topic=TopicChoices.GENERAL,
            actor=master_bot_user,
            first_message_body=f"thread {i}",
        )
    resp = client.get(
        "/api/v1/internal-chat/master/threads/?limit=2&offset=0",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_count"] == 3
    assert len(data["items"]) == 2


# --- detail --------------------------------------------------------------


def test_master_detail_returns_thread(client, tenant, master, master_bot_user, thread):
    resp = client.get(
        f"/api/v1/internal-chat/master/threads/{thread.id}/",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    data = resp.json()["thread"]
    assert data["id"] == str(thread.id)
    assert len(data["messages"]) == 1


def test_master_detail_cross_master_returns_404(
    client, tenant, other_master, other_master_bot_user, master, master_bot_user
):
    """A master must NOT see another master's thread (handoff §2.7 — 404 not 403)."""

    other_thread = create_thread(
        tenant=tenant,
        master=other_master,
        topic=TopicChoices.GENERAL,
        actor=other_master_bot_user,
        first_message_body="not yours",
    )
    resp = client.get(
        f"/api/v1/internal-chat/master/threads/{other_thread.id}/",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 404


def test_master_detail_bad_uuid_returns_404(client, master_bot_user, master):
    resp = client.get(
        "/api/v1/internal-chat/master/threads/not-a-uuid/",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 404


# --- create --------------------------------------------------------------


def test_master_creates_thread_with_first_message(client, tenant, master, master_bot_user):
    resp = client.post(
        "/api/v1/internal-chat/master/threads/",
        data=json.dumps(
            {
                "topic": TopicChoices.EARNINGS_DISPUTE.value,
                "subject": "Спор по доходу за 17 мая",
                "first_message_body": "Применилась дефолтная ставка вместо моей.",
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 201, resp.content
    data = resp.json()["thread"]
    assert data["topic"] == TopicChoices.EARNINGS_DISPUTE.value
    assert data["subject"] == "Спор по доходу за 17 мая"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["sender_role"] == "master"

    thread_count = MasterAdminThread.all_tenants.filter(tenant=tenant).count()
    message_count = MasterAdminMessage.objects.filter(thread_id=data["id"]).count()
    assert thread_count == 1
    assert message_count == 1

    # Audit row should have been written for thread_created.
    audit = AuditLog.all_tenants.filter(action="internal_chat.thread_created").first()
    assert audit is not None
    assert str(audit.target_id) == data["id"]


def test_master_create_requires_topic(client, master_bot_user, master):
    resp = client.post(
        "/api/v1/internal-chat/master/threads/",
        data=json.dumps({"first_message_body": "..."}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 400


def test_master_create_requires_body(client, master_bot_user, master):
    resp = client.post(
        "/api/v1/internal-chat/master/threads/",
        data=json.dumps({"topic": TopicChoices.GENERAL.value}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 400


def test_master_create_with_linked_artifact(client, tenant, master, master_bot_user):
    import uuid

    artifact_id = uuid.uuid4()
    resp = client.post(
        "/api/v1/internal-chat/master/threads/",
        data=json.dumps(
            {
                "topic": TopicChoices.EARNINGS_DISPUTE.value,
                "first_message_body": "disp",
                "linked_artifact_type": "earning_dispute",
                "linked_artifact_id": str(artifact_id),
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 201
    data = resp.json()["thread"]
    assert data["linked_artifact_type"] == "earning_dispute"
    assert data["linked_artifact_id"] == str(artifact_id)


# --- send message --------------------------------------------------------


def test_master_send_message_updates_status_to_master_responded(
    client, tenant, master, master_bot_user, thread
):
    # The thread was created with one master message → status already
    # master_responded. Simulate an admin reply, then a master reply.
    from apps.internal_chat.models import SenderRoleChoices
    from apps.internal_chat.services import send_message

    send_message(
        thread=thread,
        sender_role=SenderRoleChoices.ADMIN,
        sender_user=master_bot_user,  # any non-null is fine for the FK
        body="привет",
    )
    thread.refresh_from_db()
    assert thread.status == StatusChoices.ADMIN_RESPONDED

    resp = client.post(
        f"/api/v1/internal-chat/master/threads/{thread.id}/messages/",
        data=json.dumps({"body": "спасибо, поняла"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 201, resp.content
    thread.refresh_from_db()
    assert thread.status == StatusChoices.MASTER_RESPONDED


def test_master_cannot_send_to_closed_thread(client, tenant, master, master_bot_user, thread):
    from apps.internal_chat.services import close_thread

    close_thread(thread=thread, actor=master_bot_user, status=StatusChoices.RESOLVED)
    resp = client.post(
        f"/api/v1/internal-chat/master/threads/{thread.id}/messages/",
        data=json.dumps({"body": "ещё кое-что"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 409


# --- mark read -----------------------------------------------------------


def test_master_mark_read_stamps_messages(client, tenant, master, master_bot_user, thread):
    from apps.internal_chat.models import SenderRoleChoices
    from apps.internal_chat.services import send_message

    # Admin sends a few messages — none read by master.
    for i in range(3):
        send_message(
            thread=thread,
            sender_role=SenderRoleChoices.ADMIN,
            sender_user=master_bot_user,
            body=f"reply {i}",
        )

    resp = client.post(
        f"/api/v1/internal-chat/master/threads/{thread.id}/mark-read/",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    # The first message (master-authored) was already there too; total
    # unread by master = 4 — its own message + 3 admin messages all
    # had ``read_by_master_at IS NULL``. The mark-read helper stamps
    # all of them.
    data = resp.json()
    assert data["marked"] >= 3

    unread_remaining = MasterAdminMessage.objects.filter(
        thread=thread, read_by_master_at__isnull=True
    ).count()
    assert unread_remaining == 0


def test_master_mark_read_idempotent(client, tenant, master, master_bot_user, thread):
    client.post(
        f"/api/v1/internal-chat/master/threads/{thread.id}/mark-read/",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    resp = client.post(
        f"/api/v1/internal-chat/master/threads/{thread.id}/mark-read/",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 200
    assert resp.json()["marked"] == 0


# --- escalate to founder -------------------------------------------------


def test_master_escalates_to_founder(client, tenant, master, master_bot_user, thread):
    resp = client.post(
        f"/api/v1/internal-chat/master/threads/{thread.id}/escalate-to-founder/",
        data=json.dumps({"reason": "ответ не устроил"}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header(master_bot_user.channel_user_id),
    )
    assert resp.status_code == 200, resp.content
    thread.refresh_from_db()
    assert thread.status == StatusChoices.ESCALATED_TO_FOUNDER
    assert thread.founder_added_at is not None

    audit = AuditLog.all_tenants.filter(action="internal_chat.escalated_to_founder").first()
    assert audit is not None


# --- auth rejections -----------------------------------------------------


def test_customer_without_master_link_gets_401(client, tenant, customer_bot_user):
    """No linked CatalogMaster row → require_master_init_data rejects."""

    resp = client.get(
        "/api/v1/internal-chat/master/threads/",
        HTTP_AUTHORIZATION=init_data_header(customer_bot_user.channel_user_id),
    )
    # require_master_init_data emits 401 with slug=not_a_master.
    assert resp.status_code == 401
    assert resp.json()["error"] == "not_a_master"
