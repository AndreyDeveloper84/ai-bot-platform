"""DRF-915 — Reminder acceptance for Controlled Pilot.

Verifies reminder materialization / cancellation safety for a synthetic
BotUser with a real (non-empty) chat_id under the ``formula-tela`` tenant.

This is the narrow deployed-runtime acceptance that DRF-954 could not
prove because its synthetic user had no ``chat_id``.

All tests run against the same synthetic identity:

* tenant: ``formula-tela``
* channel: ``max``
* channel_user_id / chat_id: ``drf915-accept-20260808-001``
* ayla_user_id: ``f0971656-3452-481b-affb-2c4e4ca43ed7``

The delivery worker is always patched so no real MAX message is sent.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from freezegun import freeze_time

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.bookings.tasks import send_due_reminders
from apps.eventbus.consumers.booking import (
    handle_booking_cancelled,
    handle_booking_confirmed,
    handle_booking_created,
    handle_booking_rescheduled,
)
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


# ── synthetic identity mapping ───────────────────────────────────────────────
TENANT_SLUG = "formula-tela"
SYNTHETIC_CHANNEL_USER_ID = "drf915-accept-20260808-001"
SYNTHETIC_CHAT_ID = "drf915-accept-20260808-001"
SYNTHETIC_AYLA_USER_ID = "f0971656-3452-481b-affb-2c4e4ca43ed7"
SYNTHETIC_DISPLAY_NAME = "DRF-915 Acceptance Tester"

APPOINTMENT_ID_FUTURE = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
APPOINTMENT_ID_BACKDATED = "b2c3d4e5-f6a7-8901-bcde-f12345678901"
APPOINTMENT_ID_SENT = "c3d4e5f6-a7b8-9012-cdef-123456789012"
APPOINTMENT_ID_RESCHEDULE = "d4e5f6a7-b8c9-0123-defa-234567890123"
APPOINTMENT_ID_RESCHEDULE_SENT = "e5f6a7b8-c9d0-1234-efab-345678901234"


@pytest.fixture(autouse=True)
def _freeze_time():
    """Freeze wall clock so future reminders are deterministic."""
    with freeze_time("2026-08-10T10:00:00Z"):
        yield


@pytest.fixture(autouse=True)
def _pilot_allowlist(settings) -> None:
    """Authorize the formula-tela tenant and the booking event set."""
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset(
        {
            "booking.created",
            "booking.confirmed",
            "booking.cancelled",
            "booking.rescheduled",
        }
    )


@pytest.fixture
def tenant() -> Tenant:
    """The pilot tenant."""
    obj, _ = Tenant.objects.get_or_create(
        slug=TENANT_SLUG,
        defaults={"name": "Формула тела", "timezone": "Europe/Moscow"},
    )
    # The allowlist is populated dynamically so it matches the real tenant id.
    from django.conf import settings

    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset({str(obj.id)})
    return obj


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    """Synthetic deliverable BotUser for this acceptance run."""
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=SYNTHETIC_CHANNEL_USER_ID,
        chat_id=SYNTHETIC_CHAT_ID,
        display_name=SYNTHETIC_DISPLAY_NAME,
        ayla_user_id=UUID(SYNTHETIC_AYLA_USER_ID),
        timezone="Europe/Moscow",
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _envelope(
    *,
    event_name: str,
    data: dict[str, Any],
    event_id: str,
    tenant_id: str,
    user_id: str = SYNTHETIC_AYLA_USER_ID,
) -> IngestEnvelope:
    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=1,
        occurred_at=dt.datetime(2026, 8, 10, 10, 0, 0, tzinfo=dt.timezone.utc),
        tenant_id=tenant_id,
        user_id=user_id,
        actor="user",
        correlation_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        causation_id=None,
        data=data,
    )


def _booking_created_data(
    appointment_id: str,
    start_at: str,
    end_at: str,
    status: str,
) -> dict[str, Any]:
    return {
        "appointment_id": appointment_id,
        "specialist_id": "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17",
        "service_id": "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c",
        "start_at": start_at,
        "end_at": end_at,
        "status": status,
        "price_total": "1800.00",
        "source": "mobile_app",
    }


def _reminders_for(appointment_id: str):
    return BookingReminder.all_tenants.filter(ayla_appointment_id=UUID(appointment_id))


# ── 1. awaiting_payment → no reminders ───────────────────────────────────────


def test_awaiting_payment_creates_no_reminders(tenant: Tenant, bot_user: BotUser) -> None:
    env = _envelope(
        event_id="01J9DRF915AWAIT01",
        event_name="booking.created",
        tenant_id=str(tenant.id),
        data=_booking_created_data(
            appointment_id=APPOINTMENT_ID_FUTURE,
            start_at="2026-08-15T12:00:00+03:00",
            end_at="2026-08-15T13:00:00+03:00",
            status="awaiting_payment",
        ),
    )
    handle_booking_created(env)

    proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID_FUTURE))
    assert proxy.status == RemoteBookingProxy.Status.PENDING_PAYMENT
    assert _reminders_for(APPOINTMENT_ID_FUTURE).count() == 0


# ── 2. confirmed → future eligible reminders ─────────────────────────────────


def test_confirmed_creates_future_eligible_reminders(tenant: Tenant, bot_user: BotUser) -> None:
    env = _envelope(
        event_id="01J9DRF915CONFIRM01",
        event_name="booking.created",
        tenant_id=str(tenant.id),
        data=_booking_created_data(
            appointment_id=APPOINTMENT_ID_FUTURE,
            start_at="2026-08-15T12:00:00+03:00",
            end_at="2026-08-15T13:00:00+03:00",
            status="confirmed",
        ),
    )
    handle_booking_created(env)

    reminders = _reminders_for(APPOINTMENT_ID_FUTURE)
    assert reminders.count() == 2

    kinds = set(reminders.values_list("kind", flat=True))
    assert kinds == {BookingReminder.Kind.DAY_BEFORE, BookingReminder.Kind.TWO_HOURS}

    for r in reminders:
        assert r.status == BookingReminder.Status.PENDING
        assert r.scheduled_at > dt.datetime.now(dt.timezone.utc)
        assert r.bot_user_id == bot_user.id
        assert r.chat_id == SYNTHETIC_CHAT_ID
        assert r.tenant_id == tenant.id

    # Explicit offset checks against frozen now.
    day_before = reminders.get(kind=BookingReminder.Kind.DAY_BEFORE)
    two_hours = reminders.get(kind=BookingReminder.Kind.TWO_HOURS)
    assert day_before.scheduled_at.isoformat() == "2026-08-14T09:00:00+00:00"
    assert two_hours.scheduled_at.isoformat() == "2026-08-15T07:00:00+00:00"


# ── 3. repeated confirmed → no duplicates ────────────────────────────────────


def test_repeated_confirmed_does_not_duplicate_reminders(tenant: Tenant, bot_user: BotUser) -> None:
    env_created = _envelope(
        event_id="01J9DRF915DUP01",
        event_name="booking.created",
        tenant_id=str(tenant.id),
        data=_booking_created_data(
            appointment_id=APPOINTMENT_ID_FUTURE,
            start_at="2026-08-15T12:00:00+03:00",
            end_at="2026-08-15T13:00:00+03:00",
            status="confirmed",
        ),
    )
    handle_booking_created(env_created)
    assert _reminders_for(APPOINTMENT_ID_FUTURE).count() == 2

    env_confirmed = _envelope(
        event_id="01J9DRF915DUP02",
        event_name="booking.confirmed",
        tenant_id=str(tenant.id),
        data={"appointment_id": APPOINTMENT_ID_FUTURE},
    )
    handle_booking_confirmed(env_confirmed)

    assert _reminders_for(APPOINTMENT_ID_FUTURE).count() == 2
    assert (
        _reminders_for(APPOINTMENT_ID_FUTURE).filter(status=BookingReminder.Status.PENDING).count()
        == 2
    )


# ── 4. backdated offsets → skipped ───────────────────────────────────────────


def test_backdated_offsets_are_skipped(tenant: Tenant, bot_user: BotUser) -> None:
    """Booking confirmed only 1 hour before visit — both offsets are past."""
    env = _envelope(
        event_id="01J9DRF915LATE01",
        event_name="booking.created",
        tenant_id=str(tenant.id),
        data=_booking_created_data(
            appointment_id=APPOINTMENT_ID_BACKDATED,
            start_at="2026-08-10T14:00:00+03:00",  # 11:00 UTC, 1 h after frozen now
            end_at="2026-08-10T15:00:00+03:00",
            status="confirmed",
        ),
    )
    handle_booking_created(env)

    proxy = RemoteBookingProxy.all_tenants.get(appointment_id=UUID(APPOINTMENT_ID_BACKDATED))
    assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
    assert _reminders_for(APPOINTMENT_ID_BACKDATED).count() == 0


# ── 5. SENT reminders are not resurrected ────────────────────────────────────


def test_sent_reminder_not_resurrected_by_confirmed(tenant: Tenant, bot_user: BotUser) -> None:
    proxy = RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID_SENT),
        tenant=tenant,
        bot_user=bot_user,
        start_at=dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 8, 15, 13, 0, tzinfo=dt.timezone.utc),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )
    reminder = BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        ayla_appointment_id=UUID(APPOINTMENT_ID_SENT),
        chat_id=SYNTHETIC_CHAT_ID,
        visit_at=proxy.start_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT,
        scheduled_at=proxy.start_at - dt.timedelta(hours=25),
    )
    original_scheduled_at = reminder.scheduled_at

    env = _envelope(
        event_id="01J9DRF915SENTREPLAY01",
        event_name="booking.confirmed",
        tenant_id=str(tenant.id),
        data={"appointment_id": APPOINTMENT_ID_SENT},
    )
    handle_booking_confirmed(env)

    reminder.refresh_from_db()
    assert reminder.status == BookingReminder.Status.SENT
    assert reminder.scheduled_at == original_scheduled_at


# ── 6. cancel → PENDING reminders cancelled ──────────────────────────────────


def test_cancel_cancels_pending_reminders(tenant: Tenant, bot_user: BotUser) -> None:
    env_created = _envelope(
        event_id="01J9DRF915CANCEL01",
        event_name="booking.created",
        tenant_id=str(tenant.id),
        data=_booking_created_data(
            appointment_id=APPOINTMENT_ID_FUTURE,
            start_at="2026-08-15T12:00:00+03:00",
            end_at="2026-08-15T13:00:00+03:00",
            status="confirmed",
        ),
    )
    handle_booking_created(env_created)
    assert (
        _reminders_for(APPOINTMENT_ID_FUTURE).filter(status=BookingReminder.Status.PENDING).count()
        == 2
    )

    env_cancelled = _envelope(
        event_id="01J9DRF915CANCEL02",
        event_name="booking.cancelled",
        tenant_id=str(tenant.id),
        data={
            "appointment_id": APPOINTMENT_ID_FUTURE,
            "cancelled_by": "user",
            "reason_code": "user_changed_plans",
            "cancelled_at": "2026-08-10T10:05:00.000Z",
        },
    )
    handle_booking_cancelled(env_cancelled)

    assert (
        _reminders_for(APPOINTMENT_ID_FUTURE).filter(status=BookingReminder.Status.PENDING).count()
        == 0
    )
    assert (
        _reminders_for(APPOINTMENT_ID_FUTURE)
        .filter(status=BookingReminder.Status.CANCELLED)
        .count()
        == 2
    )


# ── 7. reschedule → no dangerous stale reminder remains ──────────────────────


def test_reschedule_updates_pending_reminders(tenant: Tenant, bot_user: BotUser) -> None:
    env_created = _envelope(
        event_id="01J9DRF915RESCHED01",
        event_name="booking.created",
        tenant_id=str(tenant.id),
        data=_booking_created_data(
            appointment_id=APPOINTMENT_ID_RESCHEDULE,
            start_at="2026-08-15T12:00:00+03:00",
            end_at="2026-08-15T13:00:00+03:00",
            status="confirmed",
        ),
    )
    handle_booking_created(env_created)

    old_day_before = (
        _reminders_for(APPOINTMENT_ID_RESCHEDULE)
        .get(kind=BookingReminder.Kind.DAY_BEFORE)
        .scheduled_at
    )
    old_two_hours = (
        _reminders_for(APPOINTMENT_ID_RESCHEDULE)
        .get(kind=BookingReminder.Kind.TWO_HOURS)
        .scheduled_at
    )

    env_rescheduled = _envelope(
        event_id="01J9DRF915RESCHED02",
        event_name="booking.rescheduled",
        tenant_id=str(tenant.id),
        data={
            "appointment_id": APPOINTMENT_ID_RESCHEDULE,
            "old_start_at": "2026-08-15T12:00:00+03:00",
            "new_start_at": "2026-08-16T10:00:00+03:00",
            "rescheduled_by": "admin",
        },
    )
    handle_booking_rescheduled(env_rescheduled)

    reminders = _reminders_for(APPOINTMENT_ID_RESCHEDULE)
    assert reminders.count() == 2

    new_day_before = reminders.get(kind=BookingReminder.Kind.DAY_BEFORE).scheduled_at
    new_two_hours = reminders.get(kind=BookingReminder.Kind.TWO_HOURS).scheduled_at

    assert new_day_before > old_day_before
    assert new_two_hours > old_two_hours
    assert new_day_before.isoformat() == "2026-08-15T07:00:00+00:00"
    assert new_two_hours.isoformat() == "2026-08-16T05:00:00+00:00"

    assert reminders.filter(status=BookingReminder.Status.PENDING).count() == 2


def test_reschedule_does_not_rearm_sent_reminder(tenant: Tenant, bot_user: BotUser) -> None:
    """Known issue #1148: a SENT reminder that is rescheduled to the future
    stays SENT and does not become PENDING for the new visit time.

    For the Controlled Pilot this is an accepted P2 because the dangerous
    stale-reminder case (old PENDING firing at the old time) is prevented;
    the client simply won't receive a new reminder for the new date.
    """
    proxy = RemoteBookingProxy.all_tenants.create(
        appointment_id=UUID(APPOINTMENT_ID_RESCHEDULE_SENT),
        tenant=tenant,
        bot_user=bot_user,
        start_at=dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.timezone.utc),
        end_at=dt.datetime(2026, 8, 15, 13, 0, tzinfo=dt.timezone.utc),
        status=RemoteBookingProxy.Status.CONFIRMED,
    )
    reminder = BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        ayla_appointment_id=UUID(APPOINTMENT_ID_RESCHEDULE_SENT),
        chat_id=SYNTHETIC_CHAT_ID,
        visit_at=proxy.start_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=BookingReminder.Status.SENT,
        scheduled_at=proxy.start_at - dt.timedelta(hours=24),
    )
    original_scheduled_at = reminder.scheduled_at

    env = _envelope(
        event_id="01J9DRF915RESCHEDSENT01",
        event_name="booking.rescheduled",
        tenant_id=str(tenant.id),
        data={
            "appointment_id": APPOINTMENT_ID_RESCHEDULE_SENT,
            "old_start_at": "2026-08-15T12:00:00+03:00",
            "new_start_at": "2026-08-20T12:00:00+03:00",
            "rescheduled_by": "admin",
        },
    )
    handle_booking_rescheduled(env)

    reminder.refresh_from_db()
    assert reminder.status == BookingReminder.Status.SENT
    assert reminder.scheduled_at == original_scheduled_at

    # No duplicate row was created for the new date.
    assert _reminders_for(APPOINTMENT_ID_RESCHEDULE_SENT).count() == 1


# ── 8. worker safety: no real outbound send ──────────────────────────────────


def test_worker_dispatches_without_real_outbound_send(tenant: Tenant, bot_user: BotUser) -> None:
    """A due PENDING reminder is picked up, but send_message is mocked so no
    real MAX API call leaves the process."""
    BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        ayla_appointment_id=UUID(APPOINTMENT_ID_FUTURE),
        chat_id=SYNTHETIC_CHAT_ID,
        visit_at=dt.datetime(2026, 8, 15, 12, 0, tzinfo=dt.timezone.utc),
        kind=BookingReminder.Kind.TWO_HOURS,
        status=BookingReminder.Status.PENDING,
        scheduled_at=dt.datetime(2026, 8, 10, 9, 55, tzinfo=dt.timezone.utc),
        master_name="Acceptance Master",
        service_name="Acceptance Service",
    )

    with patch("apps.bookings.tasks.send_message") as mock_send:
        result = send_due_reminders()

    assert result["sent"] == 1
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["chat_id"] == SYNTHETIC_CHAT_ID

    # Cross-tenant leak check: no reminder rows for any other tenant.
    assert BookingReminder.all_tenants.exclude(tenant=tenant).count() == 0
