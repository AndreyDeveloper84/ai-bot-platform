"""Отмена записи, сделанной в ДИАЛОГЕ бота, доходит до Ayla (DRF-2337, второй заход).

#2012 закрыл строки напоминаний, рождённые СОБЫТИЕМ Ayla (``ayla_appointment_id``
заполнен). Строки записи из диалога — основного пути записи продукта (DRF-1069)
— пишет ``reminders_factory.create_reminders_for_booking``, и под
``BOOKING_VIA_AYLA_REST`` UUID записи Ayla лежит у них в ``yclients_record_id``
строкой, а ``ayla_appointment_id`` пуст (``apps/booking/reminder_lookup.py``,
DRF-1144). ``_handle_cancel`` смотрел на одну колонку и отправлял такую строку в
ветку YClients: ``_try_yclients_cancel`` на не-цифровом id возвращал ``False``
без вызова, строка закрывалась, человек слышал «Запись отменена» — при живой
записи в Ayla. Узлов на такую строку не было ни одного.

Строки здесь пишет продуктовый писатель, а не прямой ``create``: предмет —
именно та форма строки, которую он кладёт.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder
from apps.bookings.callbacks import REPLY_CANCELLED, BookingReminderCallbackSkill
from apps.bookings.reminders_factory import create_reminders_for_booking
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.orchestrator.visits import _CANCEL_REFUSED_TEXT, _CANCEL_UNAVAILABLE_TEXT
from apps.skills.base import SkillContext
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_CANCEL = "apps.booking.services.records.cancel_booking"
_YC_CANCEL = "apps.bookings.callbacks._try_yclients_cancel"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="rem-2337-dlg", name="Salon 2337 dialog")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-2337-dlg",
        chat_id="chat-2337-dlg",
        phone="79991112244",
        client_name="Ольга",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


def _dialog_reminder(tenant, bot_user, handle: str) -> BookingReminder:
    """Строка так, как её кладёт запись из диалога, — затем «отправлена»."""
    rows = create_reminders_for_booking(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id=handle,
        visit_at=timezone.now() + timedelta(hours=26),
        master_name="Лера",
        service_name="Массаж",
    )
    assert rows, "писатель не создал ни одной строки — узлы ниже прошли бы на пустом"
    row = rows[0]
    # Определяющее свойство предмета: колонка Ayla пуста, id — в колонке YClients.
    assert row.ayla_appointment_id is None
    assert row.yclients_record_id == handle
    BookingReminder.all_tenants.filter(pk=row.pk).update(
        status=BookingReminder.Status.SENT_NO_REPLY
    )
    row.refresh_from_db()
    return row


def _tap(reminder, bot_user, conversation):
    return BookingReminderCallbackSkill().handle(
        SkillContext(
            bot_user=bot_user,
            conversation=conversation,
            message_text=f"cb:rem:cancel:{reminder.pk}",
            has_attachments=False,
        )
    )


class TestADialogBookingIsCancelledInAyla:
    @pytest.mark.parametrize(
        "spell",
        [str, lambda u: str(u).upper(), lambda u: u.hex],
        ids=["canonical", "upper", "hex"],
    )
    def test_the_rest_call_is_made_by_the_uuid_in_the_yclients_column(
        self, tenant, bot_user, conversation, spell
    ) -> None:
        appointment_id = uuid.uuid4()
        reminder = _dialog_reminder(tenant, bot_user, spell(appointment_id))

        with patch(_CANCEL, return_value="ok") as cancel, patch(_YC_CANCEL) as yc:
            result = _tap(reminder, bot_user, conversation)

        assert cancel.call_count == 1
        assert cancel.call_args.kwargs["appointment_id"] == str(appointment_id)
        assert yc.call_count == 0, "запись Ayla ушла в ветку YClients"
        assert result.reply_text == REPLY_CANCELLED
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.CANCELLED

    @pytest.mark.parametrize(
        ("status", "text"),
        [("backend_unavailable", _CANCEL_UNAVAILABLE_TEXT), ("refused", _CANCEL_REFUSED_TEXT)],
        ids=["unavailable", "refused"],
    )
    def test_no_success_is_said_when_ayla_did_not_cancel(
        self, tenant, bot_user, conversation, status, text
    ) -> None:
        reminder = _dialog_reminder(tenant, bot_user, str(uuid.uuid4()))

        with patch(_CANCEL, return_value=status):
            result = _tap(reminder, bot_user, conversation)

        assert result.reply_text != REPLY_CANCELLED
        assert result.reply_text == text
        assert result.claims_done is False
        reminder.refresh_from_db()
        assert reminder.status == BookingReminder.Status.SENT_NO_REPLY


class TestAnIntegerYclientsIdStaysAYclientsRow:
    """Граница разрешителя с другой стороны: целочисленный id — это YClients,
    в Ayla он не ходит, и сбой поставщика его локальную отмену не блокирует
    (узел #2012 на это остаётся как был)."""

    def test_an_integer_id_is_not_sent_to_ayla(self, tenant, bot_user, conversation) -> None:
        reminder = _dialog_reminder(tenant, bot_user, "123456")

        with patch(_CANCEL) as cancel, patch(_YC_CANCEL, return_value=False) as yc:
            result = _tap(reminder, bot_user, conversation)

        assert cancel.call_count == 0
        assert yc.call_count == 1
        assert result.reply_text == REPLY_CANCELLED
