"""Tests for the M5 master conversations list endpoint (PR Tier1.3).

Covers:
* Auth — customer / archived / accepted master.
* «Involves master» — booking-based inclusion + cross-master /
  cross-tenant isolation.
* Section classification (awaiting_master / ai_drafted / ai_handling /
  resolved / other). ai_drafted is wired to AiDraft (#581).
* Filter (active / all / resolved with 30-day cutoff).
* Search — first-name only; no phone / email / full-last-name leak.
* PII gating — JSON contains no phone / last_name / email / LTV keys.
* Sort — section_order → SLA → recency.
* Pagination — cursor round-trip + tamper rejection.
* Returning customer — >1 confirmed booking with this master.

All tests pin ``now`` and message timestamps explicitly so SLA tier
assertions don't drift with wall-clock time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import Client
from django.urls import reverse

from django.test.utils import CaptureQueriesContext
from django.db import connection

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import AiDraft, Conversation, Message
from apps.identity.models import BotUser
from apps.master_api.services import conversations as conv_svc
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
) -> Conversation:
    conv = Conversation.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        is_active=is_active,
        outcome=outcome,
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
) -> Message:
    msg = Message.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        role=role,
        content=content,
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
        service_name="маникюр гель-лак",
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


# --- auth -----------------------------------------------------------------


class TestConversationsAuth:
    def test_customer_unlinked_returns_401(self, client: Client, bot_user: BotUser) -> None:
        # bot_user exists but no CatalogMaster is linked → 401 not_a_master.
        resp = client.get(
            reverse("master_api:conversations_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

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
        resp = client.get(
            reverse("master_api:conversations_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 403

    def test_accepted_master_returns_200(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:conversations_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["items"] == []
        assert body["section_counts"] == {
            "awaiting_master": 0,
            "ai_drafted": 0,
            "ai_handling": 0,
            "resolved_today": 0,
        }
        assert body["next_cursor"] is None


# --- involves master ------------------------------------------------------


class TestInvolvesMaster:
    """Booking-based "involves master" predicate + isolation."""

    def test_conversation_with_booking_is_included(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(
            tenant=tenant,
            bot_user=client,
            last_message_at=now - timedelta(minutes=10),
        )
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=10),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now)
        assert len(result.items) == 1
        assert result.items[0].conversation_id == str(conv.id)

    def test_conversation_without_booking_is_excluded(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        stranger = _make_bot_user(tenant=tenant, name="Чужой Клиент", channel_user_id="stranger")
        conv = _make_conversation(tenant=tenant, bot_user=stranger, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=5),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now)
        assert result.items == []

    def test_cross_master_isolation(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        """Master B's customer is NOT visible to master A."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        other_bot_user = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="other-master-mx",
            display_name="Другой Мастер",
            chat_id="other-master-mx",
        )
        master_b = make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=other_bot_user,
            name="Мастер Б",
        )
        client = _make_bot_user(tenant=tenant)
        # Booking is with master_b, not accepted_master.
        _make_booking(
            tenant=tenant,
            master=master_b,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=5),
        )
        # accepted_master must not see master_b's customer.
        result_a = conv_svc.list_master_conversations(accepted_master, now=now)
        assert result_a.items == []
        # master_b sees their own.
        result_b = conv_svc.list_master_conversations(master_b, now=now)
        assert len(result_b.items) == 1

    def test_cross_tenant_isolation(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        other_client = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="other-tenant-user",
            display_name="Чужой",
            client_name="Чужой Клиент",
            chat_id="other-tenant-user",
        )
        # Cross-tenant booking — should never be considered.
        BookingRequest.all_tenants.create(
            tenant=other_tenant,
            bot_user=other_client,
            master=None,
            service_name="x",
            client_name="x",
            client_phone="+7000",
            visit_at=now,
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
        )
        conv = Conversation.all_tenants.create(
            tenant=other_tenant, bot_user=other_client, is_active=True
        )
        _add_message(
            tenant=other_tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=5),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now)
        assert result.items == []


