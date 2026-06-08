"""Flag-ON (``BOOKING_VIA_AYLA_REST``) booking-tool tests (S1 / #1016).

Exercises the Ayla REST path through the booking tools, driven by a
``FakeAylaBooking``-backed :class:`AylaYClientsAdapter` (the real client's own
round-trip is covered in ``apps/integrations/ayla/tests/test_booking_client.py``).

Asserts:

* ``execute_confirm`` upserts a ``RemoteBookingProxy`` (UUID PK) and writes NO
  local ``BookingRequest`` (Ayla owns canonical state, ADR-0009 rule #1);
* ``show_my_bookings`` surfaces the UUID handle, reading the proxy mirror;
* ``execute_cancel`` cancels via REST and flips the proxy to CANCELLED;
* ``execute_reschedule`` is NATIVE (same UUID, no cancel+create);
* the proxy upsert is idempotent on ``appointment_id`` (#442 convergence).

Flag OFF regression stays in the existing booking suites — this file only
covers flag ON.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from apps.booking.models import BookingRequest, PendingBookingAction, RemoteBookingProxy
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord, AylaUserRecord
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.tools import (
    cancel_booking,
    execute_cancel,
    execute_confirm,
    execute_reschedule,
    reschedule_booking,
    show_my_bookings,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_APPT = "3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34"


@pytest.fixture(autouse=True)
def _flag_on(settings) -> None:
    """All tests in this module run with the Ayla REST path ON."""
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="booking-ayla-tools", name="Booking Ayla Tools")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="telegram",
        channel_user_id="42",
        chat_id="42",
        phone="79991234567",
        client_name="Anna",
    )


class FakeAylaBooking:
    """Minimal Protocol-compatible fake for the flag-ON tool tests."""

    def __init__(self) -> None:
        self.create_response: AylaBookingRecord | None = None
        self.reschedule_response: AylaBookingRecord | None = None
        self.cancel_response = True
        self.user_records: list[AylaUserRecord] = []
        self.calls: list[dict[str, Any]] = []

    def get_services(self, *, master_id: int | None = None):  # pragma: no cover
        return []

    def get_masters(self, *, master_id: int | None = None):  # pragma: no cover
        return []

    def get_available_dates(self, *, master_id=None, service_ids=None):  # pragma: no cover
        return []

    def get_available_times(self, *, master_id, date, service_ids=None):  # pragma: no cover
        return []

    def create_appointment(self, **kwargs: Any) -> AylaBookingRecord:
        self.calls.append({"op": "create", **kwargs})
        return self.create_response or AylaBookingRecord(appointment_id=_APPT, raw={})

    def cancel_appointment(self, *, external_user_id, appointment_id, idempotency_key=None) -> bool:
        self.calls.append({"op": "cancel", "appointment_id": appointment_id})
        return self.cancel_response

    def reschedule_appointment(
        self, *, external_user_id, appointment_id, datetime, idempotency_key=None
    ) -> AylaBookingRecord:
        self.calls.append({"op": "reschedule", "appointment_id": appointment_id})
        return self.reschedule_response or AylaBookingRecord(appointment_id=appointment_id, raw={})

    def get_user_appointments(self, *, external_user_id) -> list[AylaUserRecord]:
        return list(self.user_records)


def _adapter(fake: FakeAylaBooking) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(client=fake, external_user_id="bot:telegram:42")


# ─── execute_confirm → proxy upsert, no BookingRequest ─────────────────────


class TestExecuteConfirmAyla:
    def _payload(self) -> dict[str, Any]:
        return {
            "master_id": 11,
            "service_id": 10,
            "slot_datetime": "2026-09-10T14:00:00",
            "client_phone": "79991234567",
            "client_name": "Anna",
            "master_name": "Ольга",
            "service_name": "Массаж",
        }

    def test_confirm_upserts_proxy_and_skips_booking_request(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        fake = FakeAylaBooking()
        fake.create_response = AylaBookingRecord(
            appointment_id=_APPT,
            raw={"duration_s": 3600, "specialist": {"id": _APPT}, "services": [{"id": _APPT}]},
        )
        result = execute_confirm(
            client=_adapter(fake),
            payload=self._payload(),
            tenant=tenant,
            bot_user=bot_user,
        )
        assert result.confirmation is not None and result.confirmation.ok is True
        # Ayla owns canonical state — no local BookingRequest row.
        assert BookingRequest.all_tenants.count() == 0
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=_APPT)
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
        assert proxy.source == RemoteBookingProxy.Source.AUTOMATION
        assert proxy.bot_user_id == bot_user.pk
        assert fake.calls[0]["op"] == "create"

    def test_confirm_proxy_upsert_is_idempotent(self, tenant: Tenant, bot_user: BotUser) -> None:
        fake = FakeAylaBooking()
        fake.create_response = AylaBookingRecord(appointment_id=_APPT, raw={"duration_s": 3600})
        for _ in range(2):
            execute_confirm(
                client=_adapter(fake),
                payload=self._payload(),
                tenant=tenant,
                bot_user=bot_user,
            )
        # update_or_create keyed on appointment_id → exactly one mirror row.
        assert RemoteBookingProxy.all_tenants.filter(appointment_id=_APPT).count() == 1


# ─── show_my_bookings → UUID handle from proxy ─────────────────────────────


class TestShowMyBookingsAyla:
    def test_handle_is_uuid_from_proxy(self, tenant: Tenant, bot_user: BotUser) -> None:
        from django.utils import timezone

        RemoteBookingProxy.all_tenants.create(
            appointment_id=_APPT,
            tenant=tenant,
            bot_user=bot_user,
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=1),
            status=RemoteBookingProxy.Status.CONFIRMED,
            source=RemoteBookingProxy.Source.AUTOMATION,
        )
        fake = FakeAylaBooking()
        fake.user_records = [
            AylaUserRecord(
                appointment_id=_APPT,
                services=[{"id": "svc"}],
                master={"id": "mst"},
                datetime="2026-09-10T14:00:00",
                duration_s=3600,
                raw={"ayla_appointment_id": _APPT},
            )
        ]
        result = show_my_bookings(client=_adapter(fake), tenant=tenant, bot_user=bot_user)
        assert len(result.bookings) == 1
        assert result.bookings[0].handle == _APPT
        # No int handle on the Ayla path.
        assert result.bookings[0].record_id == 0


# ─── cancel preview + execute ───────────────────────────────────────────────


class TestCancelAyla:
    def _mk_proxy(self, tenant: Tenant, bot_user: BotUser) -> RemoteBookingProxy:
        from django.utils import timezone

        return RemoteBookingProxy.all_tenants.create(
            appointment_id=_APPT,
            tenant=tenant,
            bot_user=bot_user,
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=1),
            status=RemoteBookingProxy.Status.CONFIRMED,
            source=RemoteBookingProxy.Source.AUTOMATION,
        )

    def test_cancel_preview_resolves_proxy(self, tenant: Tenant, bot_user: BotUser) -> None:
        self._mk_proxy(tenant, bot_user)
        result = cancel_booking(
            client=_adapter(FakeAylaBooking()),
            arguments={"record_id": _APPT},
            tenant=tenant,
            bot_user=bot_user,
        )
        assert result.pending is not None
        assert result.pending.kind == PendingBookingAction.Kind.CANCEL

    def test_cancel_preview_unknown_handle(self, tenant: Tenant, bot_user: BotUser) -> None:
        result = cancel_booking(
            client=_adapter(FakeAylaBooking()),
            arguments={"record_id": _APPT},
            tenant=tenant,
            bot_user=bot_user,
        )
        assert result.error == "invalid_record_id"

    def test_execute_cancel_flips_proxy_and_calls_rest(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        self._mk_proxy(tenant, bot_user)
        fake = FakeAylaBooking()
        result = execute_cancel(
            client=_adapter(fake),
            payload={"ayla_appointment_id": _APPT, "reason": ""},
            tenant=tenant,
            bot_user=bot_user,
        )
        assert not result.error
        assert fake.calls[0] == {"op": "cancel", "appointment_id": _APPT}
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=_APPT)
        assert proxy.status == RemoteBookingProxy.Status.CANCELLED


# ─── reschedule preview + execute (native) ─────────────────────────────────


class TestRescheduleAyla:
    def _mk_proxy(self, tenant: Tenant, bot_user: BotUser) -> RemoteBookingProxy:
        from django.utils import timezone

        return RemoteBookingProxy.all_tenants.create(
            appointment_id=_APPT,
            tenant=tenant,
            bot_user=bot_user,
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=1),
            status=RemoteBookingProxy.Status.CONFIRMED,
            source=RemoteBookingProxy.Source.AUTOMATION,
        )

    def test_reschedule_preview_validates_future(self, tenant: Tenant, bot_user: BotUser) -> None:
        self._mk_proxy(tenant, bot_user)
        result = reschedule_booking(
            client=_adapter(FakeAylaBooking()),
            arguments={"record_id": _APPT, "new_datetime": "2026-09-11T15:00:00"},
            tenant=tenant,
            bot_user=bot_user,
        )
        assert result.pending is not None
        assert result.pending.kind == PendingBookingAction.Kind.RESCHEDULE

    def test_reschedule_preview_rejects_past(self, tenant: Tenant, bot_user: BotUser) -> None:
        self._mk_proxy(tenant, bot_user)
        result = reschedule_booking(
            client=_adapter(FakeAylaBooking()),
            arguments={"record_id": _APPT, "new_datetime": "2020-01-01T10:00:00"},
            tenant=tenant,
            bot_user=bot_user,
        )
        assert result.error == "past_datetime"

    def test_execute_reschedule_is_native_same_uuid(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        self._mk_proxy(tenant, bot_user)
        fake = FakeAylaBooking()
        result = execute_reschedule(
            client=_adapter(fake),
            payload={
                "ayla_appointment_id": _APPT,
                "new_datetime": "2026-09-11T15:00:00",
                "master_name": "Ольга",
                "service_name": "Массаж",
            },
            tenant=tenant,
            bot_user=bot_user,
        )
        assert not result.error
        # Native — exactly one REST reschedule call, no cancel.
        assert [c["op"] for c in fake.calls] == ["reschedule"]
        assert fake.calls[0]["appointment_id"] == _APPT
        # Proxy stays the same UUID with the new window.
        assert RemoteBookingProxy.all_tenants.filter(appointment_id=_APPT).count() == 1
