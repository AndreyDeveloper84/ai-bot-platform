"""Callback handlers для напоминаний (N2).

Payload:
- cb:rem:confirm:{reminder_id}    → status=CONFIRMED + краткое спасибо
- cb:rem:reschedule:{reminder_id} → AI Concierge с pseudo-text «хочу перенести»
- cb:rem:cancel:{reminder_id}     → BotInquiry + Telegram + status=CANCELLED

Регистрируется в get_routers() ПЕРЕД ai_callbacks/ai_assistant — иначе
generic message-handler перехватит callback'и.
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from django.utils import timezone
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback

from maxbot import keyboards
from maxbot.handlers.ai_assistant import _create_bot_inquiry, run_ai_turn
from maxbot.menu_state import send_with_main_menu
from maxbot.personalization import get_or_create_bot_user
from services_app.models import BookingReminder

logger = logging.getLogger("maxbot.reminders")
router = Router()


@sync_to_async
def _load_reminder(rid: str):
    return (
        BookingReminder.objects
        .select_related("bot_user")
        .filter(id=rid)
        .first()
    )


@sync_to_async
def _update_reminder_status(reminder, status: str) -> None:
    reminder.status = status
    reminder.replied_at = timezone.now()
    reminder.save(update_fields=["status", "replied_at"])


@router.message_callback(F.callback.payload.startswith(keyboards.PAYLOAD_REM_CONFIRM_PREFIX))
async def on_reminder_confirm(callback: MessageCallback, context: MemoryContext) -> None:
    """[✅ Подтверждаю] — клиент подтвердил приход."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    rid = (callback.callback.payload or "")[len(keyboards.PAYLOAD_REM_CONFIRM_PREFIX):]
    r = await _load_reminder(rid)
    if r is None:
        logger.warning("on_reminder_confirm: reminder %s not found", rid)
        return
    if r.status != BookingReminder.Status.SENT_NO_REPLY:
        # Idempotent: повторный клик после уже сделанного выбора — игнор.
        logger.info("on_reminder_confirm: reminder %s already in status=%s", rid, r.status)
        return

    await _update_reminder_status(r, BookingReminder.Status.CONFIRMED)
    when = r.visit_at.strftime("%d.%m в %H:%M")
    await callback.bot.send_message(
        chat_id=chat_id,
        text=f"Отлично! Ждём вас {when} 🌸 Хорошего дня!",
    )


@router.message_callback(F.callback.payload.startswith(keyboards.PAYLOAD_REM_RESCHEDULE_PREFIX))
async def on_reminder_reschedule(callback: MessageCallback, context: MemoryContext) -> None:
    """[🔄 Перенести] — триггерим AI Concierge для подбора другого времени."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    rid = (callback.callback.payload or "")[len(keyboards.PAYLOAD_REM_RESCHEDULE_PREFIX):]
    r = await _load_reminder(rid)
    if r is None:
        logger.warning("on_reminder_reschedule: reminder %s not found", rid)
        return
    if r.status != BookingReminder.Status.SENT_NO_REPLY:
        return

    await _update_reminder_status(r, BookingReminder.Status.RESCHEDULE_REQUESTED)

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(
        user.user_id, user.full_name, chat_id=chat_id,
    )
    await context.clear()

    when = r.visit_at.strftime("%d.%m в %H:%M")
    pseudo_text = (
        f"Хочу перенести запись на {r.master_name} ({r.service_name}, {when}) "
        f"на другое время. Подбери, пожалуйста, свободные слоты."
    )
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id, bot_user=bot_user,
        user_text=pseudo_text,
        original_user_text="cb:rem:reschedule",
    )


@router.message_callback(F.callback.payload.startswith(keyboards.PAYLOAD_REM_CANCEL_PREFIX))
async def on_reminder_cancel(callback: MessageCallback, context: MemoryContext) -> None:
    """[❌ Отменить] — BotInquiry + Telegram менеджеру (без авто-cancel YClients)."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    rid = (callback.callback.payload or "")[len(keyboards.PAYLOAD_REM_CANCEL_PREFIX):]
    r = await _load_reminder(rid)
    if r is None:
        logger.warning("on_reminder_cancel: reminder %s not found", rid)
        return
    if r.status != BookingReminder.Status.SENT_NO_REPLY:
        return

    await _update_reminder_status(r, BookingReminder.Status.CANCELLED)

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(
        user.user_id, user.full_name, chat_id=chat_id,
    )

    when = r.visit_at.strftime("%d.%m.%Y в %H:%M")
    await _create_bot_inquiry(
        user_id=user.user_id, full_name=user.full_name, chat_id=chat_id,
        question=(
            f"[Отмена записи] {r.master_name}, {r.service_name}, {when}. "
            f"yclients_record={r.yclients_record_id}"
        ),
    )
    await send_with_main_menu(
        bot=callback.bot, chat_id=chat_id,
        text=(
            "Передал менеджеру вашу просьбу отменить запись. "
            "С вами свяжутся в ближайшее время."
        ),
        bot_user=bot_user,
    )
