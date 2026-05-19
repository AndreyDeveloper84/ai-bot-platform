"""Tests for the reschedule service (Phase 2)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from apps.booking.models import BookingRequest
from apps.booking.services.create import (
    BookingCreateError,
    CreateBookingInput,
    create_customer_booking,
)
from apps.booking.services.reschedule import reschedule_customer_booking
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant


MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="resched", name="Resched", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        display_name="Мария",
    )


@pytest.fixture
def other_bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="99999",
        chat_id="99999",
        display_name="Другой",
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
def master_service(tenant, master, service) -> None:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def working_hours(tenant, master) -> None:
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
            master=master,
            day_of_week=wd,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )


def _monday_at(hour: int) -> datetime:
    d = date.today() + timedelta(days=30)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return datetime.combine(d, time(hour, 0), tzinfo=MSK)


@pytest.fixture
def existing_booking(tenant, bot_user, master, service, master_service, working_hours):
    return create_customer_booking(
        inp=CreateBookingInput(
            tenant=tenant,
            bot_user=bot_user,
            service_id=str(service.id),
            master_id=str(master.id),
            visit_at=_monday_at(12),
        ),
    )


class TestRescheduleCustomerBooking:
    def test_happy_path(
        self, tenant, bot_user, master, service, master_service, working_hours, existing_booking
    ):
        new_visit = _monday_at(14)
        new = reschedule_customer_booking(
            tenant=tenant,
            bot_user=bot_user,
            old_booking_id=str(existing_booking.id),
            new_visit_at=new_visit,
        )
        assert new.visit_at == new_visit
        assert new.booking_source == "ai_direct"
        # Q12-α: reschedule NOT billable.
        assert new.billable is False
        assert "execute_reschedule" in new.billing_reason
        assert new.attribution_metadata["created_by"] == "execute_reschedule"
        # Old marked RESCHEDULED.
        existing_booking.refresh_from_db()
        assert existing_booking.status == BookingRequest.Status.RESCHEDULED

    def test_not_owned_rejected(self, tenant, bot_user, other_bot_user, existing_booking):
        with pytest.raises(BookingCreateError) as exc_info:
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=other_bot_user,
                old_booking_id=str(existing_booking.id),
                new_visit_at=_monday_at(15),
            )
        assert exc_info.value.slug == "forbidden"

    def test_already_cancelled_rejected(self, tenant, bot_user, existing_booking):
        existing_booking.status = BookingRequest.Status.CANCELLED
        existing_booking.save()
        with pytest.raises(BookingCreateError) as exc_info:
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=bot_user,
                old_booking_id=str(existing_booking.id),
                new_visit_at=_monday_at(15),
            )
        assert exc_info.value.slug == "not_reschedulable"

    def test_not_found_rejected(self, tenant, bot_user):
        import uuid

        with pytest.raises(BookingCreateError) as exc_info:
            reschedule_customer_booking(
                tenant=tenant,
                bot_user=bot_user,
                old_booking_id=str(uuid.uuid4()),
                new_visit_at=_monday_at(14),
            )
        assert exc_info.value.slug == "not_found"
