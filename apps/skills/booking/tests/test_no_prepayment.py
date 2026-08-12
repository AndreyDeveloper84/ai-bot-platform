"""DRF-1007 — ``payment_required`` resolution on bot-created bookings.

Controlled Pilot runs WITHOUT prepayment (owner decision 2026-08-12):
for allowlisted tenants the bot must send ``payment_required=False`` so
the backend creates the appointment directly CONFIRMED — reminders only
fire for CONFIRMED bookings. Everyone else keeps the historical
``True``. An explicit payload value always wins over the setting.

Exercises the real :class:`AylaYClientsAdapter` over the in-memory
``FakeAyla`` harness from ``test_ayla_write_lifecycle`` and asserts the
value that lands on the wire.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord
from apps.skills.booking.tests.test_ayla_write_lifecycle import (
    FakeAyla,
    _adapter,
    _appt_raw,
)
from apps.skills.booking.tools import _resolve_payment_required, execute_confirm
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_OTHER_TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _flag_on(settings):
    """Route through the Ayla REST path (the pilot configuration)."""
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="no-pre-pay", name="No Prepay")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-noprepay",
        chat_id="bu-noprepay",
        phone="79991234567",
        client_name="Anna",
    )


class _RecordingFakeAyla(FakeAyla):
    """FakeAyla that captures create_appointment kwargs."""

    def __init__(self) -> None:
        super().__init__()
        self.create_kwargs: list[dict[str, Any]] = []

    def create_appointment(self, **kwargs: Any) -> AylaBookingRecord:
        self.create_kwargs.append(kwargs)
        return super().create_appointment(**kwargs)


def _confirm(
    tenant: Tenant, bot_user: BotUser, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Run execute_confirm and return the kwargs Ayla received."""
    fake = _RecordingFakeAyla()
    fake.create_response = AylaBookingRecord(
        appointment_id="3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34",
        raw=_appt_raw(start="2026-07-01T16:00:00+03:00", end="2026-07-01T17:00:00+03:00"),
    )
    base_payload: dict[str, Any] = {
        "master_id": "7c9e0000-0000-0000-0000-000000000011",
        "service_id": "1a2b3c4d-0000-0000-0000-000000000010",
        "slot_datetime": "2026-07-01T16:00:00+03:00",
        "client_phone": "79991234567",
        "client_name": "Anna",
        "master_name": "Ольга",
        "service_name": "Массаж",
    }
    if payload:
        base_payload.update(payload)
    with tenant_scope(tenant):
        result = execute_confirm(
            client=_adapter(fake),
            payload=base_payload,
            tenant=tenant,
            bot_user=bot_user,
        )
    assert result.confirmation is not None and result.confirmation.ok
    assert len(fake.create_kwargs) == 1
    return fake.create_kwargs[0]


class TestPaymentRequiredOnConfirm:
    def test_allowlisted_tenant_books_without_prepayment(
        self, settings, tenant: Tenant, bot_user: BotUser
    ) -> None:
        settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset({str(tenant.id)})
        sent = _confirm(tenant, bot_user)
        assert sent["payment_required"] is False

    def test_tenant_outside_allowlist_keeps_prepayment(
        self, settings, tenant: Tenant, bot_user: BotUser
    ) -> None:
        settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset({_OTHER_TENANT})
        sent = _confirm(tenant, bot_user)
        assert sent["payment_required"] is True

    def test_empty_allowlist_keeps_prepayment(
        self, settings, tenant: Tenant, bot_user: BotUser
    ) -> None:
        settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset()
        sent = _confirm(tenant, bot_user)
        assert sent["payment_required"] is True

    def test_explicit_payload_true_overrides_allowlist(
        self, settings, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """A deliberate caller choice beats the deployment default."""
        settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset({str(tenant.id)})
        sent = _confirm(tenant, bot_user, {"payment_required": True})
        assert sent["payment_required"] is True

    def test_explicit_payload_false_honoured_outside_allowlist(
        self, settings, tenant: Tenant, bot_user: BotUser
    ) -> None:
        settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset()
        sent = _confirm(tenant, bot_user, {"payment_required": False})
        assert sent["payment_required"] is False


class TestPaymentRequiredHelper:
    def test_malformed_setting_fails_closed(self, settings, tenant: Tenant) -> None:
        """A malformed value injected past settings load keeps the
        historical default instead of silently dropping prepayment."""
        settings.BOOKING_NO_PREPAYMENT_TENANTS = "not-a-uuid"
        assert _resolve_payment_required(tenant, {}) is True

    def test_empty_allowlist_defaults_true(self, settings, tenant: Tenant) -> None:
        settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset()
        assert _resolve_payment_required(tenant, {}) is True
