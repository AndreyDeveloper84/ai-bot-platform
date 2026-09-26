"""DRF-2547 — бот пишет в зеркало статус КАНОНА, а не константу CONFIRMED.

При предоплате канон создаёт визит ``awaiting_payment``. ``execute_confirm``
писал в зеркало ``confirmed`` константой, пришедший следом ``booking.created``
видел «уже confirmed» и ничего не менял — зеркало врало навсегда, а
напоминание, поставленное тут же без проверки статуса, звало клиента на
неоплаченный визит.

Узлы идут через продуктовый путь ``execute_confirm`` (флаг Ayla ON, настоящий
адаптер поверх подделки клиента), а не через вызов функции записи:

* канон вернул ``awaiting_payment`` → в зеркале ``pending_payment`` (словарь
  зеркала, та же нормализация, что у ``booking.created``);
* на такой визит напоминание НЕ ставится;
* положительная пара: канон вернул ``confirmed`` → зеркало ``confirmed`` и
  напоминания есть — иначе «напоминаний нет» проходило бы и у бота, который
  не ставит их никогда;
* статуса в ответе нет → новую строку зеркала бот не заводит (её заведёт
  событие), существующей не меняет статус; напоминание не ставится.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord
from apps.integrations.ayla.booking_client import AylaSlot
from apps.skills.booking.tests.test_ayla_write_lifecycle import (
    _APPT,
    _NEW_DT,
    _SPEC,
    _SVC,
    FakeAyla,
    _adapter,
    _appt_raw,
    _seed_billing_row,
    _seed_proxy,
)
from apps.skills.booking.tools import execute_confirm, execute_reschedule
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _flag_on(settings):
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="prepay-2547", name="Prepay salon")


@pytest.fixture
def client_user(salon: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=salon,
        channel="max",
        channel_user_id="bu-2547",
        chat_id="bu-2547",
        phone="79991234567",
        client_name="Anna",
    )


def _slot() -> str:
    """Визит в будущем — чтобы напоминание, если его поставят, было настоящим."""
    visit = timezone.now().astimezone(timezone.get_fixed_timezone(180)) + timedelta(days=3)
    return visit.replace(hour=16, minute=0, second=0, microsecond=0).isoformat()


def _confirm(salon: Tenant, user: BotUser, raw: dict[str, Any]) -> Any:
    fake = FakeAyla()
    fake.create_response = AylaBookingRecord(appointment_id=_APPT, raw=raw)
    with tenant_scope(salon):
        return execute_confirm(
            client=_adapter(fake),
            payload={
                "master_id": _SPEC,
                "service_id": _SVC,
                "slot_datetime": raw["start_datetime"],
                "client_phone": "79991234567",
                "client_name": "Anna",
                "master_name": "Ольга",
                "service_name": "Массаж",
            },
            tenant=salon,
            bot_user=user,
        )


def _canon(status: str | None) -> dict[str, Any]:
    start = _slot()
    end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat()
    raw = _appt_raw(start=start, end=end)
    if status is None:
        raw.pop("status")
    else:
        raw["status"] = status
    return raw


def _reminders(salon: Tenant) -> int:
    return BookingReminder.all_tenants.filter(tenant=salon).count()


def _mirror() -> RemoteBookingProxy | None:
    return RemoteBookingProxy.all_tenants.filter(appointment_id=uuid.UUID(_APPT)).first()


class TestConfirmWritesCanonStatus:
    def test_awaiting_payment_reaches_the_mirror(self, salon: Tenant, client_user: BotUser) -> None:
        result = _confirm(salon, client_user, _canon("awaiting_payment"))

        assert result.confirmation is not None and result.confirmation.ok
        proxy = _mirror()
        assert proxy is not None
        assert proxy.status == RemoteBookingProxy.Status.PENDING_PAYMENT

    def test_no_reminder_for_an_unpaid_visit(self, salon: Tenant, client_user: BotUser) -> None:
        _confirm(salon, client_user, _canon("awaiting_payment"))
        # Наличие впереди: визит записан и зеркало есть — нуль ниже про
        # напоминания, а не про несостоявшуюся бронь.
        assert _mirror() is not None
        assert _reminders(salon) == 0

    def test_confirmed_still_mirrors_confirmed_and_reminds(
        self, salon: Tenant, client_user: BotUser
    ) -> None:
        _confirm(salon, client_user, _canon("confirmed"))

        proxy = _mirror()
        assert proxy is not None
        assert proxy.status == RemoteBookingProxy.Status.CONFIRMED
        assert _reminders(salon) > 0


class TestUnknownCanonStatus:
    def test_missing_status_creates_no_mirror_and_no_reminder(
        self, salon: Tenant, client_user: BotUser
    ) -> None:
        result = _confirm(salon, client_user, _canon(None))

        assert result.confirmation is not None and result.confirmation.ok
        assert _mirror() is None  # заведёт booking.created с настоящим статусом
        assert _reminders(salon) == 0

    def test_missing_status_does_not_touch_existing_status(
        self, salon: Tenant, client_user: BotUser
    ) -> None:
        start = timezone.now() + timedelta(days=3)
        RemoteBookingProxy.all_tenants.create(
            appointment_id=uuid.UUID(_APPT),
            tenant=salon,
            bot_user=client_user,
            start_at=start,
            end_at=start + timedelta(hours=1),
            status=RemoteBookingProxy.Status.PENDING_PAYMENT,
        )

        _confirm(salon, client_user, _canon(None))

        proxy = _mirror()
        assert proxy is not None
        assert proxy.status == RemoteBookingProxy.Status.PENDING_PAYMENT


class TestNativeRescheduleWritesCanonStatus:
    """Второй вызов той же записи зеркала — нативный перенос (``tools.py``)."""

    def test_reschedule_keeps_awaiting_payment(self, salon: Tenant, client_user: BotUser) -> None:
        _seed_billing_row(salon, client_user)
        seeded = _seed_proxy(
            salon,
            client_user,
            start_at=timezone.now() + timedelta(days=1),
            status=RemoteBookingProxy.Status.PENDING_PAYMENT,
        )
        # Наличие впереди: до переноса зеркало честно говорило «ждёт оплаты».
        assert seeded.status == RemoteBookingProxy.Status.PENDING_PAYMENT
        fake = FakeAyla()
        fake.times = [AylaSlot(time="16:00", datetime=_NEW_DT, duration_s=3600)]
        raw = _appt_raw(start=_NEW_DT, end="2026-07-01T17:00:00+03:00")
        raw["status"] = "awaiting_payment"  # канон переносит неоплаченный визит
        fake.reschedule_response = AylaBookingRecord(appointment_id=_APPT, raw=raw)

        with tenant_scope(salon):
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
                tenant=salon,
                bot_user=client_user,
            )

        assert result.confirmation is not None and result.confirmation.ok
        proxy = _mirror()
        assert proxy is not None
        assert proxy.status == RemoteBookingProxy.Status.PENDING_PAYMENT
