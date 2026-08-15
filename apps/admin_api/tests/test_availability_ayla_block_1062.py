"""Approving a day-off writes it into Ayla, not just locally (DRF-1062).

Before this, approval materialised ``ScheduleException`` rows in the bot's
own ``apps.scheduling``. Once the customer picker reads slots from Ayla
that store no longer decides anything a client can see, so the
administrator would approve a day off, be told it succeeded, and the day
would stay on sale. Two stores, one of them decorative — the exact
"промежуточное состояние" the brief calls the worst outcome.

Flag OFF is left alone on purpose: that deployment computes slots locally
and books locally, so its schedule genuinely is the local one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.admin_api.services.availability import (
    AvailabilityDecisionError,
    approve_availability_request,
)
from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.booking_client import (
    BookingUnavailableError,
    ScheduleBlockConflictError,
)
from apps.scheduling.models import ScheduleChangeRequest, ScheduleException
from apps.tenancy.models import Tenant

CLIENT_PATH = "apps.integrations.ayla.booking_client.get_ayla_booking_client"
START = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _ayla_path(settings) -> None:
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="av-1062",
        name="Салон заявок",
        timezone="Europe/Moscow",
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=7,
        external_updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        name="Ольга",
        is_active=True,
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def pending(tenant: Tenant, master: CatalogMaster) -> ScheduleChangeRequest:
    return ScheduleChangeRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        requested_start=START,
        requested_end=END,
        reason_class="sick_leave",
        reason_text="болезнь",
        status=ScheduleChangeRequest.Status.PENDING,
    )


def _fake_client(*, raises=None):
    calls: list[dict] = []

    def create_specialist_time_off(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return {"id": str(uuid.uuid4())}

    return SimpleNamespace(create_specialist_time_off=create_specialist_time_off), calls


def _approve(tenant, request_id):
    return approve_availability_request(
        request_id=request_id,
        tenant_id=tenant.id,
        actor=None,
        actor_role="admin",
    )


class TestApprovalReachesAyla:
    def test_approval_blocks_the_time_in_ayla(self, tenant, master, pending):
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert len(calls) == 1
        assert calls[0]["specialist_id"] == str(master.id)
        assert calls[0]["tenant_id"] == str(tenant.id)

    def test_sends_the_requested_interval_not_whole_days(
        self,
        tenant,
        master,
        pending,
    ):
        """The local model is date-keyed and rounds a part-day request up to
        full days. Ayla holds an interval, so it gets what was actually
        asked for — blocking more would quietly cost the salon bookings."""
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert calls[0]["start_at"] == START.isoformat()
        assert calls[0]["end_at"] == END.isoformat()

    def test_addresses_the_specialist_id_not_the_user_id(
        self,
        tenant,
        master,
        pending,
    ):
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert calls[0]["specialist_id"] != str(master.ayla_user_id)


class TestRefusalsLeaveNothingHalfDone:
    def test_active_bookings_refuse_with_409_and_request_stays_pending(
        self,
        tenant,
        master,
        pending,
    ):
        """The administrator must learn the time is booked, not watch
        "approve" silently do nothing."""
        fake, _ = _fake_client(raises=ScheduleBlockConflictError("has_active"))

        with patch(CLIENT_PATH, return_value=fake):
            with pytest.raises(AvailabilityDecisionError) as err:
                _approve(tenant, pending.id)

        assert err.value.status == 409
        assert err.value.slug == "has_active_appointments"

        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.PENDING
        assert not ScheduleException.all_tenants.filter(master=master).exists()

    def test_outage_refuses_rather_than_approving_into_the_void(
        self,
        tenant,
        master,
        pending,
    ):
        fake, _ = _fake_client(raises=BookingUnavailableError("circuit_open"))

        with patch(CLIENT_PATH, return_value=fake):
            with pytest.raises(AvailabilityDecisionError) as err:
                _approve(tenant, pending.id)

        assert err.value.status == 503

        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.PENDING
        assert not ScheduleException.all_tenants.filter(master=master).exists()


class TestFlagOffUnchanged:
    def test_local_only_deployment_never_calls_ayla(
        self,
        settings,
        tenant,
        master,
        pending,
    ):
        settings.BOOKING_VIA_AYLA_REST = False
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert calls == []
        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.APPROVED
