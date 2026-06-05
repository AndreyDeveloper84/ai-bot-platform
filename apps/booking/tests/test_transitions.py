"""Tests for the customer cancel + reschedule state-machine service.

Covers spec §2 transitions in
``docs/design/policies/customer-cancellation-reschedule-spec.md``.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingReminder, BookingRequest
from apps.booking.services.transitions import (
    UNDO_WINDOW_SECONDS,
    InvalidBookingTransition,
    abandon_reschedule,
    commit_cancel,
    commit_reschedule,
    request_cancel,
    request_reschedule,
    undo_cancel,
)
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant


MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="trans", name="Trans", timezone="Europe/Moscow")


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(slug="other", name="Other", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="111",
        chat_id="111",
    )


@pytest.fixture
def other_bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="222",
        chat_id="222",
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


@pytest.fixture
def alt_master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=2,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Мария",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="manicure",
        name="Маникюр",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def master_service(tenant: Tenant, master, service) -> None:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def alt_master_service(tenant: Tenant, alt_master, service) -> None:
    MasterService.all_tenants.create(tenant=tenant, master=alt_master, service=service)


@pytest.fixture
def working_hours(tenant: Tenant, master, alt_master) -> None:
    for m in (master, alt_master):
        for wd in (
            Weekday.MONDAY,
            Weekday.TUESDAY,
            Weekday.WEDNESDAY,
            Weekday.THURSDAY,
            Weekday.FRIDAY,
            Weekday.SATURDAY,
            Weekday.SUNDAY,
        ):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=m,
                day_of_week=wd,
                is_working=True,
                start_time=time(10, 0),
                end_time=time(19, 0),
            )


def _far_future(days: int = 14) -> datetime:
    target = dj_timezone.now().astimezone(MSK) + timedelta(days=days)
    return target.replace(hour=12, minute=0, second=0, microsecond=0)


def _make_booking(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    master: CatalogMaster,
    service: CatalogService,
    visit_at: datetime | None = None,
) -> BookingRequest:
    return BookingRequest.objects.create(
        tenant=tenant,
        bot_user=bot_user,
        service=service,
        master=master,
        service_name=service.name,
        master_name=master.name,
        client_name="Test",
        client_phone="+7-000",
        visit_at=visit_at or _far_future(),
        duration_min=60,
        status=BookingRequest.Status.CONFIRMED,
        source="bot",
        booking_source="ai_direct",
        billable=True,
        billing_reason="ai_direct + confirmed",
        attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
    )


class TestRequestCancel:
    def test_happy_path(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        before = dj_timezone.now()
        row = request_cancel(b, actor=bot_user, reason_class="timing")
        assert row.status == BookingRequest.Status.CANCEL_REQUESTED
        assert row.cancel_requested_at is not None
        assert row.cancel_requested_at >= before
        assert row.attribution_metadata["cancellation_reason_class"] == "timing"

    def test_invalid_from_state_already_cancelled(
        self, tenant, bot_user, master, service, master_service
    ):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_cancel(b, actor=bot_user)
        b.refresh_from_db()
        commit_cancel(b, actor=bot_user)
        b.refresh_from_db()
        with pytest.raises(InvalidBookingTransition) as exc:
            request_cancel(b, actor=bot_user)
        assert exc.value.slug == "invalid_state"

    def test_cross_user_forbidden(
        self, tenant, bot_user, other_bot_user, master, service, master_service
    ):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        with pytest.raises(InvalidBookingTransition) as exc:
            request_cancel(b, actor=other_bot_user)
        assert exc.value.slug == "forbidden"


class TestUndoCancel:
    def test_within_window(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_cancel(b, actor=bot_user)
        b.refresh_from_db()
        row = undo_cancel(b, actor=bot_user)
        assert row.status == BookingRequest.Status.CONFIRMED
        assert row.cancel_requested_at is None
        assert "cancellation_reason_class" not in row.attribution_metadata

    def test_past_window_fails(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_cancel(b, actor=bot_user)
        # Force the timestamp past the window.
        past = dj_timezone.now() - timedelta(seconds=UNDO_WINDOW_SECONDS + 5)
        BookingRequest.all_tenants.filter(pk=b.pk).update(cancel_requested_at=past)
        b.refresh_from_db()
        with pytest.raises(InvalidBookingTransition) as exc:
            undo_cancel(b, actor=bot_user)
        assert exc.value.slug == "undo_window_elapsed"

    def test_undo_not_in_cancel_requested(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        with pytest.raises(InvalidBookingTransition):
            undo_cancel(b, actor=bot_user)


class TestCommitCancel:
    def test_happy_path(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_cancel(b, actor=bot_user, reason_class="plans_changed")
        b.refresh_from_db()
        row = commit_cancel(b, actor=bot_user)
        assert row.status == BookingRequest.Status.CANCELLED

    def test_idempotent_replay(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_cancel(b, actor=bot_user)
        b.refresh_from_db()
        commit_cancel(b, actor=bot_user)
        b.refresh_from_db()
        # Second call is a no-op.
        row = commit_cancel(b, actor=bot_user)
        assert row.status == BookingRequest.Status.CANCELLED

    def test_invalid_from_confirmed(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        with pytest.raises(InvalidBookingTransition):
            commit_cancel(b, actor=bot_user)

    def test_cancels_pending_reminders(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        rem = BookingReminder.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            booking_request=b,
            yclients_record_id="abc",
            chat_id="111",
            visit_at=b.visit_at,
            kind=BookingReminder.Kind.DAY_BEFORE,
            scheduled_at=b.visit_at - timedelta(days=1),
            status=BookingReminder.Status.PENDING,
        )
        request_cancel(b, actor=bot_user)
        b.refresh_from_db()
        commit_cancel(b, actor=bot_user)
        rem.refresh_from_db()
        assert rem.status == BookingReminder.Status.CANCELLED


class TestRequestReschedule:
    def test_happy_path(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        alt_master,
        alt_master_service,
    ):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        new_visit = _far_future(days=21).replace(hour=14)
        row = request_reschedule(
            b,
            actor=bot_user,
            new_master_id=str(alt_master.id),
            new_service_id=str(service.id),
            new_visit_at=new_visit,
        )
        assert row.status == BookingRequest.Status.RESCHEDULE_REQUESTED
        assert row.reschedule_candidate["new_master_id"] == str(alt_master.id)

    def test_invalid_from_state(self, tenant, bot_user, master, service, master_service):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_cancel(b, actor=bot_user)
        b.refresh_from_db()
        with pytest.raises(InvalidBookingTransition):
            request_reschedule(
                b,
                actor=bot_user,
                new_master_id=str(master.id),
                new_service_id=str(service.id),
                new_visit_at=_far_future(days=21),
            )


class TestAbandonReschedule:
    def test_happy_path(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        alt_master,
        alt_master_service,
    ):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_reschedule(
            b,
            actor=bot_user,
            new_master_id=str(alt_master.id),
            new_service_id=str(service.id),
            new_visit_at=_far_future(days=21),
        )
        b.refresh_from_db()
        row = abandon_reschedule(b, actor=bot_user)
        assert row.status == BookingRequest.Status.CONFIRMED
        assert row.reschedule_candidate == {}


class TestCommitReschedule:
    def test_happy_path(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        alt_master,
        alt_master_service,
        working_hours,
    ):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        new_visit = _far_future(days=21).replace(hour=14)
        request_reschedule(
            b,
            actor=bot_user,
            new_master_id=str(alt_master.id),
            new_service_id=str(service.id),
            new_visit_at=new_visit,
        )
        b.refresh_from_db()
        old, new = commit_reschedule(b, actor=bot_user)
        assert old.status == BookingRequest.Status.RESCHEDULED
        assert new.status == BookingRequest.Status.CONFIRMED
        assert new.master_id == alt_master.id
        assert new.rescheduled_from_id == old.id
        assert new.attribution_metadata["rescheduled_from"] == str(old.id)
        assert new.attribution_metadata["reschedule_count"] == 1
        assert new.billable is False  # Q12-α retention principle

    def test_slot_collision_rolls_back(
        self,
        tenant,
        bot_user,
        other_bot_user,
        master,
        service,
        master_service,
        alt_master,
        alt_master_service,
        working_hours,
    ):
        # Original booking with bot_user.
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        new_visit = _far_future(days=21).replace(hour=14)
        # Pre-occupy the target slot for alt_master via other_bot_user.
        _make_booking(
            tenant=tenant,
            bot_user=other_bot_user,
            master=alt_master,
            service=service,
            visit_at=new_visit,
        )
        request_reschedule(
            b,
            actor=bot_user,
            new_master_id=str(alt_master.id),
            new_service_id=str(service.id),
            new_visit_at=new_visit,
        )
        b.refresh_from_db()
        with pytest.raises(InvalidBookingTransition) as exc:
            commit_reschedule(b, actor=bot_user)
        assert exc.value.slug == "slot_unavailable"

    def test_master_archived_between_request_and_commit(
        self,
        tenant,
        bot_user,
        master,
        service,
        master_service,
        alt_master,
        alt_master_service,
        working_hours,
    ):
        b = _make_booking(tenant=tenant, bot_user=bot_user, master=master, service=service)
        request_reschedule(
            b,
            actor=bot_user,
            new_master_id=str(alt_master.id),
            new_service_id=str(service.id),
            new_visit_at=_far_future(days=21),
        )
        b.refresh_from_db()
        # Archive the master.
        alt_master.is_active = False
        alt_master.save()
        with pytest.raises(InvalidBookingTransition) as exc:
            commit_reschedule(b, actor=bot_user)
        assert exc.value.slug == "master_archived"
