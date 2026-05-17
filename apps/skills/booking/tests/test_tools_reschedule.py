"""``reschedule_booking`` + ``execute_reschedule`` tool tests (B5 / DRF-841).

Reschedule is implemented as cancel-and-create (YClients has no native
reschedule endpoint). The partial-failure window — cancel succeeds,
create fails — is the destructive corner case: the test suite makes
sure the BookingRequest ends in ``CANCELLED`` (not ``RESCHEDULED``)
in that path, the caller gets a partial-failure error code, and no
retry of the create happens.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingReminder, BookingRequest, PendingBookingAction
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    UserRecord,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.skills.booking.tools import execute_reschedule, reschedule_booking
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="resched-tools", name="Resched")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-r",
        chat_id="bu-r",
        phone="79991234567",
        client_name="Anna",
    )


class FakeClient:
    def __init__(self) -> None:
        self.cancel_calls: list[int] = []
        self.cancel_exc: Exception | None = None
        self.create_calls: list[dict[str, Any]] = []
        self.create_response: BookingRecord | None = None
        self.create_exc: Exception | None = None
        self.user_records: list[UserRecord] = []
        self.user_records_exc: Exception | None = None
        self.times: list[AvailableTime] = []
        self.times_exc: Exception | None = None

    def cancel_record(self, *, record_id: int) -> bool:
        self.cancel_calls.append(record_id)
        if self.cancel_exc is not None:
            raise self.cancel_exc
        return True

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        if self.create_exc is not None:
            raise self.create_exc
        return self.create_response or BookingRecord(record_id=999, record_hash="h", raw={})

    def get_user_records(self) -> list[UserRecord]:
        if self.user_records_exc is not None:
            raise self.user_records_exc
        return list(self.user_records)

    def get_available_times(self, **_: Any) -> list[AvailableTime]:
        if self.times_exc is not None:
            raise self.times_exc
        return list(self.times)


def _make_booking(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    yc_id: int = 555,
) -> BookingRequest:
    with tenant_scope(tenant):
        return BookingRequest.objects.create(
            tenant=tenant,
            bot_user=bot_user,
            service_name="Массаж",
            master_name="Ольга",
            client_name="Anna",
            client_phone="79991234567",
            comment=f"Bot booking | yclients_record_id={yc_id}",
            source="bot",
            status=BookingRequest.Status.CONFIRMED,
        )


def _user_record(*, id_: int = 555, staff_id: int = 11, service_id: int = 22) -> UserRecord:
    return UserRecord(
        id=id_,
        services=[{"id": service_id, "title": "Массаж"}],
        company={},
        staff={"id": staff_id, "name": "Ольга"},
        date="2026-05-20T10:00:00",
        datetime="2026-05-20T10:00:00",
        seance_length=3600,
        raw={},
    )


def _future_iso(hours: int = 48) -> str:
    return (dj_timezone.now() + timedelta(hours=hours)).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# reschedule_booking — preview path
# ---------------------------------------------------------------------------


class TestReschedulePreview:
    def test_happy_path_persists_pending(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.user_records = [_user_record(id_=555, staff_id=11, service_id=22)]
        new_dt = _future_iso(48)
        # Slot the master has free at the new datetime.
        client.times = [
            AvailableTime(
                time=new_dt.split("T", 1)[1][:5],
                datetime=new_dt,
                seance_length_s=3600,
            )
        ]
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={"record_id": 555, "new_datetime": new_dt},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert result.pending is not None
        assert result.pending.kind == PendingBookingAction.Kind.RESCHEDULE
        # No destructive call yet.
        assert client.cancel_calls == []
        assert client.create_calls == []
        # Pending row carries staff/service we'll use on execute.
        row = PendingBookingAction.all_tenants.get(pk=result.pending.token)
        assert row.payload["master_id"] == 11
        assert row.payload["service_id"] == 22
        assert row.payload["new_datetime"] == new_dt

    def test_anti_hallucination_invalid_record(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={
                    "record_id": 9999,
                    "new_datetime": _future_iso(48),
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"

    def test_past_datetime_rejected(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        past = (dj_timezone.now() - timedelta(days=1)).replace(microsecond=0).isoformat()
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={"record_id": 555, "new_datetime": past},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "past_datetime"

    def test_invalid_datetime_format(self, tenant: Tenant, bot_user: BotUser) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={"record_id": 555, "new_datetime": "tomorrow at 2"},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_datetime"

    def test_slot_unavailable_returns_clarification(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.user_records = [_user_record(id_=555)]
        # Master has no slot at the requested time.
        client.times = []
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "slot_unavailable"

    def test_yclients_user_records_unreachable_returns_error(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.user_records_exc = YClientsUnavailableError("x")
        with tenant_scope(tenant):
            result = reschedule_booking(
                client=client,
                arguments={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "record_not_found"


# ---------------------------------------------------------------------------
# execute_reschedule — the actual cancel-and-create
# ---------------------------------------------------------------------------


class TestExecuteReschedule:
    def test_happy_path(self, tenant: Tenant, bot_user: BotUser) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        # Pending reminder on the OLD record.
        visit_at = dj_timezone.now() + timedelta(days=1)
        BookingReminder.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            yclients_record_id="555",
            chat_id="bu-r",
            visit_at=visit_at,
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.PENDING,
            scheduled_at=visit_at - timedelta(hours=24),
            master_name="Ольга",
            service_name="Массаж",
        )
        client = FakeClient()
        client.create_response = BookingRecord(record_id=888, record_hash="h", raw={})
        new_dt = _future_iso(72)
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": new_dt,
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert result.confirmation is not None
        assert result.confirmation.record_id == 888

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.RESCHEDULED

        # New BookingRequest created with the new record id marker.
        new_row = BookingRequest.all_tenants.filter(
            comment__contains="yclients_record_id=888",
        ).first()
        assert new_row is not None
        assert new_row.status == BookingRequest.Status.CONFIRMED
        assert "rescheduled_from=555" in new_row.comment

        # Old reminder cancelled, new reminders scheduled.
        old_reminder = BookingReminder.all_tenants.filter(
            yclients_record_id="555",
        ).first()
        assert old_reminder is not None
        assert old_reminder.status == BookingReminder.Status.CANCELLED

        new_reminder = BookingReminder.all_tenants.filter(
            yclients_record_id="888",
            kind=BookingReminder.Kind.DAY_BEFORE,
        ).first()
        assert new_reminder is not None
        assert new_reminder.status == BookingReminder.Status.PENDING

    def test_cancel_fails_keeps_old_booking(self, tenant: Tenant, bot_user: BotUser) -> None:
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.cancel_exc = YClientsAPIError("http_404")
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "yclients_cancel_failure"
        # Old booking left untouched.
        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CONFIRMED
        # No create attempted.
        assert client.create_calls == []

    def test_partial_failure_old_cancelled_no_retry(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """The destructive corner case.

        Cancel succeeded but create failed. We MUST NOT retry the
        create — risk of double-booking. The old row flips to
        ``CANCELLED`` (not ``RESCHEDULED``) so the data layer reflects
        reality, and the error code lets the caller emit a manager
        notification.
        """
        booking = _make_booking(tenant, bot_user, yc_id=555)
        client = FakeClient()
        client.create_exc = YClientsAPIError("http_400: slot collision")
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                    "master_id": 11,
                    "service_id": 22,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "booking_reschedule_partial_failure"
        assert client.cancel_calls == [555]
        # Exactly ONE create attempt — no retry.
        assert len(client.create_calls) == 1

        booking.refresh_from_db()
        assert booking.status == BookingRequest.Status.CANCELLED

    def test_invalid_payload_short_circuits(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={"record_id": 555},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_payload"
        assert client.cancel_calls == []

    def test_record_not_owned_rejected(self, tenant: Tenant, bot_user: BotUser) -> None:
        # No BookingRequest matches → ownership check fails at execute.
        client = FakeClient()
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=client,
                payload={
                    "record_id": 555,
                    "new_datetime": _future_iso(48),
                    "master_id": 11,
                    "service_id": 22,
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "invalid_record_id"
        assert client.cancel_calls == []
