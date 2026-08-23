"""What the master's assistant can look up (DRF-1061 step 1).

The property that matters most is the one that is not about correctness:
**no tool takes a master id**. It comes from whoever is speaking. Were it an
argument, «покажи день Ольги» would be a working request — and a model's
arguments are steered by the text a person types.

The rest is arithmetic on the visit mirror, which is worth pinning because
the assistant states these numbers as fact to someone planning their day.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone as dt_timezone

import pytest
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.catalog.models import CatalogMaster
from apps.master_api.services.assistant_tools import (
    MAX_SPAN_DAYS,
    TOOL_SPECS,
    ToolError,
    free_slots,
    my_day,
    my_week,
    run_tool,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

MSK = dt_timezone(timedelta(hours=3))


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="tools-salon", name="Формула тела", timezone="Europe/Moscow")


@pytest.fixture
def master(tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Ольга",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )


@pytest.fixture
def other_master(tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Марина",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )


def _visit(master, *, at: datetime, minutes: int = 60):
    """One row in the mirror the master surface actually reads.

    `RemoteBookingProxy`, not `BookingRequest`: on the pilot the local
    table has four rows and a master on none of them (DRF-1085).
    """

    return RemoteBookingProxy.all_tenants.create(
        tenant=master.tenant,
        appointment_id=uuid.uuid4(),
        specialist_id=master.id,
        start_at=at,
        end_at=at + timedelta(minutes=minutes),
        status="confirmed",
    )


class TestMyDay:
    def test_lists_the_visits_of_that_day(self, master):
        _visit(master, at=datetime(2026, 8, 25, 10, 0, tzinfo=MSK))
        _visit(master, at=datetime(2026, 8, 25, 13, 0, tzinfo=MSK))
        _visit(master, at=datetime(2026, 8, 26, 11, 0, tzinfo=MSK))

        result = my_day(master, date="2026-08-25")

        assert result["count"] == 2
        assert [v["time"] for v in result["visits"]] == ["10:00", "13:00"]

    def test_an_empty_day_is_a_zero_not_an_error(self, master):
        result = my_day(master, date="2026-08-25")

        assert result["count"] == 0
        assert result["visits"] == []

    def test_another_masters_day_is_invisible(self, master, other_master):
        _visit(other_master, at=datetime(2026, 8, 25, 10, 0, tzinfo=MSK))

        assert my_day(master, date="2026-08-25")["count"] == 0

    def test_no_phone_number_in_the_shape(self, master):
        _visit(master, at=datetime(2026, 8, 25, 10, 0, tzinfo=MSK))

        visit = my_day(master, date="2026-08-25")["visits"][0]

        assert "phone" not in visit
        assert set(visit) == {"time", "duration_min", "service", "client", "status"}

    def test_a_junk_date_is_a_readable_refusal(self, master):
        with pytest.raises(ToolError, match="не понимаю дату"):
            my_day(master, date="в четверг")


class TestMyWeek:
    def test_counts_per_day_and_total(self, master):
        _visit(master, at=datetime(2026, 8, 24, 10, 0, tzinfo=MSK))
        _visit(master, at=datetime(2026, 8, 24, 15, 0, tzinfo=MSK))
        _visit(master, at=datetime(2026, 8, 26, 11, 0, tzinfo=MSK))

        result = my_week(master, date_from="2026-08-24", date_to="2026-08-26")

        assert result["total"] == 3
        assert result["per_day"] == {"2026-08-24": 2, "2026-08-25": 0, "2026-08-26": 1}

    def test_every_day_in_the_span_is_present(self, master):
        # A missing key would read as "no data" rather than "no visits".
        result = my_week(master, date_from="2026-08-24", date_to="2026-08-30")

        assert len(result["per_day"]) == 7

    def test_a_reversed_span_is_understood_not_refused(self, master):
        result = my_week(master, date_from="2026-08-26", date_to="2026-08-24")

        assert result["date_from"] == "2026-08-24"
        assert result["date_to"] == "2026-08-26"

    def test_an_absurd_span_is_refused(self, master):
        with pytest.raises(ToolError, match=str(MAX_SPAN_DAYS)):
            my_week(master, date_from="2026-01-01", date_to="2026-12-31")


class TestFreeSlots:
    def test_a_clear_day_is_one_long_slot(self, master):
        result = free_slots(master, date="2026-08-25", duration_min=60)

        assert result["slots"] == [{"from": "09:00", "to": "21:00"}]

    def test_a_booking_splits_the_day(self, master):
        _visit(master, at=datetime(2026, 8, 25, 12, 0, tzinfo=MSK), minutes=60)

        result = free_slots(master, date="2026-08-25", duration_min=60)

        assert result["slots"] == [
            {"from": "09:00", "to": "12:00"},
            {"from": "13:00", "to": "21:00"},
        ]

    def test_gaps_shorter_than_asked_are_not_offered(self, master):
        _visit(master, at=datetime(2026, 8, 25, 9, 30, tzinfo=MSK), minutes=60)

        result = free_slots(master, date="2026-08-25", duration_min=120)

        # 09:00–09:30 is real but useless for a two-hour service; offering
        # it would send the master to check a slot they cannot use.
        assert {"from": "09:00", "to": "09:30"} not in result["slots"]

    def test_back_to_back_bookings_do_not_invent_a_gap(self, master):
        _visit(master, at=datetime(2026, 8, 25, 12, 0, tzinfo=MSK), minutes=60)
        _visit(master, at=datetime(2026, 8, 25, 13, 0, tzinfo=MSK), minutes=60)

        result = free_slots(master, date="2026-08-25", duration_min=30)

        assert result["slots"] == [
            {"from": "09:00", "to": "12:00"},
            {"from": "14:00", "to": "21:00"},
        ]

    def test_a_full_day_offers_nothing(self, master):
        _visit(master, at=datetime(2026, 8, 25, 9, 0, tzinfo=MSK), minutes=720)

        assert free_slots(master, date="2026-08-25", duration_min=60)["slots"] == []

    def test_another_masters_bookings_do_not_block_this_one(self, master, other_master):
        _visit(other_master, at=datetime(2026, 8, 25, 12, 0, tzinfo=MSK), minutes=60)

        result = free_slots(master, date="2026-08-25", duration_min=60)

        assert result["slots"] == [{"from": "09:00", "to": "21:00"}]


class TestTheMasterIsNotAnArgument:
    def test_no_spec_exposes_a_master_field(self):
        for spec in TOOL_SPECS:
            props = spec["parameters"]["properties"]
            assert not {"master", "master_id", "specialist_id"} & set(props), spec["name"]

    def test_a_master_supplied_by_the_model_is_dropped(self, master, other_master):
        """The attack shape: the model naming its own subject."""

        _visit(other_master, at=datetime(2026, 8, 25, 10, 0, tzinfo=MSK))

        outcome = run_tool(
            "my_day",
            {"date": "2026-08-25", "master": other_master},
            master=master,
        )

        assert outcome.data["count"] == 0

    def test_an_unknown_tool_is_refused(self, master):
        with pytest.raises(ToolError, match="неизвестный инструмент"):
            run_tool("drop_everything", {}, master=master)
