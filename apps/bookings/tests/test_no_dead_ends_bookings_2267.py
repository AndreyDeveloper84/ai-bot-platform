"""DRF-2267 срез 5 — отказы записи и напоминаний о записи без тупиков.

Удачные шаги воронки получили кнопки ещё в DRF-1492 и в срезе 1; отказы —
нет. «Не нашла эту запись», «Эта запись уже обработана», «Эта запись не для
этого профиля» уходили голыми: человеку сообщили, что действие не вышло, и
не дали ни одного способа посмотреть, как обстоит дело на самом деле.

Рамки (решение главного окна 22.09):

* тексты существующих сообщений не меняются — добавляются только кнопки;
* ярлык берётся БУКВАЛЬНО тот, что уже живёт в боте: «📋 Мои записи»
  (``LABEL_MY_BOOKINGS``) и «Меню» (``LABEL_MENU``). Новых видимых слов
  этот срез не вводит;
* зова «Записаться» в напоминаниях нет — человек уже записан; выход из
  отказа это его собственные записи и меню.

Оба колбэка живут на двух поверхностях (салонный бот и глобальный через
``handoff.route_booking_callback``), поэтому кнопки — из семейства
``cb:menu:*``, которое переходит границу по построению (DRF-1492).
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.booking.models import BookingReminder, PendingBookingAction
from apps.bookings.callbacks import (
    REPLY_ALREADY_HANDLED,
    REPLY_BOOK_ALREADY_HANDLED,
    REPLY_FORBIDDEN,
    REPLY_NOT_FOUND,
    BookingGateCallbackSkill,
    BookingReminderCallbackSkill,
)
from apps.bookings.pending_actions import create_pending
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.skills.base import SkillContext
from apps.skills.menu.matching import CALLBACK_MENU_HELP, CALLBACK_MENU_MY_BOOKINGS
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

WAY_ON = [CALLBACK_MENU_MY_BOOKINGS, CALLBACK_MENU_HELP]


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="dead-ends-2267e", name="Dead Ends", manager_chat_id="mgr-1")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="de-2267e",
        chat_id="de-2267e",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    with tenant_scope(tenant):
        return Conversation.objects.create(
            tenant=tenant, bot_user=bot_user, state=Conversation.State.IDLE
        )


def _ctx(text: str, *, bot_user: BotUser, conversation: Conversation) -> SkillContext:
    return SkillContext(
        conversation=conversation, bot_user=bot_user, message_text=text, has_attachments=False
    )


def _callbacks(result: Any) -> list[str]:
    attachments = (result.action_data or {}).get("attachments") or []
    return [
        button["callback"]
        for att in attachments
        for button in (att.get("payload") or {}).get("buttons") or []
    ]


def _reminder(tenant: Tenant, bot_user: BotUser, *, status: str) -> BookingReminder:
    visit_at = timezone.now() + timedelta(days=1)
    return BookingReminder.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        yclients_record_id="555",
        chat_id=bot_user.chat_id,
        visit_at=visit_at,
        kind=BookingReminder.Kind.DAY_BEFORE,
        status=status,
        scheduled_at=visit_at - timedelta(hours=24),
        master_name="Ольга",
        service_name="Массаж",
    )


# ── напоминание о записи ─────────────────────────────────────────────────


class TestReminderRefusalsShowTheirOwnBookings:
    def test_a_second_tap_on_a_handled_reminder(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        reminder = _reminder(tenant, bot_user, status=BookingReminder.Status.CONFIRMED)

        result = BookingReminderCallbackSkill().handle(
            _ctx(f"cb:rem:confirm:{reminder.pk}", bot_user=bot_user, conversation=conversation)
        )

        assert result.reply_text == REPLY_ALREADY_HANDLED  # текст не меняем
        assert _callbacks(result) == WAY_ON

    def test_a_tap_on_a_reminder_that_is_gone(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        result = BookingReminderCallbackSkill().handle(
            _ctx(f"cb:rem:cancel:{uuid4()}", bot_user=bot_user, conversation=conversation)
        )

        assert result.reply_text == REPLY_NOT_FOUND
        assert _callbacks(result) == WAY_ON

    def test_a_tap_from_another_profile(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        stranger = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="de-2267e-2",
            chat_id="de-2267e-2",
            phone="79997654321",
            client_name="Boris",
        )
        reminder = _reminder(tenant, stranger, status=BookingReminder.Status.SENT_NO_REPLY)

        result = BookingReminderCallbackSkill().handle(
            _ctx(f"cb:rem:confirm:{reminder.pk}", bot_user=bot_user, conversation=conversation)
        )

        assert result.reply_text == REPLY_FORBIDDEN
        assert _callbacks(result) == WAY_ON


# ── ворота записи ────────────────────────────────────────────────────────


class TestBookingGateRefusalsShowTheBookings:
    def test_a_second_tap_on_a_used_token(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        token = create_pending(
            tenant=tenant,
            bot_user=bot_user,
            kind=PendingBookingAction.Kind.CANCEL,
            payload={"record_id": 777},
        )
        with tenant_scope(tenant):
            # Уже использован: тот же признак, что читает сам шлюз.
            PendingBookingAction.objects.filter(pk=token).update(consumed_at=timezone.now())

        with patch("apps.integrations.yclients.get_yclients_client"):
            result = BookingGateCallbackSkill().handle(
                _ctx(f"cb:book:cancel:{token}", bot_user=bot_user, conversation=conversation)
            )

        assert result.reply_text == REPLY_BOOK_ALREADY_HANDLED
        assert _callbacks(result) == WAY_ON

    def test_a_token_that_does_not_exist(
        self, tenant: Tenant, bot_user: BotUser, conversation: Conversation
    ) -> None:
        with patch("apps.integrations.yclients.get_yclients_client"):
            result = BookingGateCallbackSkill().handle(
                _ctx("cb:book:cancel:no-such-token", bot_user=bot_user, conversation=conversation)
            )

        assert result.reply_text == REPLY_NOT_FOUND
        assert _callbacks(result) == WAY_ON
