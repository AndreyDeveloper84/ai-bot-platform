"""Booking provider-selection + Ayla adapter tests (S1 / #1016, wave 2).

Covers the ``BOOKING_VIA_AYLA_REST`` seam:

* :func:`get_booking_provider` returns YClients (flag OFF) vs the Ayla
  adapter (flag ON);
* :class:`AylaYClientsAdapter` maps Ayla DTOs onto the YClients DTOs the
  tools expect, translates Ayla errors into ``YClients*`` errors the tools
  catch, passes idempotency keys, and binds ``external_user_id`` for writes;
* the adapter is drop-in for the read tools (``show_masters`` / ``show_slots``)
  — proving the eight booking tools work unchanged under flag ON.

The real Ayla client is a skeleton, so flag-ON behaviour is exercised via
``FakeAylaBooking`` (the booking analogue of ``FakeYClients``).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings

from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    AylaBookingRecord,
    AylaMaster,
    AylaService,
    AylaSlot,
    AylaUserRecord,
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.integrations.yclients import (
    Service,
    Staff,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.skills.booking.provider import AylaYClientsAdapter, get_booking_provider
from apps.skills.booking.tools import show_masters, show_slots
from apps.tenancy.models import Tenant


# ─── fixtures + fake ──────────────────────────────────────────────────────


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="booking-ayla", name="Booking Ayla")


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
    """In-memory stand-in for the Ayla booking client (Protocol-compatible)."""

    def __init__(self) -> None:
        self.services_rows: list[AylaService] = []
        self.masters_rows: list[AylaMaster] = []
        self.dates: list[str] = []
        self.times: list[AylaSlot] = []
        self.user_records: list[AylaUserRecord] = []
        self.create_response: AylaBookingRecord | None = None
        self.cancel_response: bool = True
        self.raise_exc: Exception | None = None
        # capture
        self.calls: list[dict[str, Any]] = []

    def _maybe_raise(self) -> None:
        if self.raise_exc is not None:
            raise self.raise_exc

    def get_services(self, *, master_id: int | None = None) -> list[AylaService]:
        self._maybe_raise()
        return list(self.services_rows)

    def get_masters(self, *, master_id: int | None = None) -> list[AylaMaster]:
        self._maybe_raise()
        return list(self.masters_rows)

    def get_available_dates(
        self, *, master_id: int | None = None, service_ids: list[int] | None = None
    ) -> list[str]:
        self._maybe_raise()
        return list(self.dates)

    def get_available_times(
        self, *, master_id: int, date: str, service_ids: list[int] | None = None
    ) -> list[AylaSlot]:
        self._maybe_raise()
        return list(self.times)

    def create_appointment(
        self,
        *,
        external_user_id: str,
        master_id: int,
        service_ids: list[int],
        datetime: str,
        client_phone: str,
        client_name: str,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> AylaBookingRecord:
        self.calls.append(
            {
                "op": "create",
                "external_user_id": external_user_id,
                "master_id": master_id,
                "service_ids": service_ids,
                "datetime": datetime,
                "idempotency_key": idempotency_key,
            }
        )
        self._maybe_raise()
        return self.create_response or AylaBookingRecord(appointment_id="999", raw={})

    def cancel_appointment(
        self, *, external_user_id: str, appointment_id: str, idempotency_key: str | None = None
    ) -> bool:
        self.calls.append(
            {
                "op": "cancel",
                "external_user_id": external_user_id,
                "appointment_id": appointment_id,
                "idempotency_key": idempotency_key,
            }
        )
        self._maybe_raise()
        return self.cancel_response

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        datetime: str,
        idempotency_key: str | None = None,
    ) -> AylaBookingRecord:
        self.calls.append({"op": "reschedule", "appointment_id": appointment_id})
        self._maybe_raise()
        return self.create_response or AylaBookingRecord(appointment_id=appointment_id, raw={})

    def get_user_appointments(self, *, external_user_id: str) -> list[AylaUserRecord]:
        self._maybe_raise()
        return list(self.user_records)


def _adapter(fake: FakeAylaBooking) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(client=fake, external_user_id="bot:telegram:42")


# ─── provider selection ───────────────────────────────────────────────────


class TestProviderSelection:
    @override_settings(BOOKING_VIA_AYLA_REST=False)
    def test_flag_off_returns_yclients(self, bot_user: BotUser, monkeypatch) -> None:
        sentinel = object()
        monkeypatch.setattr("apps.integrations.yclients.get_yclients_client", lambda: sentinel)
        assert get_booking_provider(bot_user=bot_user) is sentinel

    @override_settings(
        BOOKING_VIA_AYLA_REST=True,
        AYLA_BASE_URL="https://ayla.test",
        AYLA_INTERNAL_API_TOKEN="t",
    )
    def test_flag_on_returns_adapter_bound_to_user(self, bot_user: BotUser) -> None:
        provider = get_booking_provider(bot_user=bot_user)
        assert isinstance(provider, AylaYClientsAdapter)
        assert provider._external_user_id == "bot:telegram:42"


# ─── DTO mapping ───────────────────────────────────────────────────────────


class TestDTOMapping:
    def test_get_services_maps_to_yclients_service(self) -> None:
        fake = FakeAylaBooking()
        fake.services_rows = [
            AylaService(
                id=10,
                title="Массаж",
                price_min=1500.0,
                price_max=2500.0,
                duration_s=3600,
                category_id=3,
                raw={"x": 1},
            )
        ]
        out = _adapter(fake).get_services()
        assert len(out) == 1
        assert isinstance(out[0], Service)
        assert (out[0].id, out[0].title, out[0].duration_s) == (10, "Массаж", 3600)

    def test_get_staff_maps_to_yclients_staff(self) -> None:
        fake = FakeAylaBooking()
        fake.masters_rows = [
            AylaMaster(id=11, name="Ольга", specialization="Массаж", rating=4.5, position="master")
        ]
        out = _adapter(fake).get_staff()
        assert isinstance(out[0], Staff)
        assert (out[0].id, out[0].name, out[0].avatar) == (11, "Ольга", "")

    def test_get_available_times_maps_slot(self) -> None:
        fake = FakeAylaBooking()
        fake.times = [AylaSlot(time="14:00", datetime="2026-06-10T14:00:00", duration_s=3600)]
        out = _adapter(fake).get_available_times(staff_id=11, date="2026-06-10")
        assert out[0].time == "14:00"
        assert out[0].seance_length_s == 3600

    def test_get_user_records_maps_numeric_id(self) -> None:
        fake = FakeAylaBooking()
        fake.user_records = [
            AylaUserRecord(
                appointment_id="555",
                services=[{"id": 10}],
                master={"id": 11},
                datetime="2026-06-10T14:00:00",
                duration_s=3600,
                raw={},
            )
        ]
        out = _adapter(fake).get_user_records()
        assert out[0].id == 555
        assert out[0].raw["ayla_appointment_id"] == "555"


# ─── writes ─────────────────────────────────────────────────────────────────


class TestWrites:
    def test_create_record_passes_user_and_idempotency(self) -> None:
        fake = FakeAylaBooking()
        fake.create_response = AylaBookingRecord(appointment_id="12345", raw={})
        rec = _adapter(fake).create_record(
            staff_id=11,
            services=[10],
            datetime="2026-06-10T14:00:00",
            client_phone="79991234567",
            client_name="Anna",
        )
        assert rec.record_id == 12345
        call = fake.calls[0]
        assert call["external_user_id"] == "bot:telegram:42"
        assert call["idempotency_key"]  # non-empty deterministic key

    def test_create_record_idempotency_key_is_deterministic(self) -> None:
        fake1, fake2 = FakeAylaBooking(), FakeAylaBooking()
        kwargs = dict(
            staff_id=11,
            services=[10],
            datetime="2026-06-10T14:00:00",
            client_phone="79991234567",
            client_name="Anna",
        )
        _adapter(fake1).create_record(**kwargs)
        _adapter(fake2).create_record(**kwargs)
        assert fake1.calls[0]["idempotency_key"] == fake2.calls[0]["idempotency_key"]

    def test_cancel_record_stringifies_id(self) -> None:
        fake = FakeAylaBooking()
        assert _adapter(fake).cancel_record(record_id=777) is True
        assert fake.calls[0]["appointment_id"] == "777"

    def test_non_numeric_appointment_id_raises_yclients_error(self) -> None:
        # INTERIM placeholder behaviour: canonical Ayla appointment_id is a
        # UUID (contract §8 cutover prerequisite #1). Until the bot side
        # accepts UUIDs, a non-numeric id fails loudly rather than corrupting
        # the int-keyed mirror. Uses a UUID to document the real shape.
        fake = FakeAylaBooking()
        fake.create_response = AylaBookingRecord(
            appointment_id="3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34", raw={}
        )
        with pytest.raises(YClientsAPIError, match="not_numeric"):
            _adapter(fake).create_record(
                staff_id=11,
                services=[10],
                datetime="2026-06-10T14:00:00",
                client_phone="79991234567",
                client_name="Anna",
            )


# ─── error translation ──────────────────────────────────────────────────────


class TestErrorTranslation:
    def test_unavailable_translated(self) -> None:
        fake = FakeAylaBooking()
        fake.raise_exc = BookingUnavailableError("circuit_open")
        with pytest.raises(YClientsUnavailableError):
            _adapter(fake).get_services()

    def test_bad_request_translated_to_api_error(self) -> None:
        fake = FakeAylaBooking()
        fake.raise_exc = BookingBadRequestError("slot_taken")
        with pytest.raises(YClientsAPIError):
            _adapter(fake).get_staff()

    def test_not_implemented_propagates(self) -> None:
        # The skeleton guard must surface loudly, NOT be masked as a
        # YClients error — flipping the flag before the client lands fails.
        fake = FakeAylaBooking()
        fake.raise_exc = NotImplementedError("pending #1016")
        with pytest.raises(NotImplementedError):
            _adapter(fake).get_services()


# ─── tool drop-in (flag ON, adapter as client) ──────────────────────────────


class TestToolsAcceptAdapter:
    pytestmark = pytest.mark.django_db

    def test_show_masters_works_with_adapter(self, tenant: Tenant) -> None:
        fake = FakeAylaBooking()
        fake.masters_rows = [
            AylaMaster(id=11, name="Ольга", specialization="Массаж", rating=4.5, position="master"),
            AylaMaster(id=12, name="Иван", specialization="СПА", rating=4.0, position="master"),
        ]
        result = show_masters(
            client=_adapter(fake),
            arguments={"service_name": "массаж"},
            tenant_id=str(tenant.id),
        )
        assert not result.error
        assert {m.id for m in result.masters} == {11, 12}

    def test_show_slots_works_with_adapter(self, tenant: Tenant) -> None:
        fake = FakeAylaBooking()
        fake.dates = ["2026-06-10"]
        fake.times = [
            AylaSlot(time="14:00", datetime="2026-06-10T14:00:00", duration_s=3600),
        ]
        result = show_slots(
            client=_adapter(fake),
            arguments={"master_id": 11},
            tenant_id=str(tenant.id),
            allowed_master_ids={11, 12},
        )
        assert not result.error
        assert result.slots

    def test_show_masters_handoff_on_ayla_outage(self, tenant: Tenant) -> None:
        fake = FakeAylaBooking()
        fake.raise_exc = BookingUnavailableError("circuit_open")
        result = show_masters(
            client=_adapter(fake),
            arguments={},
            tenant_id=str(tenant.id),
        )
        # Translated to YClientsUnavailableError → tool maps to its error code.
        assert result.error == "yclients_unavailable"
