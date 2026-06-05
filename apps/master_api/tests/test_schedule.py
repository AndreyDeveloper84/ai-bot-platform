"""Tests for the M3 master schedule + availability endpoints (PR Tier1.2).

Covers:
* Auth — customer / pending / archived / accepted master.
* GET /schedule — date range, working hours, off days, blocks, free
  windows, conflicts, isolation.
* POST /availability — validation, overlap check, audit, event, manager DM.
* GET /availability/pending — filter, ordering, isolation.

The tests pin ``now`` at the helper-level entrypoints when needed so
assertions don't drift with wall-clock time. View-level tests use a
fixed tenant TZ (Europe/Moscow) and reasonable defaults.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.master_api.services import schedule as sched
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.scheduling.models import (
    ScheduleChangeRequest,
    ScheduleException,
    Weekday,
    WorkingHours,
)
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")


# --- helpers --------------------------------------------------------------


def _utc(dt_local: datetime) -> datetime:
    """Tenant-local naive/aware → UTC aware."""

    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    return dt_local.astimezone(timezone.utc)


def _make_working_hours(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    weekday: int,
    start: time = time(10, 0),
    end: time = time(19, 0),
    is_working: bool = True,
) -> WorkingHours:
    return WorkingHours.all_tenants.create(
        tenant=tenant,
        master=master,
        day_of_week=weekday,
        is_working=is_working,
        start_time=start if is_working else None,
        end_time=end if is_working else None,
    )


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
    )


def _make_exception(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    on_date: date,
    type_: str = ScheduleException.Type.VACATION,
    start_time: time | None = None,
    end_time: time | None = None,
) -> ScheduleException:
    return ScheduleException.all_tenants.create(
        tenant=tenant,
        master=master,
        date=on_date,
        type=type_,
        start_time=start_time,
        end_time=end_time,
    )


# Common fixture seed: working hours Mon-Fri 10:00-19:00 for accepted_master.
@pytest.fixture
def workdays(tenant: Tenant, accepted_master: CatalogMaster) -> list[WorkingHours]:
    return [
        _make_working_hours(tenant=tenant, master=accepted_master, weekday=wd)
        for wd in [
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
        ]
    ]


# --- auth -----------------------------------------------------------------


class TestScheduleAuth:
    def test_customer_unlinked_returns_401(self, client: Client, bot_user: BotUser) -> None:
        resp = client.get(
            reverse("master_api:schedule"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "not_a_master"

    def test_pending_master_returns_401(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        pending_master: CatalogMaster,
    ) -> None:
        # PENDING master is not yet linked to bot_user → 401 not_a_master.
        resp = client.get(
            reverse("master_api:schedule"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 401

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
            reverse("master_api:schedule"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 403

    def test_accepted_master_returns_200(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        resp = client.get(
            reverse("master_api:schedule"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["tenant_tz"] == "Europe/Moscow"
        assert "days" in body
        assert len(body["days"]) == 7  # default range = 7d


# --- GET /schedule --------------------------------------------------------


class TestScheduleEndpoint:
    def test_returns_own_bookings_in_range(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        tenant: Tenant,
        workdays: list[WorkingHours],
    ) -> None:
        # Wed 21 May 2026 = weekday 2 (Wednesday)
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 11, 0),
            duration_min=60,
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 14, 0),
            duration_min=90,
        )
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-21&to=2026-05-21",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert len(body["days"]) == 1
        day = body["days"][0]
        assert day["date"] == "2026-05-21"
        assert len(day["bookings"]) == 2
        # Sorted asc by visit_at
        assert day["bookings"][0]["visit_at"] < day["bookings"][1]["visit_at"]
        assert day["bookings"][0]["duration_min"] == 60
        assert day["bookings"][1]["duration_min"] == 90
        # PII trimmed: first name + last initial only.
        assert day["bookings"][0]["client_first_name"] == "Мария"
        assert day["bookings"][0]["client_last_initial"] == "И."

    def test_working_hours_from_workinghours_row(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-21&to=2026-05-21",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        day = resp.json()["days"][0]
        assert day["working_hours"] == {"start": "10:00", "end": "19:00"}
        assert day["is_off_day"] is False

    def test_off_day_when_no_workinghours(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        # Sat May 23 has no WorkingHours row → off_day.
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-23&to=2026-05-23",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        day = resp.json()["days"][0]
        assert day["is_off_day"] is True
        assert day["working_hours"] is None

    def test_blocks_from_approved_exception(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        _make_exception(
            tenant=tenant,
            master=accepted_master,
            on_date=date(2026, 5, 21),
            type_=ScheduleException.Type.VACATION,
        )
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-21&to=2026-05-21",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        day = resp.json()["days"][0]
        assert len(day["blocks"]) == 1
        assert day["blocks"][0]["reason"] == "vacation"
        assert day["blocks"][0]["approved"] is True

    def test_free_windows_min_30_min(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        # Wed 21 May, working 10-19. Booking 11-12, booking 14-15. Gaps:
        # 10-11 (60min), 12-14 (120min), 15-19 (240min).
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 11, 0),
            duration_min=60,
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 14, 0),
            duration_min=60,
        )
        result = sched.build_schedule(
            accepted_master,
            from_date=date(2026, 5, 21),
            to_date=date(2026, 5, 21),
            now=_utc(datetime(2026, 5, 21, 9, 0)),
        )
        day = result.days[0]
        windows = [(w.start, w.end, w.duration_min) for w in day.free_windows]
        assert ("10:00", "11:00", 60) in windows
        assert ("12:00", "14:00", 120) in windows
        assert ("15:00", "19:00", 240) in windows

    def test_free_windows_sub_30min_gap_excluded(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        # 10:00-11:00 + 11:15-12:15 leaves a 15min gap which must NOT
        # be surfaced.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 10, 0),
            duration_min=60,
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 11, 15),
            duration_min=60,
        )
        result = sched.build_schedule(
            accepted_master,
            from_date=date(2026, 5, 21),
            to_date=date(2026, 5, 21),
        )
        starts = [(w.start, w.end) for w in result.days[0].free_windows]
        assert ("11:00", "11:15") not in starts

    def test_is_in_progress_computed(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 14, 0),
            duration_min=90,
        )
        # now is 14:30 MSK → mid-booking
        now = _utc(datetime(2026, 5, 21, 14, 30))
        result = sched.build_schedule(
            accepted_master,
            from_date=date(2026, 5, 21),
            to_date=date(2026, 5, 21),
            now=now,
        )
        assert result.days[0].bookings[0].is_in_progress is True

    def test_cross_master_isolation(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        # Create a second master with a booking. Master A must not see B's.
        other = make_master(
            tenant,
            name="Карина Б.",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            external_id=999,
        )
        _make_booking(
            tenant=tenant,
            master=other,
            visit_local=datetime(2026, 5, 21, 13, 0),
            duration_min=60,
            client_name="Клиент Б.",
        )
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-21&to=2026-05-21",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        day = resp.json()["days"][0]
        assert day["bookings"] == []

    def test_cross_tenant_isolation(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        other_tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        other_master = make_master(
            other_tenant,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
        )
        _make_booking(
            tenant=other_tenant,
            master=other_master,
            visit_local=datetime(2026, 5, 21, 13, 0),
            duration_min=60,
            client_name="Чужой Клиент",
        )
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-21&to=2026-05-21",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        day = resp.json()["days"][0]
        assert day["bookings"] == []

    def test_from_greater_than_to_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-21&to=2026-05-20",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_range_over_31d_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:schedule") + "?from=2026-05-01&to=2026-06-15",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_invalid_date_format_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        resp = client.get(
            reverse("master_api:schedule") + "?from=not-a-date",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400


# --- conflicts -------------------------------------------------------------


class TestScheduleConflicts:
    def test_double_booking_surfaces(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        # Two CONFIRMED overlapping bookings created via all_tenants
        # manager to bypass any race protection.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 11, 0),
            duration_min=120,  # ends 13:00
        )
        # Second booking starts at 12:00 — overlaps 11:00-13:00 window.
        # Avoid the partial-UNIQUE on (master, visit_at) by using a
        # distinct visit_at value.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 12, 0),
            duration_min=60,
        )
        result = sched.build_schedule(
            accepted_master,
            from_date=date(2026, 5, 21),
            to_date=date(2026, 5, 21),
        )
        types = {c.type for c in result.days[0].conflicts}
        assert "double_booking" in types

    def test_booking_outside_working_hours(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        # Working hours 10-19; booking at 09:00 = outside.
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 9, 0),
            duration_min=60,
        )
        result = sched.build_schedule(
            accepted_master,
            from_date=date(2026, 5, 21),
            to_date=date(2026, 5, 21),
        )
        types = {c.type for c in result.days[0].conflicts}
        assert "outside_hours" in types

    def test_booking_inside_approved_exception(
        self,
        tenant: Tenant,
        accepted_master: CatalogMaster,
        workdays: list[WorkingHours],
    ) -> None:
        # Vacation on this date + a CONFIRMED booking inside it.
        _make_exception(
            tenant=tenant,
            master=accepted_master,
            on_date=date(2026, 5, 21),
            type_=ScheduleException.Type.VACATION,
        )
        _make_booking(
            tenant=tenant,
            master=accepted_master,
            visit_local=datetime(2026, 5, 21, 11, 0),
            duration_min=60,
        )
        result = sched.build_schedule(
            accepted_master,
            from_date=date(2026, 5, 21),
            to_date=date(2026, 5, 21),
        )
        types = {c.type for c in result.days[0].conflicts}
        assert "overlapping_exception" in types


# --- POST /availability ----------------------------------------------------


class TestAvailabilityRequest:
    def test_valid_request_creates_row(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        start = _utc(datetime(2026, 5, 22, 9, 0))
        end = _utc(datetime(2026, 5, 26, 18, 0))
        resp = client.post(
            reverse("master_api:availability_request"),
            data={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason_class": "vacation",
                "reason_text": "семейная поездка",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["status"] == "pending"
        assert body["reason_class"] == "vacation"
        req = ScheduleChangeRequest.all_tenants.get(id=body["request_id"])
        assert req.master_id == accepted_master.id
        assert req.requested_by_id == bot_user.id
        assert req.reason_text == "семейная поездка"

        # Audit row landed.
        log = AuditLog.all_tenants.filter(
            action="master.availability_change_requested",
            target_id=req.id,
        ).first()
        assert log is not None

    def test_overlap_with_approved_exception_400(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        _make_exception(
            tenant=tenant,
            master=accepted_master,
            on_date=date(2026, 5, 23),
            type_=ScheduleException.Type.VACATION,
        )
        start = _utc(datetime(2026, 5, 23, 9, 0))
        end = _utc(datetime(2026, 5, 24, 18, 0))
        resp = client.post(
            reverse("master_api:availability_request"),
            data={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason_class": "vacation",
                "reason_text": "",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "overlap"

    def test_start_geq_end_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        start = _utc(datetime(2026, 5, 25, 18, 0))
        end = _utc(datetime(2026, 5, 25, 9, 0))
        resp = client.post(
            reverse("master_api:availability_request"),
            data={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason_class": "vacation",
                "reason_text": "",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_start_in_past_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        # Both start AND end in the past.
        start = datetime.now(tz=timezone.utc) - timedelta(days=5)
        end = datetime.now(tz=timezone.utc) - timedelta(days=2)
        resp = client.post(
            reverse("master_api:availability_request"),
            data={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason_class": "vacation",
                "reason_text": "",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    def test_today_remaining_hours_allowed(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        # start = now, end = +4h — must succeed (only end-in-past blocks).
        now = datetime.now(tz=timezone.utc)
        start = now + timedelta(minutes=5)
        end = now + timedelta(hours=4)
        resp = client.post(
            reverse("master_api:availability_request"),
            data={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason_class": "sick",
                "reason_text": "температура",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 201

    def test_invalid_reason_class_400(
        self,
        client: Client,
        bot_user: BotUser,
        accepted_master: CatalogMaster,
    ) -> None:
        start = _utc(datetime(2026, 5, 25, 9, 0))
        end = _utc(datetime(2026, 5, 25, 18, 0))
        resp = client.post(
            reverse("master_api:availability_request"),
            data={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "reason_class": "made_up",
                "reason_text": "",
            },
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 400

    @pytest.mark.django_db(transaction=True)
    def test_manager_dm_dispatched_on_commit(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        tenant.manager_chat_id = "777"
        tenant.save(update_fields=["manager_chat_id"])
        start = _utc(datetime(2026, 5, 25, 9, 0))
        end = _utc(datetime(2026, 5, 25, 18, 0))
        with patch("apps.channels.max.outbound.send_message") as send:
            resp = client.post(
                reverse("master_api:availability_request"),
                data={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "reason_class": "personal",
                    "reason_text": "",
                },
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("12345"),
            )
            assert resp.status_code == 201, resp.content
            # transaction=True flushes on_commit hooks at the boundary
            # of the view's atomic block — they have fired by the time
            # the response returns.
            assert send.called, "manager DM was not dispatched"
            _args, kwargs = send.call_args
            assert kwargs["chat_id"] == "777"
            assert "Анна" in kwargs["text"] or accepted_master.name in kwargs["text"]

    @pytest.mark.django_db(transaction=True)
    def test_no_manager_chat_id_skips_dm(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        # tenant fixture has empty manager_chat_id by default.
        assert tenant.manager_chat_id == ""
        start = _utc(datetime(2026, 5, 25, 9, 0))
        end = _utc(datetime(2026, 5, 25, 18, 0))
        with patch("apps.channels.max.outbound.send_message") as send:
            resp = client.post(
                reverse("master_api:availability_request"),
                data={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "reason_class": "vacation",
                    "reason_text": "",
                },
                content_type="application/json",
                HTTP_AUTHORIZATION=init_data_header("12345"),
            )
            assert resp.status_code == 201
            assert not send.called


# --- GET /availability/pending ---------------------------------------------


class TestAvailabilityPending:
    def test_returns_own_pending(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            status=ScheduleChangeRequest.Status.PENDING,
            requested_start=_utc(datetime(2026, 5, 23, 9, 0)),
            requested_end=_utc(datetime(2026, 5, 23, 18, 0)),
            reason_class="vacation",
        )
        resp = client.get(
            reverse("master_api:availability_pending"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        assert body["items"][0]["status"] == "pending"
        assert body["items"][0]["reason_class"] == "vacation"

    def test_returns_recently_decided(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            status=ScheduleChangeRequest.Status.APPROVED,
            requested_start=now + timedelta(days=2),
            requested_end=now + timedelta(days=3),
            reason_class="vacation",
            resolved_at=now - timedelta(days=3),
        )
        resp = client.get(
            reverse("master_api:availability_pending"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 1

    def test_old_decided_excluded(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            status=ScheduleChangeRequest.Status.APPROVED,
            requested_start=now - timedelta(days=40),
            requested_end=now - timedelta(days=39),
            reason_class="vacation",
            resolved_at=now - timedelta(days=20),  # > 14d cutoff
        )
        resp = client.get(
            reverse("master_api:availability_pending"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_cross_master_isolation(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        other = make_master(
            tenant,
            name="Карина Б.",
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            invite_token=None,
            expires_in_days=None,
            external_id=888,
        )
        now = datetime.now(tz=timezone.utc)
        ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=other,
            status=ScheduleChangeRequest.Status.PENDING,
            requested_start=now + timedelta(days=2),
            requested_end=now + timedelta(days=3),
            reason_class="vacation",
        )
        resp = client.get(
            reverse("master_api:availability_pending"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    def test_order_created_at_desc(
        self,
        client: Client,
        bot_user: BotUser,
        tenant: Tenant,
        accepted_master: CatalogMaster,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        first = ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            status=ScheduleChangeRequest.Status.PENDING,
            requested_start=now + timedelta(days=5),
            requested_end=now + timedelta(days=6),
            reason_class="vacation",
        )
        second = ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            status=ScheduleChangeRequest.Status.PENDING,
            requested_start=now + timedelta(days=8),
            requested_end=now + timedelta(days=9),
            reason_class="personal",
        )
        # Adjust created_at manually to force ordering (Django auto_now_add).
        ScheduleChangeRequest.all_tenants.filter(id=first.id).update(
            created_at=now - timedelta(hours=5)
        )
        ScheduleChangeRequest.all_tenants.filter(id=second.id).update(
            created_at=now - timedelta(hours=1)
        )
        resp = client.get(
            reverse("master_api:availability_pending"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        items = resp.json()["items"]
        assert items[0]["request_id"] == str(second.id)
        assert items[1]["request_id"] == str(first.id)
