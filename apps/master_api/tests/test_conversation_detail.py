"""Tests for the M6 master conversation detail endpoints (PR M6.1).

Covers four endpoints:
  GET    /api/v1/master/conversations/<id>
  POST   /api/v1/master/conversations/<id>/messages
  POST   /api/v1/master/conversations/<id>/mark-read
  POST   /api/v1/master/conversations/<id>/promote

Test groups:
  * Auth + ownership (cross-master / cross-tenant / archived)
  * GET detail (PII gate, tier, returning customer, attribution)
  * POST messages (validation, HUMAN_LOCKED rejection, attribution
    metadata persisted, audit + event emit)
  * POST mark-read (idempotency, audit per call)
  * POST promote (tier transition, audit, MAX DM dispatch, already_locked)
  * Race + atomicity (concurrent message + promote)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import Conversation, Message
from apps.events.models import Event
from apps.identity.models import BotUser
from apps.master_api.services import conversation_detail as conv_detail_svc
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")


# --- helpers --------------------------------------------------------------


def _utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    return dt_local.astimezone(timezone.utc)


def _make_bot_user(
    *,
    tenant: Tenant,
    name: str = "Ксения Леонова",
    channel_user_id: str = "client-1",
    phone: str = "+79161112233",
) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        display_name=name,
        client_name=name,
        chat_id=channel_user_id,
        phone=phone,
    )


def _make_conversation(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    last_message_at: datetime | None = None,
    is_active: bool = True,
    outcome: str = "",
    tier: str = Conversation.Tier.AI_CONTINUITY,
    tier_reason_class: str = "",
    tier_locked_at: datetime | None = None,
    tier_locked_by_master: CatalogMaster | None = None,
) -> Conversation:
    conv = Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        is_active=is_active,
        outcome=outcome,
        tier=tier,
        tier_reason_class=tier_reason_class,
        tier_locked_at=tier_locked_at,
        tier_locked_by_master=tier_locked_by_master,
    )
    if last_message_at is not None:
        Conversation.all_tenants.filter(pk=conv.pk).update(last_message_at=last_message_at)
        conv.refresh_from_db()
    return conv


def _add_message(
    *,
    tenant: Tenant,
    conversation: Conversation,
    role: str,
    content: str,
    created_at: datetime | None = None,
    action_type: str = "",
    action_data: dict | None = None,
) -> Message:
    msg = Message.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        role=role,
        content=content,
        action_type=action_type,
        action_data=action_data,
    )
    if created_at is not None:
        Message.all_tenants.filter(pk=msg.pk).update(created_at=created_at)
        msg.refresh_from_db()
    return msg


def _make_booking(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    bot_user: BotUser,
    visit_local: datetime | None = None,
    status: str = BookingRequest.Status.CONFIRMED,
    completed: bool = False,
) -> BookingRequest:
    visit_at = _utc(visit_local) if visit_local is not None else None
    booking = BookingRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        bot_user=bot_user,
        service_name="маникюр",
        client_name=bot_user.client_name or bot_user.display_name,
        client_phone=bot_user.phone or "+79000000000",
        visit_at=visit_at,
        duration_min=60,
        status=status,
    )
    if completed:
        BookingRequest.all_tenants.filter(pk=booking.pk).update(
            completed_at=visit_at or datetime.now(tz=timezone.utc)
        )
        booking.refresh_from_db()
    return booking


def _detail_url(conversation_id) -> str:
    return reverse(
        "master_api:conversation_detail",
        kwargs={"conversation_id": str(conversation_id)},
    )


def _messages_url(conversation_id) -> str:
    return reverse(
        "master_api:conversation_send_message",
        kwargs={"conversation_id": str(conversation_id)},
    )


def _mark_read_url(conversation_id) -> str:
    return reverse(
        "master_api:conversation_mark_read",
        kwargs={"conversation_id": str(conversation_id)},
    )


def _promote_url(conversation_id) -> str:
    return reverse(
        "master_api:conversation_promote",
        kwargs={"conversation_id": str(conversation_id)},
    )


# --- auth + ownership -----------------------------------------------------


class TestAuthAndOwnership:
    def test_unlinked_bot_user_returns_401(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        # The bot_user exists but no CatalogMaster is linked → not_a_master.
        customer = _make_bot_user(tenant=tenant, channel_user_id="random")
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

    def test_master_with_no_booking_link_returns_404(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # The master exists but has no booking with this customer.
        stranger = _make_bot_user(tenant=tenant, channel_user_id="stranger")
        conv = _make_conversation(tenant=tenant, bot_user=stranger)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_master_with_booking_link_returns_200(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation_id"] == str(conv.id)

    def test_cross_tenant_returns_404(
        self,
        client: Client,
        tenant: Tenant,
        other_tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # Conversation lives in other_tenant — master MUST NOT see it.
        foreign_user = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="foreign",
            display_name="Чужой",
            client_name="Чужой",
            chat_id="foreign",
        )
        conv = Conversation.all_tenants.create(
            tenant=other_tenant, bot_user=foreign_user, is_active=True
        )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 404

    def test_archived_master_returns_403(
        self, client: Client, bot_user: BotUser, tenant: Tenant
    ) -> None:
        make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=bot_user,
            archived_at=datetime.now(tz=timezone.utc),
        )
        customer = _make_bot_user(tenant=tenant, channel_user_id="c2")
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 403

    def test_cross_master_isolation(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        # master_b's customer must not appear in accepted_master's detail.
        other_max = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="other-master-mx",
            display_name="Другой",
            chat_id="other-master-mx",
        )
        master_b = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=other_max,
            name="Мастер Б",
        )
        customer = _make_bot_user(tenant=tenant, channel_user_id="c3")
        _make_booking(tenant=tenant, master=master_b, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        # accepted_master tries to view → 404
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 404


# --- GET detail -----------------------------------------------------------


class TestDetailGet:
    def test_returns_messages_chronological(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0))
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="привет",
            created_at=now - timedelta(minutes=10),
        )
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="Здравствуйте!",
            created_at=now - timedelta(minutes=9),
        )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        assert [m["content"] for m in body["messages"]] == ["привет", "Здравствуйте!"]

    def test_pii_gate_no_phone_no_full_lastname_no_email_no_ltv(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant, name="Ксения Леонова", phone="+79161112233")
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        raw = resp.content.decode()
        body = resp.json()
        # First name visible, full last name MUST NOT appear.
        assert body["client_first_name"] == "Ксения"
        assert body["client_last_initial"] == "Л."
        assert "Леонова" not in raw
        # Phone never serialised.
        assert "+79161112233" not in raw
        # No LTV / email keys.
        assert "ltv" not in raw.lower()
        assert "email" not in raw.lower()

    def test_human_locked_disables_compose(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(
            tenant=tenant,
            bot_user=customer,
            tier=Conversation.Tier.HUMAN_LOCKED,
            tier_reason_class="complaint",
            tier_locked_at=_utc(datetime(2026, 5, 21, 14, 0)),
            tier_locked_by_master=accepted_master,
        )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        body = resp.json()
        assert body["tier"] == "human_locked"
        assert body["tier_reason"] == "complaint"
        assert body["permissions"]["can_compose"] is False
        assert body["permissions"]["can_promote_to_human_locked"] is False
        assert body["tier_locked_by_admin_name"] == accepted_master.name
        assert body["tier_locked_since"] is not None

    def test_tier_reason_hidden_on_non_locked_tier(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # tier_reason should never appear when tier == ai_continuity.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(
            tenant=tenant,
            bot_user=customer,
            # Even if reason is set, the response gates it on tier.
            tier=Conversation.Tier.AI_CONTINUITY,
            tier_reason_class="complaint",
        )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.json()["tier_reason"] is None

    def test_visit_count_and_returning_flag(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        for _ in range(3):
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=customer,
                completed=True,
            )
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        body = resp.json()
        assert body["visit_count"] == 3
        assert body["is_returning_customer"] is True

    def test_composed_by_master_flag_set_for_master_messages(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        # bot-authored
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="бот",
        )
        # master-authored
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="мастер",
            action_type="master_compose",
            action_data={
                "actor_type": "master",
                "composed_by": str(accepted_master.id),
            },
        )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        msgs = resp.json()["messages"]
        assert msgs[0]["composed_by_master"] is False
        assert msgs[1]["composed_by_master"] is True
        assert msgs[1]["composed_by_master_id"] == str(accepted_master.id)

    def test_ai_draft_is_null_in_this_pr(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # AI draft generation is a separate PR — verify null shape.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        body = resp.json()
        assert body["ai_draft"] == {
            "draft_id": None,
            "content": None,
            "created_at": None,
        }

    def test_messages_capped_at_100(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        base = _utc(datetime(2026, 5, 21, 12, 0))
        # Insert 120 messages — only last 100 should be returned.
        for i in range(120):
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content=f"msg-{i:03d}",
                created_at=base + timedelta(seconds=i),
            )
        resp = client.get(
            _detail_url(conv.id),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        msgs = resp.json()["messages"]
        assert len(msgs) == 100
        # The latest 100 = msg-020..msg-119, chronological.
        assert msgs[0]["content"] == "msg-020"
        assert msgs[-1]["content"] == "msg-119"


# --- POST messages --------------------------------------------------------


class TestSendMessage:
    def test_valid_content_creates_message(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.post(
            _messages_url(conv.id),
            data=json.dumps({"content": "Ксения, добрый день!"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["composed_by_master"] is True
        # Message row exists with attribution metadata.
        msg = Message.all_tenants.get(pk=body["message_id"])
        assert msg.role == Message.Role.ASSISTANT
        assert msg.content == "Ксения, добрый день!"
        assert msg.action_type == "master_compose"
        assert msg.action_data == {
            "actor_type": "master",
            "composed_by": str(accepted_master.id),
        }

    def test_audit_row_and_event_emitted(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        client.post(
            _messages_url(conv.id),
            data=json.dumps({"content": "ok"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        audits = AuditLog.all_tenants.filter(action="conversation.master_replied")
        assert audits.count() == 1
        first_audit = audits.first()
        assert first_audit is not None
        assert first_audit.payload["master_id"] == str(accepted_master.id)
        events = Event.objects.filter(event_type="conversation.master_replied")
        assert events.count() == 1

    def test_human_locked_returns_403(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(
            tenant=tenant,
            bot_user=customer,
            tier=Conversation.Tier.HUMAN_LOCKED,
            tier_locked_at=_utc(datetime(2026, 5, 21, 14, 0)),
        )
        resp = client.post(
            _messages_url(conv.id),
            data=json.dumps({"content": "hi"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "tier_locked"

    def test_empty_content_returns_400(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.post(
            _messages_url(conv.id),
            data=json.dumps({"content": "   "}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_content_too_long_returns_400(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.post(
            _messages_url(conv.id),
            data=json.dumps({"content": "x" * 2001}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_cross_master_send_returns_404(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        # Build a different master + customer pair; accepted_master tries
        # to send to that conversation → 404.
        other_max = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="other-master-mx",
            display_name="Другой",
            chat_id="other-master-mx",
        )
        master_b = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=other_max,
            name="Мастер Б",
        )
        customer = _make_bot_user(tenant=tenant, channel_user_id="c-cross")
        _make_booking(tenant=tenant, master=master_b, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.post(
            _messages_url(conv.id),
            data=json.dumps({"content": "leak"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 404

    def test_conversation_last_message_at_updated(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        before = conv.last_message_at
        client.post(
            _messages_url(conv.id),
            data=json.dumps({"content": "новое"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        conv.refresh_from_db()
        assert conv.last_message_at != before


# --- POST mark-read -------------------------------------------------------


class TestMarkRead:
    def test_marks_all_unread_returns_count(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        # Anchor to real-now so the service's now() comparison admits
        # these messages as "before now".
        base = datetime.now(tz=timezone.utc) - timedelta(minutes=10)
        for i in range(3):
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content=f"u{i}",
                created_at=base + timedelta(seconds=i),
            )
        # assistant messages don't count as unread for the master
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="a",
            created_at=base + timedelta(seconds=10),
        )
        resp = client.post(
            _mark_read_url(conv.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json()["marked_count"] == 3

    def test_idempotent_second_call_returns_zero(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
        )
        client.post(
            _mark_read_url(conv.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        resp2 = client.post(
            _mark_read_url(conv.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp2.status_code == 200
        assert resp2.json()["marked_count"] == 0

    def test_one_audit_row_per_call(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        client.post(
            _mark_read_url(conv.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        client.post(
            _mark_read_url(conv.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        # 2 calls → 2 audit rows (debounced means «one per call»,
        # not one per message marked).
        rows = AuditLog.all_tenants.filter(action="conversation.marked_read_by_master")
        assert rows.count() == 2

    def test_cross_master_returns_404(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        other_max = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="other-master-mx-2",
            display_name="Другой",
            chat_id="other-master-mx-2",
        )
        master_b = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=other_max,
            name="Мастер Б",
        )
        customer = _make_bot_user(tenant=tenant, channel_user_id="c-mark")
        _make_booking(tenant=tenant, master=master_b, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.post(
            _mark_read_url(conv.id),
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 404


# --- POST promote ---------------------------------------------------------


class TestPromote:
    def test_valid_reason_flips_tier(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.post(
            _promote_url(conv.id),
            data=json.dumps({"reason_class": "complaint", "reason_text": "клиент злой"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["tier"] == "human_locked"
        assert body["tier_locked_at"] is not None
        conv.refresh_from_db()
        assert conv.tier == Conversation.Tier.HUMAN_LOCKED
        assert conv.tier_reason_class == "complaint"
        assert conv.tier_locked_by_master_id == accepted_master.id
        assert conv.tier_locked_reason_text == "клиент злой"

    def test_already_locked_returns_409(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(
            tenant=tenant,
            bot_user=customer,
            tier=Conversation.Tier.HUMAN_LOCKED,
            tier_locked_at=_utc(datetime(2026, 5, 21, 14, 0)),
        )
        resp = client.post(
            _promote_url(conv.id),
            data=json.dumps({"reason_class": "complaint"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "already_locked"

    def test_invalid_reason_class_returns_400(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        resp = client.post(
            _promote_url(conv.id),
            data=json.dumps({"reason_class": "unknown_reason"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_audit_row_with_reason_class_and_master_id(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        client.post(
            _promote_url(conv.id),
            data=json.dumps({"reason_class": "medical", "reason_text": "аллергия"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        rows = AuditLog.all_tenants.filter(action="conversation.tier_promoted_to_human_locked")
        assert rows.count() == 1
        first_row = rows.first()
        assert first_row is not None
        payload = first_row.payload
        assert payload["reason_class"] == "medical"
        assert payload["reason_text"] == "аллергия"
        assert payload["master_id"] == str(accepted_master.id)

    def test_max_dm_dispatched_on_commit(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        tenant.manager_chat_id = "max-manager-1"
        tenant.save(update_fields=["manager_chat_id"])
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        # ``transaction.on_commit`` doesn't fire inside the default
        # TestCase transaction — drain via captureOnCommitCallbacks.
        with patch("apps.channels.max.outbound.send_message") as send_mock:
            with TestCase.captureOnCommitCallbacks(execute=True):
                resp = client.post(
                    _promote_url(conv.id),
                    data=json.dumps({"reason_class": "complaint"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION=init_data_header("12345"),
                )
            assert resp.status_code == 200
            send_mock.assert_called_once()
            _, kwargs = send_mock.call_args
            assert kwargs["chat_id"] == "max-manager-1"
            assert "complaint" in kwargs["text"]

    def test_no_manager_chat_id_skips_dm_silently(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # manager_chat_id stays empty.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        with patch("apps.channels.max.outbound.send_message") as send_mock:
            resp = client.post(
                _promote_url(conv.id),
                data=json.dumps({"reason_class": "financial"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("12345"),
            )
            assert resp.status_code == 200
            send_mock.assert_not_called()
        # Still flipped tier, still audited.
        conv.refresh_from_db()
        assert conv.tier == Conversation.Tier.HUMAN_LOCKED

    def test_event_emitted(
        self,
        client: Client,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        client.post(
            _promote_url(conv.id),
            data=json.dumps({"reason_class": "other"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        evs = Event.objects.filter(event_type="conversation.tier_promoted_to_human_locked")
        assert evs.count() == 1


# --- service-layer direct race + atomicity --------------------------------


class TestRaceConditions:
    def test_send_then_promote_both_persist(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        # Verify the service-layer call sites both succeed atomically
        # when called in sequence — the spec asks for atomic transitions
        # that don't lose data.
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)

        from apps.tenancy.context import tenant_scope

        with tenant_scope(tenant):
            conv_detail_svc.send_master_message(
                accepted_master,
                conv.id,
                content="msg",
                actor_bot_user=bot_user,
            )
            conv_detail_svc.promote_to_human_locked(
                accepted_master,
                conv.id,
                reason_class="complaint",
                reason_text="",
                actor_bot_user=bot_user,
            )
        # Both effects observable.
        assert Message.all_tenants.filter(
            conversation_id=conv.id, action_type="master_compose"
        ).exists()
        conv.refresh_from_db()
        assert conv.tier == Conversation.Tier.HUMAN_LOCKED

    def test_promote_then_send_rejected(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        _make_booking(tenant=tenant, master=accepted_master, bot_user=customer)
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        from apps.tenancy.context import tenant_scope

        with tenant_scope(tenant):
            conv_detail_svc.promote_to_human_locked(
                accepted_master,
                conv.id,
                reason_class="complaint",
                reason_text="",
                actor_bot_user=bot_user,
            )
            with pytest.raises(conv_detail_svc.ConversationDetailError) as excinfo:
                conv_detail_svc.send_master_message(
                    accepted_master,
                    conv.id,
                    content="late",
                    actor_bot_user=bot_user,
                )
            assert excinfo.value.slug == "tier_locked"
