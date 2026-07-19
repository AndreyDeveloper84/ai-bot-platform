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

    def get_services(self, *, specialist_id: str | None = None) -> list[AylaService]:
        self._maybe_raise()
        return list(self.services_rows)

    def get_masters(self, *, specialist_id: str | None = None) -> list[AylaMaster]:
        self._maybe_raise()
        return list(self.masters_rows)

    def get_available_dates(
        self,
        *,
        specialist_id: str,
        service_id: str | None = None,
        window_days: int = 14,
    ) -> list[str]:
        self._maybe_raise()
        return list(self.dates)

    def get_available_times(
        self, *, specialist_id: str, date: str, service_id: str | None = None
    ) -> list[AylaSlot]:
        self._maybe_raise()
        return list(self.times)

    def create_appointment(
        self,
        *,
        external_user_id: str,
        client_id: str,
        specialist_id: str,
        service_id: str,
        start_datetime: str,
        idempotency_key: str | None = None,
        payment_required: bool = True,
    ) -> AylaBookingRecord:
        self.calls.append(
            {
                "op": "create",
                "external_user_id": external_user_id,
                "client_id": client_id,
                "specialist_id": specialist_id,
                "service_id": service_id,
                "start_datetime": start_datetime,
                "idempotency_key": idempotency_key,
                "payment_required": payment_required,
            }
        )
        self._maybe_raise()
        return self.create_response or AylaBookingRecord(appointment_id="appt-999", raw={})

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
        new_start_datetime: str,
        idempotency_key: str | None = None,
    ) -> AylaBookingRecord:
        self.calls.append(
            {
                "op": "reschedule",
                "appointment_id": appointment_id,
                "new_start_datetime": new_start_datetime,
                "idempotency_key": idempotency_key,
            }
        )
        self._maybe_raise()
        return self.create_response or AylaBookingRecord(appointment_id=appointment_id, raw={})

    def get_user_appointments(self, *, external_user_id: str) -> list[AylaUserRecord]:
        self._maybe_raise()
        return list(self.user_records)


def _adapter(fake: FakeAylaBooking, *, client_id: str = "client-uuid") -> AylaYClientsAdapter:
    return AylaYClientsAdapter(client=fake, external_user_id="bot:telegram:42", client_id=client_id)


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


_SVC_UUID = "1a2b3c4d-0000-0000-0000-000000000010"
_SPEC_UUID = "7c9e0000-0000-0000-0000-000000000011"
_APPT_UUID = "3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34"


class TestDTOMapping:
    def test_get_services_maps_to_yclients_service(self) -> None:
        fake = FakeAylaBooking()
        fake.services_rows = [
            AylaService(
                id=_SVC_UUID,
                title="Массаж",
                price_min=1500.0,
                price_max=2500.0,
                duration_s=3600,
                category_id="cat-uuid",
                raw={"x": 1},
            )
        ]
        out = _adapter(fake).get_services()
        assert len(out) == 1
        assert isinstance(out[0], Service)
        assert (out[0].id, out[0].title, out[0].duration_s) == (_SVC_UUID, "Массаж", 3600)

    def test_get_staff_maps_to_yclients_staff(self) -> None:
        fake = FakeAylaBooking()
        fake.masters_rows = [
            AylaMaster(
                id=_SPEC_UUID, name="Ольга", specialization="Массаж", rating=4.5, position="master"
            )
        ]
        out = _adapter(fake).get_staff()
        assert isinstance(out[0], Staff)
        assert (out[0].id, out[0].name, out[0].avatar) == (_SPEC_UUID, "Ольга", "")

    def test_get_available_times_maps_slot(self) -> None:
        fake = FakeAylaBooking()
        fake.times = [AylaSlot(time="14:00", datetime="2026-06-10T14:00:00", duration_s=3600)]
        out = _adapter(fake).get_available_times(staff_id=_SPEC_UUID, date="2026-06-10")
        assert out[0].time == "14:00"
        assert out[0].seance_length_s == 3600

    def test_get_user_records_carries_uuid_handle(self) -> None:
        fake = FakeAylaBooking()
        fake.user_records = [
            AylaUserRecord(
                appointment_id=_APPT_UUID,
                services=[{"id": _SVC_UUID}],
                master={"id": _SPEC_UUID},
                datetime="2026-06-10T14:00:00",
                duration_s=3600,
                raw={},
            )
        ]
        out = _adapter(fake).get_user_records()
        # int handle is retired on the Ayla path; the UUID rides in raw and the
        # tools resolve via RemoteBookingProxy.
        assert out[0].id == 0
        assert out[0].raw["ayla_appointment_id"] == _APPT_UUID


# ─── writes ─────────────────────────────────────────────────────────────────


