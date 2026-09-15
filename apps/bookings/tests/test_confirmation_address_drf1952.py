"""DRF-1952 — адрес салона в подтверждении записи в чате и в напоминаниях T-24/T-2.

Адрес — тенанта записи: у подтверждения — тенант, в котором исполняется
pending; у напоминания — FK ``BookingReminder.tenant``. Пусто — фраза
``visit-address.ts``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, PendingBookingAction
from apps.bookings.callbacks import BookingGateCallbackSkill
from apps.bookings.pending_actions import create_pending
from apps.bookings.tasks import _format_day_before_text, _format_two_hours_text
from apps.bookings.tests.test_dead_ends_drf1492 import (
    _confirm_payload,
    _ctx,
    _FakeYClients,
    _future_iso,
    _patched,
)
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _tenant(address: str | None) -> Tenant:
    return Tenant.objects.create(
        slug=f"addr-{_suffix()}", name="Салон", manager_chat_id="mgr-1", address=address
    )


def _user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"cf-{_suffix()}",
        chat_id="cf-1",
        phone="79991234567",
        client_name="Anna",
    )


def _confirm_reply(tenant: Tenant) -> str:
    bot_user = _user(tenant)
    with tenant_scope(tenant):
        conversation = Conversation.objects.create(
            tenant=tenant, bot_user=bot_user, state=Conversation.State.IDLE
        )
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CONFIRM,
        payload=_confirm_payload(_future_iso()),
    )
    client = _FakeYClients()
    with _patched(client):
        result = BookingGateCallbackSkill().handle(
            _ctx(f"cb:book:confirm:{token}", bot_user=bot_user, conversation=conversation)
        )
    assert client.create_calls  # запись действительно создана
    assert "Готово! Записала." in result.reply_text
    return result.reply_text


def test_confirmation_names_the_salon_address() -> None:
    assert "Адрес: ул. Карпинского, 33А" in _confirm_reply(_tenant("ул. Карпинского, 33А"))


def test_confirmation_without_an_address_says_to_ask_the_salon() -> None:
    assert "Адрес: Уточните адрес в салоне" in _confirm_reply(_tenant(None))


def _reminder(tenant: Tenant, kind: str) -> BookingReminder:
    bot_user = _user(tenant)
    visit_at = timezone.now() + timedelta(days=1)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id="555",
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=kind,
        status=BookingReminder.Status.PENDING,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Ольга",
        service_name="Массаж",
    )


def test_day_before_reminder_names_the_salon_address() -> None:
    text = _format_day_before_text(
        _reminder(_tenant("ул. Карпинского, 33А"), BookingReminder.Kind.DAY_BEFORE)
    )
    assert "Массаж к мастеру Ольга" in text
    assert "Адрес: ул. Карпинского, 33А" in text


def test_day_before_reminder_without_an_address_says_to_ask_the_salon() -> None:
    text = _format_day_before_text(_reminder(_tenant(None), BookingReminder.Kind.DAY_BEFORE))
    assert "Адрес: Уточните адрес в салоне" in text


def test_two_hours_reminder_names_the_salon_address() -> None:
    text = _format_two_hours_text(
        _reminder(_tenant("ул. Карпинского, 33А"), BookingReminder.Kind.TWO_HOURS)
    )
    assert "Массаж к мастеру Ольга" in text
    assert "Адрес: ул. Карпинского, 33А" in text


def test_two_hours_reminder_without_an_address_says_to_ask_the_salon() -> None:
    text = _format_two_hours_text(_reminder(_tenant(None), BookingReminder.Kind.TWO_HOURS))
    assert "Адрес: Уточните адрес в салоне" in text