# --- section classification -----------------------------------------------


class TestSectionClassification:
    def test_awaiting_master_when_last_message_is_user(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="можно записаться?",
            created_at=now - timedelta(minutes=10),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert len(result.items) == 1
        assert result.items[0].section == "awaiting_master"

    def test_ai_handling_when_last_message_is_assistant(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="привет",
            created_at=now - timedelta(minutes=20),
        )
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="Добрый день!",
            created_at=now - timedelta(minutes=10),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert len(result.items) == 1
        assert result.items[0].section == "ai_handling"

    def test_resolved_when_conversation_inactive(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(
            tenant=tenant,
            bot_user=client,
            last_message_at=now - timedelta(hours=2),
            is_active=False,
        )
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="до встречи",
            created_at=now - timedelta(hours=2),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert len(result.items) == 1
        assert result.items[0].section == "resolved"

    def test_resolved_filter_excludes_older_than_30d(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 3, 1, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(
            tenant=tenant,
            bot_user=client,
            last_message_at=now - timedelta(days=60),
            is_active=False,
        )
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="закрыто",
            created_at=now - timedelta(days=60),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="resolved")
        assert result.items == []


# --- filter ---------------------------------------------------------------


class TestFilter:
    def _seed(self, tenant: Tenant, master: CatalogMaster, now: datetime) -> None:
        # Awaiting master.
        c1 = _make_bot_user(tenant=tenant, name="Ксения А.", channel_user_id="c1")
        _make_booking(
            tenant=tenant,
            master=master,
            bot_user=c1,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv1 = _make_conversation(tenant=tenant, bot_user=c1, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv1,
            role=Message.Role.USER,
            content="ждёт",
            created_at=now - timedelta(minutes=10),
        )
        # AI handling.
        c2 = _make_bot_user(tenant=tenant, name="Дарья С.", channel_user_id="c2")
        _make_booking(
            tenant=tenant,
            master=master,
            bot_user=c2,
            visit_local=datetime(2026, 5, 22, 13, 0, tzinfo=MSK),
        )
        conv2 = _make_conversation(tenant=tenant, bot_user=c2, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv2,
            role=Message.Role.USER,
            content="привет",
            created_at=now - timedelta(minutes=20),
        )
        _add_message(
            tenant=tenant,
            conversation=conv2,
            role=Message.Role.ASSISTANT,
            content="ответ",
            created_at=now - timedelta(minutes=10),
        )
        # Resolved (recent).
        c3 = _make_bot_user(tenant=tenant, name="Мария И.", channel_user_id="c3")
        _make_booking(
            tenant=tenant,
            master=master,
            bot_user=c3,
            visit_local=datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
        )
        conv3 = _make_conversation(
            tenant=tenant,
            bot_user=c3,
            last_message_at=now - timedelta(hours=3),
            is_active=False,
        )
        _add_message(
            tenant=tenant,
            conversation=conv3,
            role=Message.Role.ASSISTANT,
            content="готово",
            created_at=now - timedelta(hours=3),
        )

    def test_filter_active_returns_awaiting_only(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        self._seed(tenant, accepted_master, now)
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="active")
        sections = {i.section for i in result.items}
        # «Active» keeps awaiting_master + ai_drafted (both need attention);
        # this seed has no draft rows so only awaiting_master appears.
        assert sections == {"awaiting_master"}

    def test_filter_all_returns_everything(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        self._seed(tenant, accepted_master, now)
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        sections = {i.section for i in result.items}
        assert sections == {"awaiting_master", "ai_handling", "resolved"}

    def test_filter_resolved_returns_resolved_only(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        self._seed(tenant, accepted_master, now)
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="resolved")
        sections = {i.section for i in result.items}
        assert sections == {"resolved"}


# --- search ---------------------------------------------------------------


class TestSearch:
    def test_search_finds_by_first_name_case_insensitive(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant, name="Ксения Леонова")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=10),
        )
        result = conv_svc.list_master_conversations(
            accepted_master, now=now, search="ксЕнИя", filter="all"
        )
        assert len(result.items) == 1
        assert result.items[0].client_first_name == "Ксения"

    def test_search_never_finds_by_phone(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant, name="Ксения Леонова", phone="+79161112233")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=10),
        )
        # Search by phone digits — must NOT match.
        result = conv_svc.list_master_conversations(
            accepted_master, now=now, search="79161112233", filter="all"
        )
        assert result.items == []

    def test_search_does_not_match_full_last_name(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Search restricts to the first token — passing a full last name
        searches for that token only; substring match is permitted but
        the response still ONLY exposes the initial."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant, name="Ксения Леонова")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=10),
        )
        # Search by what looks like a last name; substring match still
        # finds the row, BUT the response only ever exposes the initial.
        result = conv_svc.list_master_conversations(
            accepted_master, now=now, search="Леонова", filter="all"
        )
        # Either matches (substring on display/client_name) — but in both
        # cases the response must never contain the full last name.
        for item in result.items:
            assert item.client_last_initial == "Л."
            # No "last_name" key at all on the payload.
            assert "last_name" not in item.to_dict()


