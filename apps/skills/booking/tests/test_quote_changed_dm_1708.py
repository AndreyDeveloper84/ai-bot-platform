"""DRF-1708, DM-половина — превью подтверждения показывает цену и
длительность ребра, и ровно они уезжают в создание; расхождение —
«было → стало» и новое подтверждение.

Решение владельца (пакет 2, D4): авторитетна та execution option,
которую клиент видел и подтвердил; расхождение → MATERIAL_CHANGE →
показать → новое подтверждение; не silent normalization.

Что заперто:

- ``confirm_booking``: котировка ребра → строки «Длительность»/«Цена» в
  превью и ``quoted_*`` в снимке pending; без ребра — ни строк, ни полей
  (положительная стража);
- ``execute_confirm``: ``quoted_*`` из снимка едут в ``create_appointment``
  как прислано; без них — прежний вызов без этих полей;
- ``409 QUOTE_CHANGED`` → ``error="quote_changed"``, текст с обеими
  суммами, «Запись не создана», НОВЫЙ pending с применяемым значением и
  клавиатурой подтверждения; поломкой не считается.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest

from apps.booking.models import PendingBookingAction
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord, BookingBadRequestError
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.tests.test_ayla_write_lifecycle import (
    _APPT,
    _SPEC,
    _SVC,
    FakeAyla,
    _appt_raw,
)
from apps.skills.booking.tools import confirm_booking, execute_confirm
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _flag_on(settings) -> None:
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-quote-dm", name="Quote DM")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-quote",
        chat_id="bu-quote",
        phone="79991234567",
        client_name="Anna",
        ayla_user_id=uuid.uuid4(),
    )


class QuotingFake(FakeAyla):
    """FakeAyla + ребро с ценой/длительностью + запись аргументов создания."""

    def __init__(self) -> None:
        super().__init__()
        self.edges: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.create_exc: Exception | None = None

    def get_specialist_service_edges(
        self, *, specialist_id: str, service_id: str
    ) -> list[dict[str, Any]]:
        return list(self.edges)

    def create_appointment(self, **kwargs: Any) -> AylaBookingRecord:
        self.create_calls.append(kwargs)
        if self.create_exc:
            raise self.create_exc
        return self.create_response or AylaBookingRecord(appointment_id=_APPT, raw={})


def _adapter(fake: FakeAyla) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(
        client=fake, external_user_id="bot:max:bu-quote", client_id="client-uuid"
    )


SLOT = "2026-11-01T16:00:00+03:00"


def _preview(fake: QuotingFake, tenant: Tenant, bot_user: BotUser):
    with tenant_scope(tenant):
        return confirm_booking(
            client=_adapter(fake),
            arguments={"master_id": _SPEC, "service_id": _SVC, "slot_datetime": SLOT},
            tenant=tenant,
            bot_user=bot_user,
            allowed_master_ids={_SPEC},
            allowed_service_ids={_SVC},
            master_lookup={_SPEC: "Ольга"},
            service_lookup={_SVC: "Массаж"},
        )


def _payload_of(result) -> dict[str, Any]:
    assert result.pending is not None
    return PendingBookingAction.all_tenants.get(pk=result.pending.token).payload


class TestPreviewQuotes:
    def test_edge_price_and_duration_are_shown_and_snapshotted(self, tenant, bot_user):
        fake = QuotingFake()
        fake.edges = [{"price": "1500.00", "duration_minutes": 60}]
        result = _preview(fake, tenant, bot_user)
        assert "• Длительность: 1 ч" in result.text
        assert "• Цена: 1 500 ₽" in result.text
        payload = _payload_of(result)
        assert payload["quoted_price"] == "1500.00"
        assert payload["quoted_duration_minutes"] == 60

    def test_without_edge_no_lines_and_no_fields(self, tenant, bot_user):
        """Положительная стража: превью и снимок прежние."""
        fake = QuotingFake()
        result = _preview(fake, tenant, bot_user)
        assert "• Услуга: Массаж" in result.text
        assert "Цена" not in result.text and "Длительность" not in result.text
        payload = _payload_of(result)
        assert "quoted_price" not in payload and "quoted_duration_minutes" not in payload


class TestExecutePassesQuote:
    def test_quoted_values_ride_to_create(self, tenant, bot_user):
        fake = QuotingFake()
        fake.create_response = AylaBookingRecord(
            appointment_id=_APPT, raw=_appt_raw(start=SLOT, end="2026-11-01T17:00:00+03:00")
        )
        with tenant_scope(tenant):
            result = execute_confirm(
                client=_adapter(fake),
                payload={
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "slot_datetime": SLOT,
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "quoted_price": "1500.00",
                    "quoted_duration_minutes": 60,
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.confirmation is not None and result.confirmation.ok
        sent = fake.create_calls[0]
        assert sent["quoted_price"] == "1500.00"
        assert sent["quoted_duration_minutes"] == 60

    def test_without_quote_nothing_extra_is_sent(self, tenant, bot_user):
        fake = QuotingFake()
        fake.create_response = AylaBookingRecord(
            appointment_id=_APPT, raw=_appt_raw(start=SLOT, end="2026-11-01T17:00:00+03:00")
        )
        with tenant_scope(tenant):
            execute_confirm(
                client=_adapter(fake),
                payload={
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "slot_datetime": SLOT,
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        sent = fake.create_calls[0]
        assert sent["service_id"] == _SVC
        assert set(sent) & {"quoted_price", "quoted_duration_minutes"} == set()


class TestQuoteChanged:
    def test_price_changed_is_named_and_reconfirmed(self, tenant, bot_user):
        fake = QuotingFake()
        fake.create_exc = BookingBadRequestError(
            "http_409_QUOTE_CHANGED",
            status_code=409,
            code="QUOTE_CHANGED",
            details={"field": "price", "quoted": "1500.00", "applied": "1700.00"},
        )
        with tenant_scope(tenant):
            result = execute_confirm(
                client=_adapter(fake),
                payload={
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "slot_datetime": SLOT,
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "quoted_price": "1500.00",
                    "quoted_duration_minutes": 60,
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "quote_changed"
        assert result.confirmation is not None and not result.confirmation.ok
        assert "цена изменилась: было 1 500 ₽, стало 1 700 ₽" in result.text
        assert "Запись не создана" in result.text
        # Новое превью — с применяемой ценой, новый pending с ней же.
        assert "• Цена: 1 700 ₽" in result.text
        new_payload = _payload_of(result)
        assert new_payload["quoted_price"] == "1700.00"
        assert new_payload["quoted_duration_minutes"] == 60
        assert result.pending is not None and result.pending.keyboard

    def test_duration_changed_keeps_its_own_words(self, tenant, bot_user):
        fake = QuotingFake()
        fake.create_exc = BookingBadRequestError(
            "http_409_QUOTE_CHANGED",
            status_code=409,
            code="QUOTE_CHANGED",
            details={"field": "duration_minutes", "quoted": 60, "applied": 45},
        )
        with tenant_scope(tenant):
            result = execute_confirm(
                client=_adapter(fake),
                payload={
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "slot_datetime": SLOT,
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "quoted_price": "1500.00",
                    "quoted_duration_minutes": 60,
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "quote_changed"
        assert "длительность изменилась: было 1 ч, стало 45 мин" in result.text
        assert _payload_of(result)["quoted_duration_minutes"] == 45
        assert _payload_of(result)["quoted_price"] == "1500.00"

    def test_other_409_is_not_quote_changed(self, tenant, bot_user):
        """Положительная стража: чужой 409 остаётся своим отказом."""
        fake = QuotingFake()
        fake.create_exc = BookingBadRequestError(
            "http_409_SUBSCRIPTION_PAST_DUE", status_code=409, code="SUBSCRIPTION_PAST_DUE"
        )
        with tenant_scope(tenant):
            result = execute_confirm(
                client=_adapter(fake),
                payload={
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "slot_datetime": SLOT,
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "quoted_price": "1500.00",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error != "quote_changed"
        assert result.confirmation is not None and result.confirmation.error == "unavailable"


class TestAdapterQuote:
    def test_quote_reads_price_and_duration_strictly(self):
        fake = QuotingFake()
        fake.edges = [{"price": "1700.50", "duration_minutes": 45}]
        assert _adapter(fake).get_specialist_service_quote(staff_id=_SPEC, service_id=_SVC) == (
            Decimal("1700.50"),
            45,
        )
        fake.edges = [{"price": "abc", "duration_minutes": "45"}]
        assert _adapter(fake).get_specialist_service_quote(staff_id=_SPEC, service_id=_SVC) == (
            None,
            None,
        )
        fake.edges = []
        assert _adapter(fake).get_specialist_service_quote(staff_id=_SPEC, service_id=_SVC) == (
            None,
            None,
        )
