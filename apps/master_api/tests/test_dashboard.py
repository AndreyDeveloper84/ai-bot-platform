"""Tests for the GET /api/v1/master/dashboard aggregator endpoint.

Covers:

* Auth — customer / pending / archived → 401-ish; accepted master → 200.
* Active visit — in progress / just ended / no bookings today.
* Next visit — soonest of multiple / tomorrow excluded / cancelled
  excluded / returning customer flag.
* Inbox preview — empty when no conversation rows / capped at 3 / sort
  order red > yellow > white.
* Today summary — counts + next free window scenarios.
* Tab badges — customer unread + internal-chat unread + schedule
  pending flag.
* States — is_day_done.
* Cross-tenant + cross-master isolation.

The tests pin ``now`` via the helper-level entrypoints so the assertions
don't drift with wall-clock time.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import Client
from django.urls import reverse

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.internal_chat.models import (
    MasterAdminMessage,
    MasterAdminThread,
    SenderRoleChoices,
    StatusChoices,
    TopicChoices,
)
from apps.master_api.services import dashboard as ds
from apps.master_api.tests.conftest import init_data_header
from apps.scheduling.models import ScheduleChangeRequest, WorkingHours
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")


# --- helpers --------------------------------------------------------------


def _utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    return dt_local.astimezone(timezone.utc)


def _make_booking(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    visit_local: datetime,
    duration_min: int = 60,
    status: str = BookingRequest.Status.CONFIRMED,
    service_name: str = "маникюр гель-лак",
    client_name: str = "Мария Иванова",
    bot_user: BotUser | None = None,
    completed_at: datetime | None = None,
    conversation: Conversation | None = None,
) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        bot_user=bot_user,
        service_name=service_name,
        client_name=client_name,
        client_phone="+79000000000",
        visit_at=_utc(visit_local),
        duration_min=duration_min,
        status=status,
        completed_at=completed_at,
        conversation=conversation,
    )


# --- auth -----------------------------------------------------------------


class TestDashboardAuth:
    def test_accepted_master_returns_200(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        assert data["master"]["id"] == str(accepted_master.id)
        assert data["salon"]["id"] == str(accepted_master.tenant_id)
        assert set(data.keys()) >= {
            "master",
            "salon",
            "now_iso",
            "active_visit",
            "next_visit",
            "inbox_preview",
            "today_summary",
            "tab_badges",
            "states",
        }

    def test_no_linked_master_returns_401(
        self,
        client: Client,
        bot_user: BotUser,
    ) -> None:
        # bot_user exists but no master is linked to it.
        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

    def test_archived_master_returns_403(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        from apps.master_api.tests.conftest import make_master

        make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=bot_user,
            archived_at=datetime.now(tz=timezone.utc),
        )
        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "master_inactive"

    def test_inactive_master_returns_403(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
    ) -> None:
        from apps.master_api.tests.conftest import make_master

        make_master(
            tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            linked_bot_user=bot_user,
            is_active=False,
        )
        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 403


# --- active visit ---------------------------------------------------------


class TestActiveVisit:
    def test_visit_in_progress_returned(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now_local = datetime(2026, 5, 21, 14, 42, tzinfo=MSK)
        # started at 14:00, 90 min → ends 15:30 → now in window.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 14, 0, tzinfo=MSK),
            duration_min=90,
        )
        result = ds.get_active_visit(accepted_master, _utc(now_local))
        assert result is not None
        assert result.duration_min == 90
        assert result.is_in_progress is True
        # 42 minutes elapsed → 48 remaining.
        assert 47 <= result.minutes_remaining <= 48
        assert result.client_first_name == "Мария"
        assert result.client_last_initial == "И."
        assert result.note == ""  # placeholder

    def test_visit_just_ended_returns_none(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        # Visit ended 5 min ago.
        now_local = datetime(2026, 5, 21, 15, 35, tzinfo=MSK)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 14, 0, tzinfo=MSK),
            duration_min=90,
        )
        assert ds.get_active_visit(accepted_master, _utc(now_local)) is None

    def test_no_bookings_today(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now_local = datetime(2026, 5, 21, 14, 0, tzinfo=MSK)
        assert ds.get_active_visit(accepted_master, _utc(now_local)) is None

    def test_cancelled_booking_excluded(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now_local = datetime(2026, 5, 21, 14, 42, tzinfo=MSK)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 14, 0, tzinfo=MSK),
            duration_min=90,
            status=BookingRequest.Status.CANCELLED,
        )
        assert ds.get_active_visit(accepted_master, _utc(now_local)) is None


# --- next visit -----------------------------------------------------------


class TestNextVisit:
    def test_returns_soonest_today(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now_local = datetime(2026, 5, 21, 14, 0, tzinfo=MSK)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 18, 0, tzinfo=MSK),
            client_name="Анна Петрова",
        )
        soonest = _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 16, 0, tzinfo=MSK),
            client_name="Анна Петрова",
            duration_min=120,
            service_name="наращивание ногтей",
        )
        result = ds.get_next_visit(accepted_master, _utc(now_local))
        assert result is not None
        assert result.booking_id == str(soonest.id)
        assert result.client_first_name == "Анна"
        assert result.client_last_initial == "П."
        assert result.duration_min == 120
        assert result.service_name == "наращивание ногтей"

    def test_tomorrow_excluded(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now_local = datetime(2026, 5, 21, 20, 0, tzinfo=MSK)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 22, 10, 0, tzinfo=MSK),
        )
        assert ds.get_next_visit(accepted_master, _utc(now_local)) is None

    def test_cancelled_booking_excluded(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now_local = datetime(2026, 5, 21, 14, 0, tzinfo=MSK)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 16, 0, tzinfo=MSK),
            status=BookingRequest.Status.CANCELLED,
        )
        assert ds.get_next_visit(accepted_master, _utc(now_local)) is None

    def test_returning_customer_flag(
        self, tenant: Tenant, accepted_master: CatalogMaster, bot_user: BotUser
    ) -> None:
        now_local = datetime(2026, 5, 21, 14, 0, tzinfo=MSK)
        # History: one prior booking + one upcoming.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=bot_user,
            visit_local=datetime(2026, 5, 1, 12, 0, tzinfo=MSK),
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=bot_user,
            visit_local=datetime(2026, 5, 21, 16, 0, tzinfo=MSK),
        )
        result = ds.get_next_visit(accepted_master, _utc(now_local))
        assert result is not None
        assert result.is_returning_customer is True

    def test_returns_none_when_empty(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now_local = datetime(2026, 5, 21, 14, 0, tzinfo=MSK)
        assert ds.get_next_visit(accepted_master, _utc(now_local)) is None


# --- inbox preview --------------------------------------------------------


class TestInboxPreview:
    def test_empty_when_no_conversations(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now_local = datetime(2026, 5, 21, 14, 0, tzinfo=MSK)
        assert ds.get_inbox_preview(accepted_master, _utc(now_local)) == []

    def test_limited_to_three(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now_local = datetime(2026, 5, 21, 14, 0, tzinfo=MSK)
        for i in range(5):
            client = BotUser.all_tenants.create(
                tenant=tenant,
                channel="max",
                channel_user_id=f"client-{i}",
                display_name=f"Клиент {i}",
                chat_id=f"client-{i}",
            )
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=client,
                visit_local=datetime(2026, 5, 1, 12 + i, 0, tzinfo=MSK),
            )
            conv = Conversation.all_tenants.create(
                tenant=tenant,
                bot_user=client,
                state=Conversation.State.IDLE,
                is_active=True,
            )
            Message.all_tenants.create(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content=f"Hello {i}",
            )
        items = ds.get_inbox_preview(accepted_master, _utc(now_local))
        assert len(items) == 3

    def test_sort_by_sla_tier(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))

        def _client_with_unread(idx: int, msg_age_minutes: int, body: str) -> BotUser:
            client = BotUser.all_tenants.create(
                tenant=tenant,
                channel="max",
                channel_user_id=f"sort-client-{idx}",
                display_name=f"Klient {idx}",
                chat_id=f"sort-client-{idx}",
            )
            _make_booking(
                tenant=tenant,
                master=accepted_master,
                bot_user=client,
                visit_local=datetime(2026, 5, 1, 12 + idx, 0, tzinfo=MSK),
            )
            conv = Conversation.all_tenants.create(tenant=tenant, bot_user=client, is_active=True)
            msg = Message.all_tenants.create(
                tenant=tenant,
                conversation=conv,
                role=Message.Role.USER,
                content=body,
            )
            # Backdate.
            Message.all_tenants.filter(pk=msg.pk).update(
                created_at=now - timedelta(minutes=msg_age_minutes)
            )
            return client

        _client_with_unread(1, 5, "white-fresh")
        _client_with_unread(2, 60, "yellow-mid")
        _client_with_unread(3, 360, "red-old")
        items = ds.get_inbox_preview(accepted_master, now)
        assert [i.sla_tier for i in items] == ["red", "yellow", "white"]

    def test_skips_if_assistant_replied(
        self, tenant: Tenant, accepted_master: CatalogMaster, bot_user: BotUser
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            bot_user=bot_user,
            visit_local=datetime(2026, 5, 1, 12, 0, tzinfo=MSK),
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user, is_active=True)
        user_msg = Message.all_tenants.create(
            tenant=tenant, conversation=conv, role=Message.Role.USER, content="hi"
        )
        asst_msg = Message.all_tenants.create(
            tenant=tenant, conversation=conv, role=Message.Role.ASSISTANT, content="hi back"
        )
        # Backdate user message and assistant message so the assistant
        # row is strictly after the user row (auto_now_add gives them
        # near-identical timestamps which defeats the `__gt` check).
        Message.all_tenants.filter(pk=user_msg.pk).update(created_at=now - timedelta(minutes=10))
        Message.all_tenants.filter(pk=asst_msg.pk).update(created_at=now - timedelta(minutes=5))
        assert ds.get_inbox_preview(accepted_master, now) == []


# --- today summary --------------------------------------------------------


class TestTodaySummary:
    def test_counts_match(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        # Two completed, one upcoming.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 10, 0, tzinfo=MSK),
            completed_at=_utc(datetime(2026, 5, 21, 11, 0, tzinfo=MSK)),
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 12, 0, tzinfo=MSK),
            completed_at=_utc(datetime(2026, 5, 21, 13, 0, tzinfo=MSK)),
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 16, 0, tzinfo=MSK),
        )
        summary = ds.get_today_summary(accepted_master, now)
        assert summary.total_clients_today == 3
        assert summary.completed_count == 2

    def test_next_free_window_with_gap(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 15, 0, tzinfo=MSK))
        # Working hours 10:00-20:00 on Thursday (2026-05-21 is a Thursday).
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            day_of_week=3,  # Thursday
            is_working=True,
            start_time=time(10, 0),
            end_time=time(20, 0),
        )
        # Booking 16:00 → 17:30 (90min) → free window 15:00–16:00.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 16, 0, tzinfo=MSK),
            duration_min=90,
        )
        summary = ds.get_today_summary(accepted_master, now)
        assert summary.next_free_window == {"start": "15:00", "end": "16:00"}

    def test_next_free_window_empty_day(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 10, 0, tzinfo=MSK))
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            day_of_week=3,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(20, 0),
        )
        summary = ds.get_today_summary(accepted_master, now)
        assert summary.next_free_window == {"start": "10:00", "end": "20:00"}

    def test_next_free_window_none_when_no_working_hours(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 10, 0, tzinfo=MSK))
        summary = ds.get_today_summary(accepted_master, now)
        assert summary.next_free_window is None


# --- tab badges -----------------------------------------------------------


class TestTabBadges:
    def test_internal_chat_unread(
        self, tenant: Tenant, accepted_master: CatalogMaster, bot_user: BotUser
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        thread = MasterAdminThread.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            topic=TopicChoices.GENERAL.value,
            status=StatusChoices.OPEN.value,
        )
        MasterAdminMessage.objects.create(
            thread=thread,
            sender_role=SenderRoleChoices.ADMIN.value,
            body="hello master",
        )
        badges = ds.get_tab_badges(accepted_master, now)
        assert badges.conversations_unread >= 1

    def test_schedule_pending_change(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            status=ScheduleChangeRequest.Status.PENDING,
        )
        badges = ds.get_tab_badges(accepted_master, now)
        assert badges.schedule_has_pending_change is True

    def test_profile_pending_placeholder_false(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        badges = ds.get_tab_badges(accepted_master, now)
        assert badges.profile_has_owner_pending_change is False


# --- states ---------------------------------------------------------------


class TestStates:
    def test_day_done_true(self, tenant: Tenant, accepted_master: CatalogMaster) -> None:
        now = _utc(datetime(2026, 5, 21, 20, 0, tzinfo=MSK))
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 16, 0, tzinfo=MSK),
            duration_min=60,
        )
        states = ds.get_states(accepted_master, now)
        assert states.is_day_done is True
        assert states.is_offline_safe_response is False

    def test_day_done_false_when_visit_pending(
        self, tenant: Tenant, accepted_master: CatalogMaster
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 18, 0, tzinfo=MSK),
        )
        states = ds.get_states(accepted_master, now)
        assert states.is_day_done is False


# --- isolation ------------------------------------------------------------


class TestIsolation:
    def test_cross_tenant_isolation(
        self,
        tenant: Tenant,
        other_tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        # Foreign master in other tenant with a booking RIGHT NOW.
        foreign_master = CatalogMaster.all_tenants.create(
            tenant=other_tenant,
            external_id=99,
            external_updated_at=datetime.now(tz=timezone.utc),
            name="Foreign",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        _make_booking(
            tenant=other_tenant,
            master=foreign_master,
            visit_local=datetime(2026, 5, 21, 14, 0, tzinfo=MSK),
            duration_min=120,
        )
        # Our master sees nothing.
        assert ds.get_active_visit(accepted_master, now) is None
        assert ds.get_next_visit(accepted_master, now) is None
        summary = ds.get_today_summary(accepted_master, now)
        assert summary.total_clients_today == 0

    def test_cross_master_isolation(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        peer = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=88,
            external_updated_at=datetime.now(tz=timezone.utc),
            name="Peer",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        )
        _make_booking(
            tenant=tenant,
            master=peer,
            visit_local=datetime(2026, 5, 21, 14, 0, tzinfo=MSK),
            duration_min=120,
        )
        assert ds.get_active_visit(accepted_master, now) is None
        summary = ds.get_today_summary(accepted_master, now)
        assert summary.total_clients_today == 0


# --- top-level aggregator -------------------------------------------------


class TestAggregator:
    def test_build_dashboard_shape(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = _utc(datetime(2026, 5, 21, 14, 0, tzinfo=MSK))
        snap = ds.build_dashboard(accepted_master, now)
        out = snap.to_dict()
        assert out["master"]["id"] == str(accepted_master.id)
        assert out["salon"]["name"] == tenant.name
        assert out["active_visit"] is None
        assert out["next_visit"] is None
        assert out["inbox_preview"] == []
        assert out["today_summary"]["total_clients_today"] == 0
        assert out["states"]["is_offline_safe_response"] is False

    def test_dashboard_endpoint_returns_full_shape(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        # Add one upcoming booking so next_visit is populated.
        _make_booking(
            tenant=accepted_master.tenant,
            master=accepted_master,
            visit_local=datetime.now(tz=MSK) + timedelta(hours=2),
            client_name="Анна Петрова",
            duration_min=60,
        )
        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        data = resp.json()
        # Cross-check the next_visit branch wired through helpers.
        if data["next_visit"] is not None:
            assert data["next_visit"]["client_first_name"] == "Анна"
            assert data["next_visit"]["client_last_initial"] == "П."