# --- PII gating -----------------------------------------------------------


class TestPiiGating:
    def test_response_excludes_phone_email_ltv_last_name(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        tenant: Tenant,
    ) -> None:
        """The serialised JSON must contain NO leaked PII keys."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        customer = _make_bot_user(tenant=tenant, name="Ксения Леонова", phone="+79161234567")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=customer, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="привет",
            created_at=now - timedelta(minutes=10),
        )
        resp = client.get(
            reverse("master_api:conversations_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        body = resp.json()
        # Top-level: items only.
        assert set(body.keys()) == {"items", "section_counts", "next_cursor"}
        # Per-item: no forbidden keys; no phone digits leaked.
        for item in body["items"]:
            forbidden = {"phone", "email", "ltv", "last_name", "assignee", "human_locked_reason"}
            assert not forbidden.intersection(item.keys()), item
            raw = str(item)
            assert "79161234567" not in raw
            assert "Леонова" not in raw  # full last name never serialised

    def test_last_initial_is_single_letter(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        customer = _make_bot_user(tenant=tenant, name="Ксения Леонова")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=customer, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="привет",
            created_at=now - timedelta(minutes=10),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now)
        assert len(result.items) == 1
        initial = result.items[0].client_last_initial
        assert len(initial) == 2  # "Л."
        assert initial.endswith(".")

    def test_reason_chip_is_null_for_now(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """PR Tier1.3 scope cut: reason_chip is null (no «жалоба» risk
        of leaking complaint about master themselves)."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        customer = _make_bot_user(tenant=tenant)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=customer, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="жалоба на мастера",
            created_at=now - timedelta(minutes=10),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now)
        assert len(result.items) == 1
        assert result.items[0].reason_chip is None


# --- sort -----------------------------------------------------------------


