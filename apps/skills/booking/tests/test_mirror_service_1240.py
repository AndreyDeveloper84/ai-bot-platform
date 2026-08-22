"""DRF-1240 — the mirror upsert must not blank a service it already knew."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from apps.booking.models import RemoteBookingProxy
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(slug="mirror-1240", name="Mirror Salon")


@pytest.fixture
def bot_user(db, tenant):
    return BotUser.objects.create(
        tenant=tenant, channel="max", channel_user_id="1240", display_name="Пробный"
    )


class _Record:
    def __init__(self, raw):
        self.raw = raw


def _call(tenant, bot_user, raw, start_at):
    from apps.skills.booking.tools import _upsert_remote_booking_proxy

    _upsert_remote_booking_proxy(
        tenant=tenant, bot_user=bot_user, record=_Record(raw), start_at=start_at
    )


def test_a_reschedule_without_a_service_keeps_the_one_on_the_row(
    tenant, bot_user, settings, monkeypatch
):
    """The defect this pins: Ayla's payload has no salon service, so the
    second write used to overwrite a good service_id with NULL — turning a
    named visit on the day board into a nameless one."""
    monkeypatch.setattr("apps.skills.booking.tools._booking_via_ayla", lambda: True)

    appt = uuid.uuid4()
    service = uuid.uuid4()
    start = datetime.now(tz=timezone.utc) + timedelta(days=1)

    _call(
        tenant,
        bot_user,
        {"ayla_appointment_id": str(appt), "service_id": str(service)},
        start,
    )
    row = RemoteBookingProxy.all_tenants.get(appointment_id=appt)
    assert row.service_id == service

    # The move: same booking, response carries no service at all.
    _call(
        tenant,
        bot_user,
        {"ayla_appointment_id": str(appt)},
        start + timedelta(hours=2),
    )

    row.refresh_from_db()
    assert row.start_at == start + timedelta(hours=2), "the move itself must apply"
    assert row.service_id == service, "absent means «no news», never «it is gone»"
