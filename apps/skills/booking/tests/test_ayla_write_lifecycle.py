"""Flag-ON (``BOOKING_VIA_AYLA_REST``) booking write-lifecycle tests.

S1 / #1016 PR-2. Exercises the Ayla-routed write path end to end through the
real :class:`AylaYClientsAdapter` (wrapping an in-memory fake Ayla client),
asserting the UUID reground:

* :func:`execute_confirm` writes the billing ``BookingRequest`` with a
  ``yclients_record_id=<uuid>`` marker **and** mirrors the appointment onto
  :class:`RemoteBookingProxy` (source AUTOMATION, status CONFIRMED);
* :func:`execute_cancel` flips both the billing row and the mirror to
  CANCELLED, keyed by the UUID;
* :func:`execute_reschedule` uses the **native** Ayla move — same canonical
  appointment id, mirror start/end updated;
* :func:`show_my_bookings` lists from the mirror (names from the billing row),
  dropping cancelled / past appointments.

Flag-OFF (YClients int) behaviour is covered byte-for-byte by the existing
``test_tools*`` suites; this file only adds the flag-ON path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord, AylaSlot
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.tools import (
    execute_cancel,
    execute_confirm,
    execute_reschedule,
    show_my_bookings,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _flag_on(settings):
    """Route every test in this module through the Ayla REST path."""
    settings.BOOKING_VIA_AYLA_REST = True


_APPT = "3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34"
_SVC = "1a2b3c4d-0000-0000-0000-000000000010"
_SPEC = "7c9e0000-0000-0000-0000-000000000011"
_NEW_DT = "2026-07-01T16:00:00+03:00"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="ayla-life", name="Ayla Lifecycle")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-life",
        chat_id="bu-life",
        phone="79991234567",
        client_name="Anna",
    )


class FakeAyla:
    """In-memory Ayla booking client (the methods the adapter calls)."""

    def __init__(self) -> None:
        self.create_response: AylaBookingRecord | None = None
        self.reschedule_response: AylaBookingRecord | None = None
        self.times: list[AylaSlot] = []
        self.cancel_calls: list[str] = []
        self.reschedule_calls: list[dict[str, Any]] = []

    # ── unused-by-these-tests Protocol surface (kept so the fake satisfies
    #    AylaBookingClient for the typed adapter constructor) ──────────────
    def get_services(self, *, specialist_id: str | None = None) -> list[Any]:
        return []

    def get_masters(self, *, specialist_id: str | None = None) -> list[Any]:
        return []

    def get_available_dates(
        self, *, specialist_id: str, service_id: str | None = None, window_days: int = 14
    ) -> list[str]:
        return []

    def get_user_appointments(self, *, external_user_id: str) -> list[Any]:
        return []

    def get_available_times(
        self, *, specialist_id: str, date: str, service_id: str | None = None
    ) -> list[AylaSlot]:
        return list(self.times)

    def create_appointment(self, **kwargs: Any) -> AylaBookingRecord:
        return self.create_response or AylaBookingRecord(appointment_id=_APPT, raw={})

    def cancel_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        idempotency_key: str | None = None,
        specialist_id: str | None = None,
        service_id: str | None = None,
        date: str | None = None,
    ) -> bool:
        self.cancel_calls.append(appointment_id)
        return True

    def reschedule_appointment(
        self,
        *,
        external_user_id: str,
        appointment_id: str,
        new_start_datetime: str,
        expected_version: int | None = None,
        idempotency_key: str | None = None,
        specialist_id: str | None = None,
        service_id: str | None = None,
        old_date: str | None = None,
    ) -> AylaBookingRecord:
        self.reschedule_calls.append(
            {
                "appointment_id": appointment_id,
                "new_start_datetime": new_start_datetime,
                "expected_version": expected_version,
                "specialist_id": specialist_id,
                "service_id": service_id,
                "old_date": old_date,
            }
        )
        return self.reschedule_response or AylaBookingRecord(appointment_id=appointment_id, raw={})


def _adapter(fake: FakeAyla) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(
        client=fake, external_user_id="bot:max:bu-life", client_id="client-uuid"
    )


def _appt_raw(*, start: str, end: str) -> dict[str, Any]:
    return {
        "id": _APPT,
        "start_datetime": start,
        "end_datetime": end,
        "service": {"id": _SVC},
        "specialist": {"id": _SPEC},
        "status": "confirmed",
    }


def _seed_proxy(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    start_at: Any,
    status: str = RemoteBookingProxy.Status.CONFIRMED,
    appointment_id: str = _APPT,
) -> RemoteBookingProxy:
    return RemoteBookingProxy.all_tenants.create(
        appointment_id=uuid.UUID(appointment_id),
        tenant=tenant,
        bot_user=bot_user,
        start_at=start_at,
        end_at=start_at + timedelta(hours=1),
        status=status,
        source=RemoteBookingProxy.Source.AUTOMATION,
        service_id=uuid.UUID(_SVC),
        specialist_id=uuid.UUID(_SPEC),
    )


def _seed_billing_row(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    appointment_id: str = _APPT,
    status: str = BookingRequest.Status.CONFIRMED,
) -> BookingRequest:
    return BookingRequest.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        service_name="Массаж",
        master_name="Ольга",
        client_name="Anna",
        client_phone="79991234567",
        comment=f"Bot booking | yclients_record_id={appointment_id}",
        source="bot",
        status=status,
    )


# ─── confirm ──────────────────────────────────────────────────────────────


class TestConfirmFlagOn:
    def test_writes_billing_row_and_mirror(self, tenant: Tenant, bot_user: BotUser) -> None:
        fake = FakeAyla()
        fake.create_response = AylaBookingRecord(
            appointment_id=_APPT,
            raw=_appt_raw(start="2026-07-01T16:00:00+03:00", end="2026-07-01T17:00:00+03:00"),
        )
        with tenant_scope(tenant):
            result = execute_confirm(
                client=_adapter(fake),
                payload={
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "slot_datetime": "2026-07-01T16:00:00+03:00",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.confirmation is not None and result.confirmation.ok
        # Confirmation surfaces the canonical UUID, not a 0/int.
        assert result.confirmation.record_id == _APPT

        # Billing row written with the UUID marker + ai_direct billable.
        billing = BookingRequest.all_tenants.get(comment__contains=f"yclients_record_id={_APPT}")
        assert billing.status == BookingRequest.Status.CONFIRMED
        assert billing.booking_source == "ai_direct"
        assert billing.billable is True

        # Mirror written: CONFIRMED, AUTOMATION, typed UUID columns.
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=uuid.UUID(_APPT))
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
        assert proxy.source == RemoteBookingProxy.Source.AUTOMATION
        assert str(proxy.service_id) == _SVC
        assert str(proxy.specialist_id) == _SPEC
        assert proxy.end_at > proxy.start_at  # duration carried from the response

    def test_proxy_upsert_scopes_lookup_by_tenant(self, tenant: Tenant, bot_user: BotUser) -> None:
        # S5-LOW: the mirror upsert must scope its lookup by (appointment_id,
        # tenant), not appointment_id alone, so a cross-tenant id collision
        # surfaces (IntegrityError) instead of silently overwriting another
        # tenant's row. Assert tenant is a LOOKUP kwarg, not in defaults.
        from unittest.mock import patch

        fake = FakeAyla()
        fake.create_response = AylaBookingRecord(
            appointment_id=_APPT,
            raw=_appt_raw(start="2026-07-01T16:00:00+03:00", end="2026-07-01T17:00:00+03:00"),
        )
        with patch("apps.booking.models.RemoteBookingProxy.all_tenants") as mgr:
            mgr.update_or_create.return_value = (object(), True)
            with tenant_scope(tenant):
                execute_confirm(
                    client=_adapter(fake),
                    payload={
                        "master_id": _SPEC,
                        "service_id": _SVC,
                        "slot_datetime": "2026-07-01T16:00:00+03:00",
                        "client_phone": "79991234567",
                        "client_name": "Anna",
                        "master_name": "Ольга",
                        "service_name": "Массаж",
                    },
                    tenant=tenant,
                    bot_user=bot_user,
                )
        assert mgr.update_or_create.called
        kwargs = mgr.update_or_create.call_args.kwargs
        assert kwargs.get("tenant") is tenant  # tenant is part of the lookup
        assert "appointment_id" in kwargs
        assert "tenant" not in kwargs["defaults"]  # moved out of defaults

    def test_missing_appointment_id_is_api_error(self, tenant: Tenant, bot_user: BotUser) -> None:
        fake = FakeAyla()
        fake.create_response = AylaBookingRecord(appointment_id="", raw={})
        with tenant_scope(tenant):
            result = execute_confirm(
                client=_adapter(fake),
                payload={
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "slot_datetime": "2026-07-01T16:00:00+03:00",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == "yclients_api_error"
        # No marker-less row left behind.
        assert not BookingRequest.all_tenants.filter(bot_user=bot_user).exists()
        assert not RemoteBookingProxy.all_tenants.filter(bot_user=bot_user).exists()


# ─── cancel ─────────────────────────────────────────────────────────────────


class TestCancelFlagOn:
    def test_flips_billing_row_and_mirror(self, tenant: Tenant, bot_user: BotUser) -> None:
        now = dj_timezone.now()
        _seed_billing_row(tenant, bot_user)
        _seed_proxy(tenant, bot_user, start_at=now + timedelta(days=1))
        fake = FakeAyla()
        with tenant_scope(tenant):
            result = execute_cancel(
                client=_adapter(fake),
                payload={"record_id": _APPT, "reason": "заболела"},
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.error == ""
        assert fake.cancel_calls == [_APPT]  # UUID passed through, no int()
        billing = BookingRequest.all_tenants.get(comment__contains=f"yclients_record_id={_APPT}")
        assert billing.status == BookingRequest.Status.CANCELLED
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=uuid.UUID(_APPT))
        assert proxy.status == RemoteBookingProxy.Status.CANCELLED


# ─── reschedule (native) ─────────────────────────────────────────────────────


class TestRescheduleFlagOn:
    def test_native_move_preserves_id_and_updates_mirror(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        now = dj_timezone.now()
        _seed_billing_row(tenant, bot_user)
        _seed_proxy(tenant, bot_user, start_at=now + timedelta(days=1))
        fake = FakeAyla()
        fake.times = [AylaSlot(time="16:00", datetime=_NEW_DT, duration_s=3600)]
        fake.reschedule_response = AylaBookingRecord(
            appointment_id=_APPT,
            raw=_appt_raw(start=_NEW_DT, end="2026-07-01T17:00:00+03:00"),
        )
        with tenant_scope(tenant):
            result = execute_reschedule(
                client=_adapter(fake),
                payload={
                    "record_id": _APPT,
                    "new_datetime": _NEW_DT,
                    "master_id": _SPEC,
                    "service_id": _SVC,
                    "master_name": "Ольга",
                    "service_name": "Массаж",
                    "client_phone": "79991234567",
                    "client_name": "Anna",
                },
                tenant=tenant,
                bot_user=bot_user,
            )
        assert result.confirmation is not None and result.confirmation.ok
        # Same canonical id (native move, not cancel+create).
        assert result.confirmation.record_id == _APPT
        old_date = (now + timedelta(days=1)).date().isoformat()
        assert fake.reschedule_calls == [
            {
                "appointment_id": _APPT,
                "new_start_datetime": _NEW_DT,
                "expected_version": None,
                "specialist_id": _SPEC,
                "service_id": _SVC,
                "old_date": old_date,
            }
        ]
        # Billing row stays CONFIRMED (not RESCHEDULED) — id unchanged.
        billing = BookingRequest.all_tenants.get(comment__contains=f"yclients_record_id={_APPT}")
        assert billing.status == BookingRequest.Status.CONFIRMED
        # Mirror moved to the new window.
        proxy = RemoteBookingProxy.all_tenants.get(appointment_id=uuid.UUID(_APPT))
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
        assert proxy.start_at == datetime.fromisoformat(_NEW_DT)  # same instant (UTC-stored)


# ─── show_my_bookings ────────────────────────────────────────────────────────


class TestShowMyBookingsFlagOn:
    def test_lists_from_mirror_with_billing_names(self, tenant: Tenant, bot_user: BotUser) -> None:
        now = dj_timezone.now()
        _seed_billing_row(tenant, bot_user)
        _seed_proxy(tenant, bot_user, start_at=now + timedelta(days=2))
        with tenant_scope(tenant):
            result = show_my_bookings(client=_adapter(FakeAyla()), tenant=tenant, bot_user=bot_user)
        assert len(result.bookings) == 1
        row = result.bookings[0]
        assert row.record_id == _APPT
        assert row.service_name == "Массаж" and row.master_name == "Ольга"
        assert row.visit_at  # mirror start_at surfaced

    def test_excludes_cancelled_and_past(self, tenant: Tenant, bot_user: BotUser) -> None:
        now = dj_timezone.now()
        # Cancelled mirror → excluded.
        _seed_billing_row(tenant, bot_user)
        _seed_proxy(
            tenant,
            bot_user,
            start_at=now + timedelta(days=1),
            status=RemoteBookingProxy.Status.CANCELLED,
        )
        # Past (confirmed) mirror under a second appointment → excluded.
        past_appt = "11111111-2222-3333-4444-555555555555"
        _seed_billing_row(tenant, bot_user, appointment_id=past_appt)
        _seed_proxy(
            tenant,
            bot_user,
            start_at=now - timedelta(days=1),
            appointment_id=past_appt,
        )
        with tenant_scope(tenant):
            result = show_my_bookings(client=_adapter(FakeAyla()), tenant=tenant, bot_user=bot_user)
        assert result.bookings == []

    @pytest.mark.parametrize("terminal_status", ["completed", "no_show"])
    def test_excludes_other_terminal_mirror_statuses(
        self, tenant: Tenant, bot_user: BotUser, terminal_status: str
    ) -> None:
        """DRF-1034: the rule was «hide cancelled», so a mirror row that said
        ``completed`` or ``no_show`` still showed up as an upcoming booking.

        Both are Ayla wire values the mirror legitimately holds, and neither is
        a visit the client is still expecting.
        """
        now = dj_timezone.now()
        _seed_billing_row(tenant, bot_user)
        _seed_proxy(
            tenant,
            bot_user,
            start_at=now + timedelta(days=1),
            status=terminal_status,
        )
        with tenant_scope(tenant):
            result = show_my_bookings(client=_adapter(FakeAyla()), tenant=tenant, bot_user=bot_user)
        assert result.bookings == []

    def test_excludes_booking_with_no_mirror_row(self, tenant: Tenant, bot_user: BotUser) -> None:
        """DRF-1034: a local billing row with no mirror behind it is not evidence.

        It is written at confirm time and no event ever moves it, so it stays
        CONFIRMED forever. Previously such a row was listed with a blank visit
        time — a booking the bot could say nothing true about.
        """
        _seed_billing_row(tenant, bot_user)
        with tenant_scope(tenant):
            result = show_my_bookings(client=_adapter(FakeAyla()), tenant=tenant, bot_user=bot_user)
        assert result.bookings == []

    def test_includes_awaiting_payment_mirror_status(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """``awaiting_payment`` is Ayla's wire value and is not in
        ``RemoteBookingProxy.Status.choices`` — it must stay visible."""
        now = dj_timezone.now()
        _seed_billing_row(tenant, bot_user)
        _seed_proxy(
            tenant,
            bot_user,
            start_at=now + timedelta(days=2),
            status="awaiting_payment",
        )
        with tenant_scope(tenant):
            result = show_my_bookings(client=_adapter(FakeAyla()), tenant=tenant, bot_user=bot_user)
        assert len(result.bookings) == 1