class TestSort:
    def test_section_order_awaiting_before_handling_before_resolved(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        # Resolved (most recent activity).
        cR = _make_bot_user(tenant=tenant, name="Резолв Р", channel_user_id="cR")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cR,
            visit_local=datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
        )
        convR = _make_conversation(
            tenant=tenant,
            bot_user=cR,
            last_message_at=now - timedelta(minutes=5),
            is_active=False,
        )
        _add_message(
            tenant=tenant,
            conversation=convR,
            role=Message.Role.ASSISTANT,
            content="закрыто",
            created_at=now - timedelta(minutes=5),
        )
        # AI handling.
        cH = _make_bot_user(tenant=tenant, name="Хендл Х", channel_user_id="cH")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cH,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        convH = _make_conversation(
            tenant=tenant,
            bot_user=cH,
            last_message_at=now - timedelta(minutes=8),
        )
        _add_message(
            tenant=tenant,
            conversation=convH,
            role=Message.Role.USER,
            content="q",
            created_at=now - timedelta(minutes=15),
        )
        _add_message(
            tenant=tenant,
            conversation=convH,
            role=Message.Role.ASSISTANT,
            content="a",
            created_at=now - timedelta(minutes=8),
        )
        # Awaiting (oldest activity).
        cA = _make_bot_user(tenant=tenant, name="Авейт А", channel_user_id="cA")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cA,
            visit_local=datetime(2026, 5, 22, 13, 0, tzinfo=MSK),
        )
        convA = _make_conversation(
            tenant=tenant,
            bot_user=cA,
            last_message_at=now - timedelta(minutes=20),
        )
        _add_message(
            tenant=tenant,
            conversation=convA,
            role=Message.Role.USER,
            content="?",
            created_at=now - timedelta(minutes=20),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        sections = [i.section for i in result.items]
        assert sections == ["awaiting_master", "ai_handling", "resolved"]

    def test_within_awaiting_red_before_yellow_before_white(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))

        def _waiting(age_min: int, idx: int) -> None:
            c = _make_bot_user(tenant=tenant, name=f"K{idx}", channel_user_id=f"k-{idx}")
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=c,
                visit_local=datetime(2026, 5, 22, 12 + idx, 0, tzinfo=MSK),
            )
            conv = _make_conversation(
                tenant=tenant,
                bot_user=c,
                last_message_at=now - timedelta(minutes=age_min),
            )
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content="?",
                created_at=now - timedelta(minutes=age_min),
            )

        _waiting(5, 1)  # white
        _waiting(60, 2)  # yellow
        _waiting(360, 3)  # red
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="active")
        tiers = [i.sla_tier for i in result.items]
        assert tiers == ["red", "yellow", "white"]


# --- pagination -----------------------------------------------------------


class TestPagination:
    def test_cursor_round_trip(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        for i in range(5):
            c = _make_bot_user(
                tenant=tenant,
                name=f"C{i:02d} N",
                channel_user_id=f"p-{i}",
            )
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=c,
                visit_local=datetime(2026, 5, 22, 12 + i, 0, tzinfo=MSK),
            )
            conv = _make_conversation(
                tenant=tenant,
                bot_user=c,
                last_message_at=now - timedelta(minutes=10 + i),
            )
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content=f"msg-{i}",
                created_at=now - timedelta(minutes=10 + i),
            )

        page1 = conv_svc.list_master_conversations(
            accepted_master, now=now, filter="active", limit=2
        )
        assert len(page1.items) == 2
        assert page1.next_cursor is not None

        page2 = conv_svc.list_master_conversations(
            accepted_master, now=now, filter="active", limit=2, cursor=page1.next_cursor
        )
        assert len(page2.items) == 2
        # No overlap between pages.
        ids1 = {i.conversation_id for i in page1.items}
        ids2 = {i.conversation_id for i in page2.items}
        assert ids1.isdisjoint(ids2)

    def test_tampered_cursor_returns_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:conversations_list"),
            data={"cursor": "garbage:badsig"},
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_cursor"


# --- returning customer ---------------------------------------------------


class TestReturningCustomer:
    def test_multiple_bookings_marks_returning(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        customer = _make_bot_user(tenant=tenant)
        # Two completed bookings → returning.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 4, 1, 12, 0, tzinfo=MSK),
            completed=True,
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 5, 1, 12, 0, tzinfo=MSK),
            completed=True,
        )
        conv = _make_conversation(tenant=tenant, bot_user=customer, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=5),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now)
        assert len(result.items) == 1
        assert result.items[0].is_returning_customer is True

    def test_first_time_customer_marks_not_returning(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        customer = _make_bot_user(tenant=tenant)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=customer, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="hi",
            created_at=now - timedelta(minutes=5),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now)
        assert len(result.items) == 1
        assert result.items[0].is_returning_customer is False


# --- section counts -------------------------------------------------------


