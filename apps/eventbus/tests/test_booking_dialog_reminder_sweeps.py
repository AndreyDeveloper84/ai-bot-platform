"""Lifecycle sweeps must reach dialog-created reminders (DRF-1144).

A booking made in the bot's own dialog schedules its reminders through the R1
factory (``apps.skills.booking.tools._schedule_reminders`` ->
``apps.bookings.reminders_factory.create_reminders_for_booking``). That factory
parks the Ayla appointment UUID in ``yclients_record_id`` and leaves
``ayla_appointment_id`` NULL.

Every consumer-side sweep used to filter on ``ayla_appointment_id`` alone, so
``booking.cancelled`` matched **zero** reminder rows for the product's main
booking path: the reminders stayed PENDING and the beat sent them. These tests
pin the sweeps against both column spellings.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.eventbus.consumers.booking import (
    handle_booking_cancelled,
    handle_booking_no_show,
    handle_booking_rescheduled,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

TENANT_ID = "2f6b1d4e-7a83-4c15-9e02-5b8d3a1c6f47"
APPOINTMENT_ID = uuid.UUID("c4e9a7b2-1d35-4f68-8a0c-9e2b7d4f1a63")
START_AT = dt.datetime(2026, 5, 22, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def _pilot_allowlist(settings) -> None:
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({TENANT_ID})
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset(
        {"booking.cancelled", "booking.no_show", "booking.rescheduled"}
    )


@pytest.fixture
def tenant(db, _pilot_allowlist) -> Tenant:
    obj, _ = Tenant.objects.get_or_create(
        id=TENANT_ID,
        defaults={"slug": "t-dialog-sweep", "name": "Dialog sweep tenant"},
    )
    return obj


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-dialog",
        chat_id="chat-dialog",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def proxy(tenant: Tenant, bot_user: BotUser) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=APPOINTMENT_ID,
        tenant=tenant,
        bot_user=bot_user,
        start_at=START_AT,
        end_at=START_AT + dt.timedelta(hours=1),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )


def _dialog_reminder(
    *, tenant: Tenant, bot_user: BotUser, kind: str = BookingReminder.Kind.DAY_BEFORE
) -> BookingReminder:
    """Exactly the row shape the dialog booking path writes."""
    offset = (
        dt.timedelta(hours=24) if kind == BookingReminder.Kind.DAY_BEFORE else dt.timedelta(hours=2)
    )
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        booking_request=None,
        ayla_appointment_id=None,
        yclients_record_id=str(APPOINTMENT_ID),
        chat_id=bot_user.chat_id,
        visit_at=START_AT,
        kind=kind,
        status=BookingReminder.Status.PENDING,
        scheduled_at=START_AT - offset,
        master_name="Lera",
        service_name="Strizhka",
    )


def _envelope(*, event_name: str, data: dict[str, Any], event_id: str) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 5, 21, 14, 32, 11, tzinfo=dt.timezone.utc),
        tenant_id=TENANT_ID,
        # ``handle_booking_rescheduled`` calls ``UUID(envelope.user_id)``
        # unconditionally, so a NULL here is a TypeError rather than a
        # skipped conversation touch. Supply an unlinked-but-valid id.
        user_id="f1a2b3c4-d5e6-4789-9abc-def012345678",
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


def test_cancellation_cancels_dialog_created_reminders(tenant, bot_user, proxy):
    reminder = _dialog_reminder(tenant=tenant, bot_user=bot_user)

    handle_booking_cancelled(
        _envelope(
            event_name="booking.cancelled",
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F71",  # pragma: allowlist secret
            data={
                "appointment_id": str(APPOINTMENT_ID),
                "cancelled_by": "client",
                "reason_code": "client_request",
            },
        )
    )

    reminder.refresh_from_db()
    assert reminder.status == BookingReminder.Status.CANCELLED


def test_no_show_cancels_dialog_created_reminders(tenant, bot_user, proxy):
    reminder = _dialog_reminder(tenant=tenant, bot_user=bot_user)

    handle_booking_no_show(
        _envelope(
            event_name="booking.no_show",
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F72",  # pragma: allowlist secret
            data={"appointment_id": str(APPOINTMENT_ID)},
        )
    )

    reminder.refresh_from_db()
    assert reminder.status == BookingReminder.Status.CANCELLED


def test_reschedule_repegs_dialog_created_reminders(tenant, bot_user, proxy):
    reminder = _dialog_reminder(tenant=tenant, bot_user=bot_user)
    new_start = START_AT + dt.timedelta(days=1)

    handle_booking_rescheduled(
        _envelope(
            event_name="booking.rescheduled",
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F73",  # pragma: allowlist secret
            data={
                "appointment_id": str(APPOINTMENT_ID),
                "old_start_at": START_AT.isoformat(),
                "new_start_at": new_start.isoformat(),
                "rescheduled_by": "client",
            },
        )
    )

    reminder.refresh_from_db()
    assert reminder.visit_at == new_start
    assert reminder.scheduled_at == new_start - dt.timedelta(hours=24)


def test_cancellation_still_cancels_event_path_reminders(tenant, bot_user, proxy):
    """Regression guard for the column the sweeps already matched."""
    reminder = BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        booking_request=None,
        ayla_appointment_id=APPOINTMENT_ID,
        yclients_record_id=None,
        chat_id=bot_user.chat_id,
        visit_at=START_AT,
        kind=BookingReminder.Kind.TWO_HOURS,
        status=BookingReminder.Status.PENDING,
        scheduled_at=START_AT - dt.timedelta(hours=2),
        master_name="Lera",
        service_name="Strizhka",
    )

    handle_booking_cancelled(
        _envelope(
            event_name="booking.cancelled",
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F74",  # pragma: allowlist secret
            data={"appointment_id": str(APPOINTMENT_ID), "cancelled_by": "client"},
        )
    )

    reminder.refresh_from_db()
    assert reminder.status == BookingReminder.Status.CANCELLED


def test_cancellation_does_not_touch_an_unrelated_yclients_reminder(tenant, bot_user, proxy):
    """A legacy YClients row whose integer id is unrelated must be left alone."""
    reminder = BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        booking_request=None,
        ayla_appointment_id=None,
        yclients_record_id="1284412",
        chat_id=bot_user.chat_id,
        visit_at=START_AT,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.PENDING,
        scheduled_at=START_AT - dt.timedelta(hours=24),
        master_name="Lera",
        service_name="Strizhka",
    )

    handle_booking_cancelled(
        _envelope(
            event_name="booking.cancelled",
            event_id="01J9HXKM8Z2T4V6R8Q1P3D5F75",  # pragma: allowlist secret
            data={"appointment_id": str(APPOINTMENT_ID), "cancelled_by": "client"},
        )
    )

    reminder.refresh_from_db()
    assert reminder.status == BookingReminder.Status.PENDING
