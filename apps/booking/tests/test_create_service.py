"""Tests for the atomic booking-create service (4a hardening)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.db.utils import IntegrityError

from apps.booking.models import BookingRequest
from apps.booking.services.create import (
    BookingCreateError,
    CreateBookingInput,
    create_customer_booking,
)
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.identity.models import BotUser
from apps.scheduling.models import Weekday, WorkingHours
from apps.tenancy.models import Tenant


MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="create-test", name="Create Test", timezone="Europe/Moscow")


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(slug="other", name="Other", timezone="Europe/Moscow")


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
def master_service(tenant: Tenant, master, service) -> None:
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


@pytest.fixture
def working_hours(tenant: Tenant, master) -> None:
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


def _far_future_monday_noon() -> datetime:
    """Pick a Monday 30+ days in the future at 12:00 MSK (past lead-time)."""

    target = date.today() + timedelta(days=30)
    while target.weekday() != 0:
        target += timedelta(days=1)
    return datetime.combine(target, time(12, 0), tzinfo=MSK)


class TestCreateCustomerBooking:
    def test_happy_path(
        self, tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        booking = create_customer_booking(
            inp=CreateBookingInput(
                tenant=tenant,
                bot_user=bot_user,
                service_id=str(service.id),
                master_id=str(master.id),
                visit_at=_far_future_monday_noon(),
            ),
            correlation_id="corr-1",
        )
        assert booking.booking_source == "ai_direct"
        assert booking.billable is True
        assert booking.service_id == service.id
        assert booking.master_id == master.id

    def test_visit_in_past(
        self, tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        with pytest.raises(BookingCreateError) as exc_info:
            create_customer_booking(
                inp=CreateBookingInput(
                    tenant=tenant,
                    bot_user=bot_user,
                    service_id=str(service.id),
                    master_id=str(master.id),
                    visit_at=datetime(2020, 1, 1, 12, 0, tzinfo=MSK),
                )
            )
        assert exc_info.value.slug == "visit_in_past"

    def test_master_archived(
        self, tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        master.is_active = False
        master.save()
        with pytest.raises(BookingCreateError) as exc_info:
            create_customer_booking(
                inp=CreateBookingInput(
                    tenant=tenant,
                    bot_user=bot_user,
                    service_id=str(service.id),
                    master_id=str(master.id),
                    visit_at=_far_future_monday_noon(),
                )
            )
        assert exc_info.value.slug == "master_archived"

    def test_master_invite_pending(
        self, tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        master.invite_status = CatalogMaster.InviteStatus.PENDING
        master.save()
        with pytest.raises(BookingCreateError) as exc_info:
            create_customer_booking(
                inp=CreateBookingInput(
                    tenant=tenant,
                    bot_user=bot_user,
                    service_id=str(service.id),
                    master_id=str(master.id),
                    visit_at=_far_future_monday_noon(),
                )
            )
        assert exc_info.value.slug == "master_not_bookable"

    def test_tenant_mismatch(
        self, tenant, other_tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        with pytest.raises(BookingCreateError) as exc_info:
            create_customer_booking(
                inp=CreateBookingInput(
                    tenant=other_tenant,
                    bot_user=bot_user,
                    service_id=str(service.id),
                    master_id=str(master.id),
                    visit_at=_far_future_monday_noon(),
                )
            )
        # Service is scoped to caller tenant — other_tenant has no service row.
        assert exc_info.value.slug == "service_not_found"

    def test_service_not_offered(self, tenant, bot_user, master, service, working_hours) -> None:
        # No MasterService mapping.
        with pytest.raises(BookingCreateError) as exc_info:
            create_customer_booking(
                inp=CreateBookingInput(
                    tenant=tenant,
                    bot_user=bot_user,
                    service_id=str(service.id),
                    master_id=str(master.id),
                    visit_at=_far_future_monday_noon(),
                )
            )
        assert exc_info.value.slug == "service_not_offered"

    def test_slot_off_grid(
        self, tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        # 12:07 is not on the 15-min grid.
        target = date.today() + timedelta(days=30)
        while target.weekday() != 0:
            target += timedelta(days=1)
        bad = datetime.combine(target, time(12, 7), tzinfo=MSK)
        with pytest.raises(BookingCreateError) as exc_info:
            create_customer_booking(
                inp=CreateBookingInput(
                    tenant=tenant,
                    bot_user=bot_user,
                    service_id=str(service.id),
                    master_id=str(master.id),
                    visit_at=bad,
                )
            )
        assert exc_info.value.slug == "slot_unavailable"

    def test_concurrent_double_book_db_backstop(
        self, tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        """Second booking for the same (master, visit_at, CONFIRMED) must
        fail at the partial UniqueConstraint level even if app-side
        race-check is bypassed.
        """

        visit_at = _far_future_monday_noon()
        first = create_customer_booking(
            inp=CreateBookingInput(
                tenant=tenant,
                bot_user=bot_user,
                service_id=str(service.id),
                master_id=str(master.id),
                visit_at=visit_at,
            )
        )
        assert first.status == "confirmed"

        # Second attempt — resolver re-check should catch it (slot now
        # occupied). The DB UniqueConstraint is the backstop if resolver
        # somehow misses.
        with pytest.raises(BookingCreateError) as exc_info:
            create_customer_booking(
                inp=CreateBookingInput(
                    tenant=tenant,
                    bot_user=bot_user,
                    service_id=str(service.id),
                    master_id=str(master.id),
                    visit_at=visit_at,
                )
            )
        assert exc_info.value.slug == "slot_unavailable"

    def test_db_unique_constraint_direct_violation(
        self, tenant, bot_user, master, service, master_service, working_hours
    ) -> None:
        """Directly create two BookingRequest rows with same (master,
        visit_at, CONFIRMED). DB unique partial index must reject the
        second insert.
        """

        visit_at = _far_future_monday_noon()
        BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service=service,
            master=master,
            service_name=service.name,
            master_name=master.name,
            client_name="Test",
            client_phone="+79991234567",
            visit_at=visit_at,
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
            booking_source="ai_direct",
            attribution_metadata={"actor_type": "customer", "created_by": "execute_confirm"},
        )
        with pytest.raises(IntegrityError):
            BookingRequest.all_tenants.create(
                tenant=tenant,
                bot_user=bot_user,
                service=service,
                master=master,
                service_name=service.name,
                master_name=master.name,
                client_name="Test 2",
                client_phone="+79992223344",
                visit_at=visit_at,
                duration_min=60,
                status=BookingRequest.Status.CONFIRMED,
                booking_source="ai_direct",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
            )


class TestVisitAtValidator:
    def test_ai_direct_without_visit_at_rejected(
        self, tenant, bot_user, master, service, master_service
    ) -> None:
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="visit_at"):
            BookingRequest.all_tenants.create(
                tenant=tenant,
                bot_user=bot_user,
                service=service,
                master=master,
                service_name=service.name,
                master_name=master.name,
                client_name="Test",
                client_phone="+79991234567",
                status=BookingRequest.Status.CONFIRMED,
                booking_source="ai_direct",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
                # visit_at intentionally omitted
            )

    def test_external_without_visit_at_allowed(self, tenant, bot_user) -> None:
        """Legacy external rows can keep NULL visit_at — slot resolver
        excludes them anyway."""

        BookingRequest.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Legacy",
            client_name="Legacy",
            client_phone="+71112223344",
            # booking_source defaults to 'external' → validator skipped.
        )
