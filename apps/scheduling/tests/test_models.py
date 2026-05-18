"""Tests for apps.scheduling models — Phase 0a r2 (per schedule-management handoff)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest
from django.db.utils import IntegrityError

from apps.catalog.models import CatalogMaster
from apps.scheduling.models import (
    ScheduleChangeRequest,
    ScheduleException,
    SlotConfig,
    TimeBlock,
    Weekday,
    WorkingHours,
)
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="sched-test", name="Scheduling Test")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
    )


class TestWorkingHours:
    def test_create_working_day_with_lunch(self, tenant: Tenant, master: CatalogMaster) -> None:
        wh = WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.MONDAY,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
            lunch_start=time(13, 0),
            lunch_end=time(14, 0),
        )
        assert wh.day_of_week == 0
        assert wh.lunch_start == time(13, 0)

    def test_day_off_no_times(self, tenant: Tenant, master: CatalogMaster) -> None:
        wh = WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.SUNDAY,
            is_working=False,
        )
        assert wh.start_time is None
        assert wh.end_time is None

    def test_working_day_without_times_rejected(
        self, tenant: Tenant, master: CatalogMaster
    ) -> None:
        with pytest.raises(IntegrityError):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=Weekday.MONDAY,
                is_working=True,
            )

    def test_day_off_with_times_rejected(self, tenant: Tenant, master: CatalogMaster) -> None:
        with pytest.raises(IntegrityError):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=Weekday.SUNDAY,
                is_working=False,
                start_time=time(10, 0),
                end_time=time(19, 0),
            )

    def test_unique_per_master_day(self, tenant: Tenant, master: CatalogMaster) -> None:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.MONDAY,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )
        with pytest.raises(IntegrityError):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=Weekday.MONDAY,
                is_working=True,
                start_time=time(11, 0),
                end_time=time(20, 0),
            )

    def test_end_before_start_rejected(self, tenant: Tenant, master: CatalogMaster) -> None:
        with pytest.raises(IntegrityError):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=Weekday.MONDAY,
                is_working=True,
                start_time=time(19, 0),
                end_time=time(10, 0),
            )

    def test_lunch_only_one_side_rejected(self, tenant: Tenant, master: CatalogMaster) -> None:
        with pytest.raises(IntegrityError):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=Weekday.MONDAY,
                is_working=True,
                start_time=time(10, 0),
                end_time=time(19, 0),
                lunch_start=time(13, 0),
            )

    def test_lunch_end_before_start_rejected(self, tenant: Tenant, master: CatalogMaster) -> None:
        with pytest.raises(IntegrityError):
            WorkingHours.all_tenants.create(
                tenant=tenant,
                master=master,
                day_of_week=Weekday.MONDAY,
                is_working=True,
                start_time=time(10, 0),
                end_time=time(19, 0),
                lunch_start=time(14, 0),
                lunch_end=time(13, 0),
            )


class TestScheduleException:
    def test_vacation_full_day(self, tenant: Tenant, master: CatalogMaster) -> None:
        exc = ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=date(2026, 6, 1),
            type=ScheduleException.Type.VACATION,
            reason="Сочи",
        )
        assert exc.start_time is None
        assert exc.type == "vacation"

    def test_sick_leave_full_day(self, tenant: Tenant, master: CatalogMaster) -> None:
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=date(2026, 6, 2),
            type=ScheduleException.Type.SICK_LEAVE,
        )

    def test_event_full_day(self, tenant: Tenant, master: CatalogMaster) -> None:
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=date(2026, 6, 3),
            type=ScheduleException.Type.EVENT,
            reason="Корпоратив",
        )

    def test_custom_hours_with_times(self, tenant: Tenant, master: CatalogMaster) -> None:
        exc = ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=date(2026, 6, 14),
            type=ScheduleException.Type.CUSTOM_HOURS,
            start_time=time(11, 0),
            end_time=time(20, 0),
        )
        assert exc.type == "custom_hours"

    def test_full_day_with_times_rejected(self, tenant: Tenant, master: CatalogMaster) -> None:
        with pytest.raises(IntegrityError):
            ScheduleException.all_tenants.create(
                tenant=tenant,
                master=master,
                date=date(2026, 6, 1),
                type=ScheduleException.Type.VACATION,
                start_time=time(10, 0),
                end_time=time(19, 0),
            )

    def test_custom_hours_without_times_rejected(
        self, tenant: Tenant, master: CatalogMaster
    ) -> None:
        with pytest.raises(IntegrityError):
            ScheduleException.all_tenants.create(
                tenant=tenant,
                master=master,
                date=date(2026, 6, 14),
                type=ScheduleException.Type.CUSTOM_HOURS,
            )

    def test_custom_hours_end_before_start_rejected(
        self, tenant: Tenant, master: CatalogMaster
    ) -> None:
        with pytest.raises(IntegrityError):
            ScheduleException.all_tenants.create(
                tenant=tenant,
                master=master,
                date=date(2026, 6, 14),
                type=ScheduleException.Type.CUSTOM_HOURS,
                start_time=time(20, 0),
                end_time=time(10, 0),
            )

    def test_unique_per_master_date(self, tenant: Tenant, master: CatalogMaster) -> None:
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=date(2026, 6, 1),
            type=ScheduleException.Type.DAY_OFF,
        )
        with pytest.raises(IntegrityError):
            ScheduleException.all_tenants.create(
                tenant=tenant,
                master=master,
                date=date(2026, 6, 1),
                type=ScheduleException.Type.VACATION,
            )


class TestTimeBlock:
    def test_create(self, tenant: Tenant, master: CatalogMaster) -> None:
        start = datetime(2026, 6, 14, 13, 0, tzinfo=timezone.utc)
        block = TimeBlock.all_tenants.create(
            tenant=tenant,
            master=master,
            start_at=start,
            end_at=start + timedelta(hours=1),
            reason="обед",
        )
        assert block.reason == "обед"

    def test_end_before_start_rejected(self, tenant: Tenant, master: CatalogMaster) -> None:
        start = datetime(2026, 6, 14, 14, 0, tzinfo=timezone.utc)
        with pytest.raises(IntegrityError):
            TimeBlock.all_tenants.create(
                tenant=tenant,
                master=master,
                start_at=start,
                end_at=start - timedelta(hours=1),
                reason="bad",
            )


class TestScheduleChangeRequest:
    def test_create_pending(self, tenant: Tenant, master: CatalogMaster) -> None:
        req = ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=master,
            requested_change={
                "type": "working_hours_change",
                "day_of_week": 2,
                "new_start": "11:00",
                "new_end": "19:00",
            },
            reason="Утренние йога-занятия",
        )
        assert req.status == "pending"
        assert req.resolved_at is None

    def test_arbitrary_json_payload(self, tenant: Tenant, master: CatalogMaster) -> None:
        # No DB-level schema validation — accepts any well-formed JSON.
        ScheduleChangeRequest.all_tenants.create(
            tenant=tenant,
            master=master,
            requested_change={"anything": ["goes", 1, True, None]},
        )


class TestSlotConfig:
    def test_defaults(self, tenant: Tenant) -> None:
        cfg = SlotConfig.all_tenants.create(tenant=tenant)
        assert cfg.slot_granularity_min == 15
        assert cfg.buffer_min == 5
        assert cfg.lead_time_min == 60
        assert cfg.max_advance_days == 60

    def test_one_per_tenant(self, tenant: Tenant) -> None:
        SlotConfig.all_tenants.create(tenant=tenant)
        with pytest.raises(IntegrityError):
            SlotConfig.all_tenants.create(tenant=tenant)