class TestSectionCounts:
    def test_counts_match_section_distribution(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        # awaiting (1)
        cA = _make_bot_user(tenant=tenant, name="A A", channel_user_id="counts-a")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cA,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        convA = _make_conversation(tenant=tenant, bot_user=cA, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=convA,
            role=Message.Role.USER,
            content="?",
            created_at=now - timedelta(minutes=10),
        )
        # ai_handling (1)
        cH = _make_bot_user(tenant=tenant, name="H H", channel_user_id="counts-h")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cH,
            visit_local=datetime(2026, 5, 22, 13, 0, tzinfo=MSK),
        )
        convH = _make_conversation(tenant=tenant, bot_user=cH, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=convH,
            role=Message.Role.ASSISTANT,
            content="a",
            created_at=now - timedelta(minutes=10),
        )
        # resolved today (1)
        cR = _make_bot_user(tenant=tenant, name="R R", channel_user_id="counts-r")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cR,
            visit_local=datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
        )
        convR = _make_conversation(
            tenant=tenant,
            bot_user=cR,
            last_message_at=now - timedelta(hours=3),
            is_active=False,
        )
        _add_message(
            tenant=tenant,
            conversation=convR,
            role=Message.Role.ASSISTANT,
            content="готово",
            created_at=now - timedelta(hours=3),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert result.section_counts.awaiting_master == 1
        assert result.section_counts.ai_handling == 1
        assert result.section_counts.resolved_today == 1
        assert result.section_counts.ai_drafted == 0


# --- AI-drafted section (#581) -------------------------------------------


def _seed_active_draft(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    conversation: Conversation,
    content: str = "Помощник: спасибо!",
    status: str = AiDraft.Status.ACTIVE,
) -> AiDraft:
    """Seed an :class:`AiDraft` for tests in this module.

    Mirrors the helper in :mod:`apps.master_api.tests.test_ai_drafts`.
    """

    return AiDraft.all_tenants.create(
        tenant=tenant,
        conversation=conversation,
        master=master,
        content=content,
        status=status,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
    )


class TestAiDraftedSection:
    """Verify M5 wires «ПРЕДЛОЖЕН ОТВЕТ (N)» counter to AiDraft (#581)."""

    def test_conversation_with_active_draft_classifies_as_ai_drafted(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant, name="Ксения Л.", channel_user_id="d1")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="хочу записаться",
            created_at=now - timedelta(minutes=5),
        )
        _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert len(result.items) == 1
        item = result.items[0]
        assert item.section == "ai_drafted"
        assert item.ai_drafted_reply_available is True

    def test_conversation_with_terminal_draft_does_not_appear_ai_drafted(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Terminal-status drafts (REPLACED/SENT/RELEASED/DISMISSED) must not
        bump the section. The partial unique constraint also guarantees these
        rows can coexist with a future ACTIVE row — here we test the
        no-active-row case."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        terminal_statuses = [
            AiDraft.Status.REPLACED,
            AiDraft.Status.SENT_AS_MASTER,
            AiDraft.Status.RELEASED_TO_AI,
            AiDraft.Status.DISMISSED,
        ]
        for idx, status in enumerate(terminal_statuses):
            client = _make_bot_user(
                tenant=tenant, name=f"Терм {idx}", channel_user_id=f"term-{idx}"
            )
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=client,
                visit_local=datetime(2026, 5, 22, 12 + idx, 0, tzinfo=MSK),
            )
            conv = _make_conversation(
                tenant=tenant, bot_user=client, last_message_at=now - timedelta(seconds=idx)
            )
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content="?",
                created_at=now - timedelta(minutes=5 + idx),
            )
            _seed_active_draft(
                tenant=tenant,
                master=accepted_master,
                conversation=conv,
                status=status,
            )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert len(result.items) == len(terminal_statuses)
        for item in result.items:
            assert item.section == "awaiting_master", item
            assert item.ai_drafted_reply_available is False, item

    def test_conversation_with_no_drafts_classifies_as_awaiting_master(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Baseline: no AiDraft rows at all → classic awaiting_master path."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant, name="Базовый Б.", channel_user_id="b1")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="?",
            created_at=now - timedelta(minutes=5),
        )
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert len(result.items) == 1
        assert result.items[0].section == "awaiting_master"
        assert result.items[0].ai_drafted_reply_available is False

    def test_active_draft_with_assistant_last_message_classifies_ai_handling(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Edge: ACTIVE draft + assistant-last message → ai_handling, NOT
        ai_drafted. Per service docstring: the bot/master already replied,
        so the draft is conceptually obsolete (PR #540 transitions such
        drafts on the next autotrigger sweep). We classify by ground truth."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        client = _make_bot_user(tenant=tenant, name="Эдж Э.", channel_user_id="edge-1")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv = _make_conversation(tenant=tenant, bot_user=client, last_message_at=now)
        # Customer asked, then assistant replied — last message is assistant.
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="?",
            created_at=now - timedelta(minutes=15),
        )
        _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="ответ",
            created_at=now - timedelta(minutes=5),
        )
        # Stale ACTIVE draft still lingering.
        _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert len(result.items) == 1
        item = result.items[0]
        assert item.section == "ai_handling"
        # The boolean still reports the underlying truth — only section
        # classification suppresses the double-classification.
        assert item.ai_drafted_reply_available is True

    def test_section_counts_ai_drafted_increments(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Mixed list: 2 ai_drafted + 1 awaiting + 1 ai_handling →
        section_counts.ai_drafted == 2."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))

        def _seed(idx: int, role: str, has_draft: bool) -> None:
            client = _make_bot_user(tenant=tenant, name=f"M {idx}", channel_user_id=f"mix-{idx}")
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=client,
                visit_local=datetime(2026, 5, 22, 12, idx * 5, tzinfo=MSK),
            )
            conv = _make_conversation(
                tenant=tenant, bot_user=client, last_message_at=now - timedelta(seconds=idx)
            )
            if role == Message.Role.ASSISTANT:
                _add_message(
                    tenant=tenant,
                    conversation=conv,
                    role=Message.Role.USER,
                    content="?",
                    created_at=now - timedelta(minutes=20 + idx),
                )
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=role,
                content="x",
                created_at=now - timedelta(minutes=10 + idx),
            )
            if has_draft:
                _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        _seed(0, Message.Role.USER, has_draft=True)
        _seed(1, Message.Role.USER, has_draft=True)
        _seed(2, Message.Role.USER, has_draft=False)
        _seed(3, Message.Role.ASSISTANT, has_draft=False)
        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        assert result.section_counts.ai_drafted == 2
        assert result.section_counts.awaiting_master == 1
        assert result.section_counts.ai_handling == 1

    def test_cross_tenant_isolation_ai_drafted(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        """Foreign-tenant AiDraft must NOT leak into master A's view even
        if a same-shape conversation exists. We seed a draft in tenant B
        for an unrelated conversation, plus a clean awaiting conversation
        for master A, and assert master A's view classifies as
        awaiting_master with ai_drafted_reply_available=False."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        # Master A's own awaiting conversation.
        client_a = _make_bot_user(tenant=tenant, name="A A", channel_user_id="ten-a")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=client_a,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        conv_a = _make_conversation(tenant=tenant, bot_user=client_a, last_message_at=now)
        _add_message(
            tenant=tenant,
            conversation=conv_a,
            role=Message.Role.USER,
            content="?",
            created_at=now - timedelta(minutes=5),
        )
        # Tenant B: build an unrelated draft on a separate conversation +
        # master. Wire the FK to a tenant-B master/conversation so the
        # data is internally consistent. This row's tenant_id differs
        # from accepted_master.tenant_id; the SQL Exists must reject it.
        other_client = BotUser.all_tenants.create(
            tenant=other_tenant,
            channel="max",
            channel_user_id="other-cli",
            display_name="Чужой",
            client_name="Чужой Клиент",
            chat_id="other-cli",
        )
        other_master = make_master(
            other_tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=other_client,
            name="Чужой Мастер",
        )
        other_conv = Conversation.all_tenants.create(
            tenant=other_tenant, bot_user=other_client, is_active=True
        )
        _seed_active_draft(tenant=other_tenant, master=other_master, conversation=other_conv)

        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        # Only master A's own conversation; classified as awaiting_master.
        assert len(result.items) == 1
        item = result.items[0]
        assert item.conversation_id == str(conv_a.id)
        assert item.section == "awaiting_master"
        assert item.ai_drafted_reply_available is False
        assert result.section_counts.ai_drafted == 0

    def test_n_plus_one_query_count_bounded(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """Listing 20 conversations each with an ACTIVE draft must NOT
        scale SQL query count linearly. The AiDraft predicate is wired
        as a single Exists annotation; the page must fit in a constant
        number of queries (≤ ~10 regardless of N)."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        for i in range(20):
            client = _make_bot_user(tenant=tenant, name=f"N {i}", channel_user_id=f"n-{i}")
            # Stagger visit times so the (master_id, visit_at) unique
            # constraint doesn't trip — booking dates are irrelevant to
            # the query-count assertion.
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=client,
                visit_local=datetime(2026, 5, 22, 8, 0, tzinfo=MSK) + timedelta(minutes=i * 15),
            )
            conv = _make_conversation(
                tenant=tenant, bot_user=client, last_message_at=now - timedelta(seconds=i)
            )
            _add_message(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content="?",
                created_at=now - timedelta(minutes=5 + i),
            )
            _seed_active_draft(tenant=tenant, master=accepted_master, conversation=conv)

        # Warm any one-shot bootstrap queries (auth-style preflights).
        conv_svc.list_master_conversations(accepted_master, now=now, filter="all", limit=50)

        with CaptureQueriesContext(connection) as ctx:
            result = conv_svc.list_master_conversations(
                accepted_master, now=now, filter="all", limit=50
            )
        # All 20 should be classified ai_drafted.
        ai_drafted_items = [i for i in result.items if i.section == "ai_drafted"]
        assert len(ai_drafted_items) == 20
        # Bound: the service issues a fixed handful of queries regardless
        # of N — conversation list, last-message-overall, last-user-
        # message, returning-customer aggregate, plus annotation. A
        # generous ceiling of 10 catches a real N+1 regression (which
        # would push the count past 20).
        assert len(ctx.captured_queries) <= 10, (
            f"expected ≤10 queries, got {len(ctx.captured_queries)}\n"
            + "\n".join(q["sql"] for q in ctx.captured_queries)
        )

    def test_section_sort_order_ai_drafted_between_awaiting_and_ai_handling(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        """With one of each section visible, output order must be
        awaiting_master → ai_drafted → ai_handling → resolved."""

        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))

        # Resolved (recent).
        cR = _make_bot_user(tenant=tenant, name="Резолв", channel_user_id="ord-r")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cR,
            visit_local=datetime(2026, 5, 20, 12, 0, tzinfo=MSK),
        )
        convR = _make_conversation(
            tenant=tenant,
            bot_user=cR,
            last_message_at=now - timedelta(minutes=5),
            is_active=False,
        )
        _add_message(
            tenant=tenant,
            conversation=convR,
            role=Message.Role.ASSISTANT,
            content="закрыто",
            created_at=now - timedelta(minutes=5),
        )
        # AI handling.
        cH = _make_bot_user(tenant=tenant, name="Хендл", channel_user_id="ord-h")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cH,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        convH = _make_conversation(
            tenant=tenant, bot_user=cH, last_message_at=now - timedelta(minutes=8)
        )
        _add_message(
            tenant=tenant,
            conversation=convH,
            role=Message.Role.USER,
            content="q",
            created_at=now - timedelta(minutes=15),
        )
        _add_message(
            tenant=tenant,
            conversation=convH,
            role=Message.Role.ASSISTANT,
            content="a",
            created_at=now - timedelta(minutes=8),
        )
        # Awaiting.
        cA = _make_bot_user(tenant=tenant, name="Авейт", channel_user_id="ord-a")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cA,
            visit_local=datetime(2026, 5, 22, 13, 0, tzinfo=MSK),
        )
        convA = _make_conversation(
            tenant=tenant, bot_user=cA, last_message_at=now - timedelta(minutes=20)
        )
        _add_message(
            tenant=tenant,
            conversation=convA,
            role=Message.Role.USER,
            content="?",
            created_at=now - timedelta(minutes=20),
        )
        # AI drafted (between awaiting and ai_handling per _SECTION_ORDER).
        cD = _make_bot_user(tenant=tenant, name="Драфт", channel_user_id="ord-d")
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=cD,
            visit_local=datetime(2026, 5, 22, 14, 0, tzinfo=MSK),
        )
        convD = _make_conversation(
            tenant=tenant, bot_user=cD, last_message_at=now - timedelta(minutes=10)
        )
        _add_message(
            tenant=tenant,
            conversation=convD,
            role=Message.Role.USER,
            content="?",
            created_at=now - timedelta(minutes=10),
        )
        _seed_active_draft(tenant=tenant, master=accepted_master, conversation=convD)

        result = conv_svc.list_master_conversations(accepted_master, now=now, filter="all")
        sections = [i.section for i in result.items]
        assert sections == ["awaiting_master", "ai_drafted", "ai_handling", "resolved"]