class TestWrites:
    def test_create_record_passes_uuids_user_and_idempotency(self) -> None:
        fake = FakeAylaBooking()
        fake.create_response = AylaBookingRecord(
            appointment_id=_APPT_UUID,
            raw={
                "id": _APPT_UUID,
                "start_datetime": "2026-06-10T14:00:00+03:00",
                "end_datetime": "2026-06-10T15:00:00+03:00",
                "service": {"id": _SVC_UUID},
                "specialist": {"id": _SPEC_UUID},
                "status": "confirmed",
            },
        )
        rec = _adapter(fake).create_record(
            staff_id=_SPEC_UUID,
            services=[_SVC_UUID],
            datetime="2026-06-10T14:00:00+03:00",
            client_phone="79991234567",
            client_name="Anna",
        )
        # No int handle; canonical UUID + normalised mirror keys ride in raw.
        assert rec.record_id == 0
        assert rec.raw["ayla_appointment_id"] == _APPT_UUID
        assert rec.raw["start_at"] == "2026-06-10T14:00:00+03:00"
        assert rec.raw["service_id"] == _SVC_UUID
        assert rec.raw["specialist_id"] == _SPEC_UUID
        call = fake.calls[0]
        assert call["external_user_id"] == "bot:telegram:42"
        assert call["client_id"] == "client-uuid"
        assert (call["specialist_id"], call["service_id"]) == (_SPEC_UUID, _SVC_UUID)
        assert call["idempotency_key"]  # non-empty deterministic key

    def test_create_record_idempotency_key_is_deterministic(self) -> None:
        fake1, fake2 = FakeAylaBooking(), FakeAylaBooking()
        kwargs: dict[str, Any] = dict(
            staff_id=_SPEC_UUID,
            services=[_SVC_UUID],
            datetime="2026-06-10T14:00:00",
            client_phone="79991234567",
            client_name="Anna",
        )
        _adapter(fake1).create_record(**kwargs)
        _adapter(fake2).create_record(**kwargs)
        assert fake1.calls[0]["idempotency_key"] == fake2.calls[0]["idempotency_key"]

    def test_create_record_payment_required_passed_through(self) -> None:
        """AMD-002: the adapter forwards payment_required verbatim and folds
        it into the idempotency seed — same intent dedups, flipped intent
        (online pay → no prepay) mints a NEW key."""
        fake = FakeAylaBooking()
        base: dict[str, Any] = dict(
            staff_id=_SPEC_UUID,
            services=[_SVC_UUID],
            datetime="2026-06-10T14:00:00",
            client_phone="79991234567",
            client_name="Anna",
        )
        _adapter(fake).create_record(**base, payment_required=False)
        _adapter(fake).create_record(**base)  # default True
        call_false, call_true = fake.calls
        assert call_false["payment_required"] is False
        assert call_true["payment_required"] is True
        assert call_false["idempotency_key"] != call_true["idempotency_key"]

    def test_create_record_without_client_id_raises(self) -> None:
        # BotUser not linked to Ayla → no client_id → create fails loudly
        # rather than 403-ing server-side.
        fake = FakeAylaBooking()
        with pytest.raises(YClientsAPIError, match="ayla_client_id_missing"):
            _adapter(fake, client_id="").create_record(
                staff_id=_SPEC_UUID,
                services=[_SVC_UUID],
                datetime="2026-06-10T14:00:00",
                client_phone="79991234567",
                client_name="Anna",
            )

    def test_cancel_record_stringifies_uuid_id(self) -> None:
        fake = FakeAylaBooking()
        assert _adapter(fake).cancel_record(record_id=_APPT_UUID) is True
        assert fake.calls[0]["appointment_id"] == _APPT_UUID

    def test_reschedule_record_native_preserves_uuid(self) -> None:
        fake = FakeAylaBooking()
        fake.create_response = AylaBookingRecord(
            appointment_id=_APPT_UUID,
            raw={"id": _APPT_UUID, "start_datetime": "2026-06-11T16:00:00+03:00"},
        )
        rec = _adapter(fake).reschedule_record(
            record_id=_APPT_UUID, datetime="2026-06-11T16:00:00+03:00"
        )
        assert rec.raw["ayla_appointment_id"] == _APPT_UUID
        call = fake.calls[0]
        assert call["op"] == "reschedule"
        assert call["appointment_id"] == _APPT_UUID
        assert call["new_start_datetime"] == "2026-06-11T16:00:00+03:00"


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

    def test_c1_debt_block_translated_to_specialist_unavailable(self) -> None:
        """C1: Ayla 409 SUBSCRIPTION_PAST_DUE → neutral specialist_unavailable."""
        from apps.skills.booking.provider import YClientsSpecialistUnavailableError

        fake = FakeAylaBooking()
        fake.raise_exc = BookingBadRequestError(
            "http_409_subscription_past_due",
            status_code=409,
            code="SUBSCRIPTION_PAST_DUE",
        )
        with pytest.raises(YClientsSpecialistUnavailableError):
            _adapter(fake).create_record(
                staff_id="s1",
                services=["svc1"],
                datetime="2026-08-01T10:00:00+03:00",
                client_phone="79991234567",
                client_name="Anna",
            )

    def test_other_409_stays_generic_api_error(self) -> None:
        """A 409 that is NOT the C1 debt block (e.g. slot conflict) must
        keep the generic mapping — the neutral slug is C1-only."""
        fake = FakeAylaBooking()
        fake.raise_exc = BookingBadRequestError(
            "http_409_slot_taken", status_code=409, code="SLOT_TAKEN"
        )
        with pytest.raises(YClientsAPIError):
            _adapter(fake).create_record(
                staff_id="s1",
                services=["svc1"],
                datetime="2026-08-01T10:00:00+03:00",
                client_phone="79991234567",
                client_name="Anna",
            )

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
            AylaMaster(
                id="11", name="Ольга", specialization="Массаж", rating=4.5, position="master"
            ),
            AylaMaster(id="12", name="Иван", specialization="СПА", rating=4.0, position="master"),
        ]
        result = show_masters(
            client=_adapter(fake),
            arguments={"service_name": "массаж"},
            tenant_id=str(tenant.id),
        )
        assert not result.error
        assert {m.id for m in result.masters} == {"11", "12"}

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
