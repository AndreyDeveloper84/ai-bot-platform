"""Booking-skill tool handler tests (DRF-839 / Phase 1 / B3).

Drives the four handlers directly with a stub YClients client. The
skill-level orchestration (LLM provider mocking, tool dispatch) lives
in :mod:`apps.skills.booking.tests.test_skill`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingRequest
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    Service,
    Staff,
    UserRecord,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.skills.booking.tools import (
    BOOKING_TOOL_SPECS,
    build_master_lookup,
    build_service_lookup,
    confirm_booking,
    show_masters,
    show_my_bookings,
    show_slots,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures + fake client
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="booking-tools", name="Booking Tools")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-1",
        chat_id="bu-1",
        phone="79991234567",
        client_name="Anna",
    )


class FakeYClients:
    """In-memory stand-in for the B1 ``YClientsAPI``.

    Each public method's behaviour is driven by attributes set by the
    test. Default behaviour is "empty list / minimal record".
    """

    def __init__(self) -> None:
        self.staff_rows: list[Staff] = []
        self.services_rows: list[Service] = []
        self.dates: list[str] = []
        self.times: list[AvailableTime] = []
        self.create_record_response: BookingRecord | None = None
        self.create_record_exc: Exception | None = None
        self.staff_exc: Exception | None = None
        self.get_user_records_rows: list[UserRecord] = []
        # Captures for assertions
        self.create_calls: list[dict[str, Any]] = []

    def get_staff(self, *, staff_id: int | None = None) -> list[Staff]:
        if self.staff_exc is not None:
            raise self.staff_exc
        return list(self.staff_rows)

    def get_services(
        self,
        *,
        staff_id: int | None = None,
        category_id: int | None = None,
    ) -> list[Service]:
        return list(self.services_rows)

    def get_available_dates(
        self,
        *,
        staff_id: int | None = None,
        service_ids: list[int] | None = None,
    ) -> list[str]:
        return list(self.dates)

    def get_available_times(
        self,
        *,
        staff_id: int,
        date: str,
        service_ids: list[int] | None = None,
    ) -> list[AvailableTime]:
        return list(self.times)

    def create_record(
        self,
        *,
        staff_id: int,
        services: list[int],
        datetime: str,
        client_phone: str,
        client_name: str,
        client_email: str = "",
        comment: str | None = None,
        notify_by_sms: int = 0,
        notify_by_email: int = 0,
    ) -> BookingRecord:
        self.create_calls.append(
            {
                "staff_id": staff_id,
                "services": services,
                "datetime": datetime,
                "client_phone": client_phone,
                "client_name": client_name,
            }
        )
        if self.create_record_exc is not None:
            raise self.create_record_exc
        if self.create_record_response is None:
            return BookingRecord(record_id=12345, record_hash="h", raw={})
        return self.create_record_response

    def get_user_records(self) -> list[UserRecord]:
        return list(self.get_user_records_rows)


def _staff(id_: int, name: str = "Olga", spec: str = "Массаж") -> Staff:
    return Staff(
        id=id_,
        name=name,
        specialization=spec,
        rating=4.5,
        avatar="",
        position="master",
        raw={},
    )


def _service(id_: int, title: str = "Массаж спины") -> Service:
    return Service(
        id=id_,
        title=title,
        price_min=1500.0,
        price_max=2500.0,
        duration_s=3600,
        category_id=None,
        raw={},
    )


# ---------------------------------------------------------------------------
# Tool spec sanity
# ---------------------------------------------------------------------------


class TestToolSpecs:
    def test_all_eight_specs_registered(self) -> None:
        # B7 / DRF-843 — added buy_certificate as the 8th tool.
        names = {spec["name"] for spec in BOOKING_TOOL_SPECS}
        assert names == {
            "show_masters",
            "show_slots",
            "confirm_booking",
            "cancel_booking",
            "reschedule_booking",
            "show_my_bookings",
            "calc_price",
            "buy_certificate",
        }

    def test_specs_have_openai_shape(self) -> None:
        for spec in BOOKING_TOOL_SPECS:
            assert "name" in spec
            assert "description" in spec
            assert "parameters" in spec
            assert spec["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# show_masters
# ---------------------------------------------------------------------------


class TestShowMasters:
    def test_returns_staff_list(self, tenant: Tenant) -> None:
        client = FakeYClients()
        client.staff_rows = [_staff(11, "Ольга"), _staff(12, "Иван", spec="СПА")]
        result = show_masters(
            client=client, arguments={"service_name": "массаж"}, tenant_id=str(tenant.id)
        )
        assert not result.error
        assert len(result.masters) == 2
        ids = {m.id for m in result.masters}
        assert ids == {11, 12}

    def test_empty_when_no_staff(self, tenant: Tenant) -> None:
        client = FakeYClients()
        result = show_masters(client=client, arguments={}, tenant_id=str(tenant.id))
        assert result.masters == []
        assert not result.error

    def test_yclients_unavailable_sets_error(self, tenant: Tenant) -> None:
        client = FakeYClients()
        client.staff_exc = YClientsUnavailableError("circuit_open")
        result = show_masters(client=client, arguments={}, tenant_id=str(tenant.id))
        assert result.error == "yclients_unavailable"

    def test_yclients_api_error_sets_error(self, tenant: Tenant) -> None:
        client = FakeYClients()
        client.staff_exc = YClientsAPIError("http_400")
        result = show_masters(client=client, arguments={}, tenant_id=str(tenant.id))
        assert result.error == "yclients_api_error"

    def test_scoring_promotes_specialization_match(self, tenant: Tenant) -> None:
        client = FakeYClients()
        client.staff_rows = [
            _staff(11, "Ольга", spec="Маникюр"),
            _staff(12, "Иван", spec="Массаж спины"),
        ]
        result = show_masters(
            client=client, arguments={"service_name": "массаж"}, tenant_id=str(tenant.id)
        )
        # Higher-scoring "Иван" should land first.
        assert result.masters[0].id == 12


# ---------------------------------------------------------------------------
# show_slots
# ---------------------------------------------------------------------------


class TestShowSlots:
    def test_returns_slots(self, tenant: Tenant) -> None:
        client = FakeYClients()
        client.dates = ["2026-05-20"]
        client.times = [
            AvailableTime(time="14:00", datetime="2026-05-20T14:00:00", seance_length_s=3600),
            AvailableTime(time="15:30", datetime="2026-05-20T15:30:00", seance_length_s=3600),
        ]
        result = show_slots(
            client=client,
            arguments={"master_id": 11},
            tenant_id=str(tenant.id),
            allowed_master_ids={11, 12},
        )
        assert not result.error
        assert len(result.slots) == 2
        assert result.slots[0].duration_minutes == 60

    def test_rejects_hallucinated_master(self, tenant: Tenant) -> None:
        client = FakeYClients()
        result = show_slots(
            client=client,
            arguments={"master_id": 999},
            tenant_id=str(tenant.id),
            allowed_master_ids={11, 12},
        )
        assert result.error == "invalid_master_id"

    def test_no_dates_returns_friendly_text(self, tenant: Tenant) -> None:
        client = FakeYClients()
        result = show_slots(
            client=client,
            arguments={"master_id": 11},
            tenant_id=str(tenant.id),
            allowed_master_ids={11},
        )
        assert "Нет свободных" in result.text
        assert result.slots == []

    def test_date_from_filters_forward(self, tenant: Tenant) -> None:
        client = FakeYClients()
        client.dates = ["2026-05-15", "2026-05-20", "2026-05-25"]
        client.times = [
            AvailableTime(time="14:00", datetime="2026-05-20T14:00:00", seance_length_s=3600)
        ]
        result = show_slots(
            client=client,
            arguments={"master_id": 11, "date_from": "2026-05-18"},
            tenant_id=str(tenant.id),
            allowed_master_ids={11},
        )
        assert "2026-05-20" in result.text

    def test_yclients_unavailable(self, tenant: Tenant) -> None:
        client = FakeYClients()

        def boom(**_: Any) -> list[str]:
            raise YClientsUnavailableError("x")

        client.get_available_dates = boom  # type: ignore[method-assign]
        result = show_slots(
            client=client,
            arguments={"master_id": 11},
            tenant_id=str(tenant.id),
            allowed_master_ids={11},
        )
        assert result.error == "yclients_unavailable"


# ---------------------------------------------------------------------------
# confirm_booking
# ---------------------------------------------------------------------------


class TestConfirmBooking:
    """B5 / DRF-841: ``confirm_booking`` is now preview-only.

    The actual YClients create + BookingRequest row write moved to
    :func:`apps.skills.booking.tools.execute_confirm`; covered in
    ``test_confirm_gate.py``. These tests only assert preview-time
    behaviour: validation gates, pending row persistence, no
    YClients side-effects.
    """

    def test_preview_persists_pending_action(self, tenant: Tenant, bot_user: BotUser) -> None:
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        with tenant_scope(tenant):
            result = confirm_booking(
                client=client,
                arguments={
                    "master_id": 11,
                    "service_id": 22,
                    "slot_datetime": "2026-05-20T14:00:00",
                },
                tenant=tenant,
                bot_user=bot_user,
                allowed_master_ids={11},
                allowed_service_ids={22},
                master_lookup={11: "Ольга"},
                service_lookup={22: "Массаж спины"},
            )
        # Preview path: no record created, no BookingRequest written.
        assert client.create_calls == []
        assert BookingRequest.all_tenants.filter(tenant=tenant, bot_user=bot_user).count() == 0
        # ``pending`` populated with a UUID token + 2 buttons.
        assert result.pending is not None
        assert result.pending.kind == PendingBookingAction.Kind.CONFIRM
        assert len(result.pending.keyboard) == 2
        pending_row = PendingBookingAction.all_tenants.get(pk=result.pending.token)
        assert pending_row.bot_user_id == bot_user.id
        assert pending_row.payload["slot_datetime"] == "2026-05-20T14:00:00"

    def test_rejects_invalid_master_id(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeYClients()
        with tenant_scope(tenant):
            result = confirm_booking(
                client=client,
                arguments={
                    "master_id": 999,
                    "service_id": 22,
                    "slot_datetime": "2026-05-20T14:00:00",
                },
                tenant=tenant,
                bot_user=bot_user,
                allowed_master_ids={11},
                allowed_service_ids={22},
                master_lookup={},
                service_lookup={22: "x"},
            )
        assert result.error == "invalid_master_id"
        assert client.create_calls == []

    def test_rejects_invalid_service_id(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeYClients()
        with tenant_scope(tenant):
            result = confirm_booking(
                client=client,
                arguments={
                    "master_id": 11,
                    "service_id": 9999,
                    "slot_datetime": "2026-05-20T14:00:00",
                },
                tenant=tenant,
                bot_user=bot_user,
                allowed_master_ids={11},
                allowed_service_ids={22},
                master_lookup={11: "Ольга"},
                service_lookup={22: "x"},
            )
        assert result.error == "invalid_service_id"

    def test_missing_slot_returns_error(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeYClients()
        with tenant_scope(tenant):
            result = confirm_booking(
                client=client,
                arguments={
                    "master_id": 11,
                    "service_id": 22,
                    "slot_datetime": "",
                },
                tenant=tenant,
                bot_user=bot_user,
                allowed_master_ids={11},
                allowed_service_ids={22},
                master_lookup={11: "Ольга"},
                service_lookup={22: "Массаж"},
            )
        assert result.error == "missing_slot"


# ---------------------------------------------------------------------------
# show_my_bookings
# ---------------------------------------------------------------------------


class TestShowMyBookings:
    def test_returns_local_rows(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeYClients()
        # Future visit comes back from YClients.
        future = (dj_timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
        client.get_user_records_rows = [
            UserRecord(
                id=42,
                services=[{"id": 22, "title": "Массаж"}],
                company={},
                staff={"name": "Ольга"},
                date=future,
                datetime=future,
                seance_length=3600,
                raw={},
            )
        ]
        with tenant_scope(tenant):
            BookingRequest.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                service_name="Массаж спины",
                master_name="Ольга",
                client_name="Anna",
                client_phone="79991234567",
                comment="Bot booking | yclients_record_id=42",
                source="bot",
            )
            result = show_my_bookings(client=client, tenant=tenant, bot_user=bot_user)
        assert len(result.bookings) == 1
        assert result.bookings[0].record_id == 42

    def test_empty_returns_friendly_text(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeYClients()
        result = show_my_bookings(client=client, tenant=tenant, bot_user=bot_user)
        assert "пока нет" in result.text
        assert result.bookings == []

    def test_yclients_unreachable_falls_back_silently(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        client = FakeYClients()

        def boom() -> list[UserRecord]:
            raise YClientsUnavailableError("x")

        client.get_user_records = boom  # type: ignore[method-assign]
        with tenant_scope(tenant):
            BookingRequest.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                service_name="Массаж",
                master_name="Ольга",
                client_name="Anna",
                client_phone="79991234567",
                comment="Bot booking | yclients_record_id=42",
                source="bot",
            )
            result = show_my_bookings(client=client, tenant=tenant, bot_user=bot_user)
        # No exception raised — empty live records degrades cleanly.
        assert len(result.bookings) == 1

    def test_past_bookings_filtered_out(self, tenant: Tenant, bot_user: BotUser) -> None:
        client = FakeYClients()
        past = (dj_timezone.now() - timedelta(days=2)).replace(microsecond=0).isoformat()
        client.get_user_records_rows = [
            UserRecord(
                id=99,
                services=[],
                company={},
                staff={"name": "Ольга"},
                date=past,
                datetime=past,
                seance_length=3600,
                raw={},
            )
        ]
        with tenant_scope(tenant):
            BookingRequest.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                service_name="Массаж",
                master_name="Ольга",
                client_name="Anna",
                client_phone="79991234567",
                comment="Bot booking | yclients_record_id=99",
                source="bot",
            )
            result = show_my_bookings(client=client, tenant=tenant, bot_user=bot_user)
        assert result.bookings == []


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


class TestLookups:
    def test_build_master_lookup(self) -> None:
        rows = [_staff(11, "Ольга"), _staff(12, "Иван")]
        lookup = build_master_lookup(rows)
        assert lookup == {11: "Ольга", 12: "Иван"}

    def test_build_service_lookup(self) -> None:
        rows = [_service(22, "Массаж"), _service(33, "Маникюр")]
        lookup = build_service_lookup(rows)
        assert lookup == {22: "Массаж", 33: "Маникюр"}
