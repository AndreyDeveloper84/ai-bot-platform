"""Tests for the slot resolver (apps.scheduling.services.resolver) — r2.

Updated to match the rebuilt schema: 0-indexed weekday, lunch splits,
5-value exception types, SlotConfig (buffer + lead_time +
granularity), and TimeBlock intervals.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from apps.catalog.models import CatalogMaster
from apps.scheduling.models import (
    ScheduleException,
    SlotConfig,
    TimeBlock,
    Weekday,
    WorkingHours,
)
from apps.scheduling.services.resolver import (
    ResolverConfig,
    collect_time_block_intervals,
    compute_free_slots,
    get_slot_config,
    resolve_working_blocks,
)
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="res-test", name="Resolver Test", timezone="Europe/Moscow")


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Анна",
    )


class TestGetSlotConfig:
    def test_defaults_when_missing(self, tenant: Tenant) -> None:
        cfg = get_slot_config(tenant)
        assert cfg.slot_granularity_min == 15
        assert cfg.buffer_min == 5
        assert cfg.lead_time_min == 60
        assert cfg.max_advance_days == 60

    def test_uses_row_when_present(self, tenant: Tenant) -> None:
        SlotConfig.all_tenants.create(
            tenant=tenant,
            slot_granularity_min=30,
            buffer_min=0,
            lead_time_min=0,
            max_advance_days=14,
        )
        cfg = get_slot_config(tenant)
        assert cfg.slot_granularity_min == 30
        assert cfg.buffer_min == 0


class TestResolveWorkingBlocks:
    def test_weekday_template_0_indexed(self, tenant: Tenant, master: CatalogMaster) -> None:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.MONDAY,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )
        # 2026-05-18 is a Monday. .weekday() == 0.
        blocks = resolve_working_blocks(tenant=tenant, master=master, on_date=date(2026, 5, 18))
        assert blocks == [(time(10, 0), time(19, 0))]

    def test_lunch_split(self, tenant: Tenant, master: CatalogMaster) -> None:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.MONDAY,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
            lunch_start=time(13, 0),
            lunch_end=time(14, 0),
        )
        blocks = resolve_working_blocks(tenant=tenant, master=master, on_date=date(2026, 5, 18))
        assert blocks == [(time(10, 0), time(13, 0)), (time(14, 0), time(19, 0))]

    def test_recurring_day_off(self, tenant: Tenant, master: CatalogMaster) -> None:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.SUNDAY,
            is_working=False,
        )
        # 2026-05-24 is a Sunday.
        blocks = resolve_working_blocks(tenant=tenant, master=master, on_date=date(2026, 5, 24))
        assert blocks == []

    def test_vacation_overrides(self, tenant: Tenant, master: CatalogMaster) -> None:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.MONDAY,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=date(2026, 5, 18),
            type=ScheduleException.Type.VACATION,
        )
        blocks = resolve_working_blocks(tenant=tenant, master=master, on_date=date(2026, 5, 18))
        assert blocks == []

    def test_custom_hours_overrides(self, tenant: Tenant, master: CatalogMaster) -> None:
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=master,
            day_of_week=Weekday.MONDAY,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )
        ScheduleException.all_tenants.create(
            tenant=tenant,
            master=master,
            date=date(2026, 5, 18),
            type=ScheduleException.Type.CUSTOM_HOURS,
            start_time=time(12, 0),
            end_time=time(20, 0),
        )
        blocks = resolve_working_blocks(tenant=tenant, master=master, on_date=date(2026, 5, 18))
        assert blocks == [(time(12, 0), time(20, 0))]


class TestComputeFreeSlots:
    def test_simple_block_default_config(self) -> None:
        slots = compute_free_slots(
            working_blocks=[(time(10, 0), time(12, 0))],
            occupied=[],
            service_duration_min=30,
            on_date=date(2026, 5, 18),
            tz=MSK,
            config=ResolverConfig(slot_granularity_min=30),
        )
        # 10:00, 10:30, 11:00, 11:30.
        starts = [s.start.time() for s in slots]
        assert starts == [time(10, 0), time(10, 30), time(11, 0), time(11, 30)]

    def test_buffer_extends_occupied(self) -> None:
        # Occupy 11:00-12:00, buffer 15 → blocks 10:45-12:15.
        occ_start = datetime(2026, 5, 18, 11, 0, tzinfo=MSK)
        occ_end = datetime(2026, 5, 18, 12, 0, tzinfo=MSK)
        slots = compute_free_slots(
            working_blocks=[(time(10, 0), time(13, 0))],
            occupied=[(occ_start, occ_end)],
            service_duration_min=30,
            on_date=date(2026, 5, 18),
            tz=MSK,
            config=ResolverConfig(slot_granularity_min=15, buffer_min=15),
        )
        starts = [s.start.time() for s in slots]
        # 10:00 ends 10:30, OK. 10:15 ends 10:45 — touches expanded occ at 10:45,
        # closed-open so 10:45 < 10:45 false → not overlapping. So 10:15 OK.
        # 10:30 ends 11:00 — overlaps expanded (10:45 to 12:15). Excluded.
        # 10:45, 11:00, 11:15, 11:30, 11:45 — all inside expanded. Excluded.
        # 12:00 ends 12:30 — start 12:00 < 12:15 → overlaps. Excluded.
        # 12:15 ends 12:45 — start 12:15 < 12:15 false → not overlapping. OK.
        # 12:30 OK.
        assert starts == [time(10, 0), time(10, 15), time(12, 15), time(12, 30)]

    def test_lead_time_cutoff(self) -> None:
        # Today is 2026-05-18 in MSK. Now is 11:00, lead 60 min → cutoff 12:00.
        now = datetime(2026, 5, 18, 11, 0, tzinfo=MSK)
        slots = compute_free_slots(
            working_blocks=[(time(10, 0), time(14, 0))],
            occupied=[],
            service_duration_min=60,
            on_date=date(2026, 5, 18),
            tz=MSK,
            config=ResolverConfig(slot_granularity_min=30, buffer_min=0, lead_time_min=60),
            now=now,
        )
        starts = [s.start.time() for s in slots]
        # 10:00, 10:30, 11:00, 11:30 all before cutoff 12:00. 12:00 OK, 12:30 OK,
        # 13:00 ends 14:00, OK. 13:30 ends 14:30 > end. Excluded.
        assert starts == [time(12, 0), time(12, 30), time(13, 0)]

    def test_service_longer_than_block(self) -> None:
        slots = compute_free_slots(
            working_blocks=[(time(10, 0), time(11, 0))],
            occupied=[],
            service_duration_min=90,
            on_date=date(2026, 5, 18),
            tz=MSK,
        )
        assert slots == []

    def test_zero_duration(self) -> None:
        slots = compute_free_slots(
            working_blocks=[(time(10, 0), time(13, 0))],
            occupied=[],
            service_duration_min=0,
            on_date=date(2026, 5, 18),
            tz=MSK,
        )
        assert slots == []

    def test_split_shift_two_blocks(self) -> None:
        slots = compute_free_slots(
            working_blocks=[
                (time(9, 0), time(13, 0)),
                (time(14, 0), time(19, 0)),
            ],
            occupied=[],
            service_duration_min=60,
            on_date=date(2026, 5, 18),
            tz=MSK,
            config=ResolverConfig(slot_granularity_min=60, buffer_min=0),
        )
        starts = [s.start.time() for s in slots]
        assert starts == [
            time(9, 0),
            time(10, 0),
            time(11, 0),
            time(12, 0),
            time(14, 0),
            time(15, 0),
            time(16, 0),
            time(17, 0),
            time(18, 0),
        ]


class TestCollectTimeBlockIntervals:
    def test_returns_intersecting(self, tenant: Tenant, master: CatalogMaster) -> None:
        b = TimeBlock.all_tenants.create(
            tenant=tenant,
            master=master,
            start_at=datetime(2026, 5, 18, 10, 0, tzinfo=MSK),
            end_at=datetime(2026, 5, 18, 11, 0, tzinfo=MSK),
            reason="обед",
        )
        intervals = collect_time_block_intervals(
            tenant=tenant,
            master=master,
            date_from=date(2026, 5, 18),
            date_to=date(2026, 5, 18),
            tz=MSK,
        )
        assert len(intervals) == 1
        assert intervals[0][0] == b.start_at
