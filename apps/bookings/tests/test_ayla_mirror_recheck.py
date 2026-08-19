"""Send-time re-check against the Ayla mirror (DRF-1144).

The defect these tests pin: a reminder scheduled on the Ayla path had NO
send-time state check at all. ``_recheck_booking_state`` only knew how to read
``BookingRequest.status``, and every Ayla-path reminder carries a NULL
``booking_request`` FK, so the classifier returned an unconditional ``send``.
The only thing that could stop the beat was ``booking.cancelled`` having
already flipped the row to CANCELLED — and on the pilot that route missed,
which is how ``sent`` reminders ended up against appointments that no longer
exist.

Every assertion here is on observable outcome — the reminder's stored status
and whether an outbound message actually left the process — not on whether
some helper was called.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.bookings.tasks import send_due_reminders
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="mirror-recheck", name="Mirror Recheck")


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(slug="mirror-recheck-2", name="Mirror Recheck 2")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-mirror",
        chat_id="chat-mirror",
        phone="79991234567",
        client_name="Anna",
    )


def _proxy(*, tenant: Tenant, bot_user: BotUser, appointment_id: uuid.UUID, status: str):
    start_at = timezone.now() + timedelta(hours=2)
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=appointment_id,
        tenant=tenant,
        bot_user=bot_user,
        start_at=start_at,
        end_at=start_at + timedelta(hours=1),
        status=status,
    )


def _due_reminder(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    ayla_appointment_id: uuid.UUID | None = None,
    yclients_record_id: str | None = None,
    kind: str = BookingReminder.Kind.DAY_BEFORE,
) -> BookingReminder:
    """A PENDING reminder whose ``scheduled_at`` has already passed."""
    now = timezone.now()
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        booking_request=None,
        ayla_appointment_id=ayla_appointment_id,
        yclients_record_id=yclients_record_id,
        chat_id=bot_user.chat_id,
        visit_at=now + timedelta(hours=24),
        kind=kind,
        status=BookingReminder.Status.PENDING,
        scheduled_at=now - timedelta(minutes=5),
        master_name="Lera",
        service_name="Strizhka",
    )


def _run_dispatch():
    """Run the beat with the outbound channel captured.

    Returns ``(result_counters, sent_calls)``. ``sent_calls`` is the list of
    messages that actually left — an empty list is the property DRF-1144 is
    about.
    """
    with patch("apps.bookings.tasks.send_message") as send:
        result = send_due_reminders()
    return result, send.call_args_list


# -- the mirror says the visit will not happen ------------------------------


@pytest.mark.parametrize("mirror_status", ["cancelled", "completed", "no_show"])
def test_terminal_mirror_status_drops_instead_of_sending(tenant, bot_user, mirror_status):
    appointment_id = uuid.uuid4()
    _proxy(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        status=mirror_status,
    )
    reminder = _due_reminder(tenant=tenant, bot_user=bot_user, ayla_appointment_id=appointment_id)

    result, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert sent == [], "a reminder must not go out for a booking that will not happen"
    assert reminder.status == BookingReminder.Status.STALE_DROPPED
    assert result["stale"] == 1
    assert result["sent"] == 0


def test_missing_mirror_row_drops_instead_of_sending(tenant, bot_user):
    """The DRF-1144 pilot case: reminder alive, appointment gone.

    No ``RemoteBookingProxy`` row exists for this appointment id. Before the
    fix the beat sent the T-24h reminder about it.
    """
    reminder = _due_reminder(tenant=tenant, bot_user=bot_user, ayla_appointment_id=uuid.uuid4())

    result, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert sent == []
    assert reminder.status == BookingReminder.Status.STALE_DROPPED
    assert result["stale"] == 1


def test_mirror_row_owned_by_another_tenant_is_not_trusted(tenant, other_tenant, bot_user):
    """A mirror row under a different tenant must read as absent, not as live."""
    appointment_id = uuid.uuid4()
    other_user = BotUser.all_tenants.create(
        tenant=other_tenant,
        channel="max",
        channel_user_id="bu-other",
        chat_id="chat-other",
        phone="79990000000",
        client_name="Boris",
    )
    _proxy(
        tenant=other_tenant,
        bot_user=other_user,
        appointment_id=appointment_id,
        status="confirmed",
    )
    reminder = _due_reminder(tenant=tenant, bot_user=bot_user, ayla_appointment_id=appointment_id)

    _, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert sent == []
    assert reminder.status == BookingReminder.Status.STALE_DROPPED


# -- the dialog booking path: identity parked in yclients_record_id ---------


def test_dialog_path_reminder_is_dropped_when_mirror_is_cancelled(tenant, bot_user):
    """The product's main booking path.

    ``apps.skills.booking.tools._schedule_reminders`` routes through the R1
    factory, which stores the Ayla appointment UUID in ``yclients_record_id``
    and leaves ``ayla_appointment_id`` NULL. Such a reminder must still be
    re-checked against the mirror.
    """
    appointment_id = uuid.uuid4()
    _proxy(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        status="cancelled",
    )
    reminder = _due_reminder(
        tenant=tenant, bot_user=bot_user, yclients_record_id=str(appointment_id)
    )

    _, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert sent == []
    assert reminder.status == BookingReminder.Status.STALE_DROPPED


def test_dialog_path_reminder_matches_uppercase_uuid_spelling(tenant, bot_user):
    appointment_id = uuid.uuid4()
    _proxy(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        status="cancelled",
    )
    reminder = _due_reminder(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=str(appointment_id).upper(),
    )

    _, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert sent == []
    assert reminder.status == BookingReminder.Status.STALE_DROPPED


# -- the mirror says the visit is still on: nothing changes -----------------


@pytest.mark.parametrize(
    "mirror_status", ["confirmed", "pending_payment", "awaiting_payment", "tentative"]
)
def test_live_mirror_status_still_sends(tenant, bot_user, mirror_status):
    """Regression guard. ``awaiting_payment`` is Ayla's wire value and is NOT
    in ``RemoteBookingProxy.Status.choices`` — it must read as live, not as an
    unrecognised slug."""
    appointment_id = uuid.uuid4()
    _proxy(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        status=mirror_status,
    )
    reminder = _due_reminder(tenant=tenant, bot_user=bot_user, ayla_appointment_id=appointment_id)

    result, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert len(sent) == 1
    assert reminder.status == BookingReminder.Status.SENT_NO_REPLY
    assert result["sent"] == 1


def test_unrecognised_mirror_status_defers_without_sending(tenant, bot_user):
    """A state slug we do not understand must not become a send.

    The row stays PENDING so the next 15-minute tick re-checks it once the
    code (or the operator) learns what the slug means.
    """
    appointment_id = uuid.uuid4()
    _proxy(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        status="dispute",
    )
    reminder = _due_reminder(tenant=tenant, bot_user=bot_user, ayla_appointment_id=appointment_id)

    result, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert sent == []
    assert reminder.status == BookingReminder.Status.PENDING
    assert result["deferred"] == 1


def test_legacy_yclients_integer_record_id_is_untouched(tenant, bot_user):
    """A real YClients reminder has an integer record id and no Ayla mirror.

    It must keep its pre-DRF-1144 behaviour: send.
    """
    reminder = _due_reminder(tenant=tenant, bot_user=bot_user, yclients_record_id="1284412")

    result, sent = _run_dispatch()

    reminder.refresh_from_db()
    assert len(sent) == 1
    assert reminder.status == BookingReminder.Status.SENT_NO_REPLY
    assert result["sent"] == 1