# --- helpers --------------------------------------------------------------


class TestServiceHelpers:
    def test_sla_tier_thresholds(self) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        assert conv_svc._sla_tier(None, now) == "white"
        assert conv_svc._sla_tier(now - timedelta(minutes=5), now) == "white"
        assert conv_svc._sla_tier(now - timedelta(minutes=45), now) == "yellow"
        assert conv_svc._sla_tier(now - timedelta(hours=5), now) == "red"

    def test_section_for_explicit_branches(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        customer = _make_bot_user(tenant=tenant)
        # awaiting
        conv = _make_conversation(tenant=tenant, bot_user=customer)
        msg = _add_message(tenant=tenant, conversation=conv, role=Message.Role.USER, content="q")
        assert conv_svc._section_for(conv, msg) == "awaiting_master"
        # ai_handling
        msg2 = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.ASSISTANT,
            content="a",
        )
        assert conv_svc._section_for(conv, msg2) == "ai_handling"
        # resolved
        conv.is_active = False
        assert conv_svc._section_for(conv, msg2) == "resolved"
        # other (tool role)
        conv.is_active = True
        msg3 = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.TOOL,
            content="tool",
        )
        assert conv_svc._section_for(conv, msg3) == "other"
        # ai_drafted — customer-last + has_active_draft=True (#581).
        msg_user = _add_message(
            tenant=tenant,
            conversation=conv,
            role=Message.Role.USER,
            content="q again",
        )
        assert conv_svc._section_for(conv, msg_user, has_active_draft=True) == "ai_drafted"
        # Plain awaiting_master when has_active_draft is omitted (default).
        assert conv_svc._section_for(conv, msg_user) == "awaiting_master"

    def test_is_returning_helper(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        customer = _make_bot_user(tenant=tenant)
        assert conv_svc._is_returning(customer, accepted_master) is False
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 5, 22, 12, 0, tzinfo=MSK),
        )
        assert conv_svc._is_returning(customer, accepted_master) is False
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            visit_local=datetime(2026, 5, 23, 12, 0, tzinfo=MSK),
        )
        assert conv_svc._is_returning(customer, accepted_master) is True


# --- validation -----------------------------------------------------------


class TestValidation:
    def test_bad_filter_returns_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:conversations_list") + "?filter=invalid",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_limit_over_max_returns_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:conversations_list") + "?limit=999",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_non_integer_limit_returns_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:conversations_list") + "?limit=abc",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400
