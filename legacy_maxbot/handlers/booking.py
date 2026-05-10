"""Handler FSM-заявки: имя → телефон → подтверждение → BookingRequest.

Состояния (states.py):
- awaiting_name    — ждём ФИО
- awaiting_phone   — ждём телефон
- awaiting_confirm — ждём «Да/Нет» из confirm_booking_keyboard

При confirm:yes → создаётся BookingRequest(source='bot_max', bot_user=..)
+ Telegram/email уведомление менеджеру + +1 к bot_user.context['bookings_count']
+ возврат в главное меню.

При confirm:no → state очищается, главное меню (поведение из ответа Q6 владельца).
"""
from __future__ import annotations

import logging
import re

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.core.exceptions import ValidationError
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback, MessageCreated

from maxbot import keyboards, texts
from maxbot.menu_state import send_with_main_menu
from maxbot.personalization import get_or_create_bot_user, greet_text
from maxbot.states import BookingStates
from services_app.models import BookingRequest, BotUser, Service
from website.utils import normalize_ru_phone


logger = logging.getLogger("maxbot.booking")
router = Router()

# Валидация имени: 2-100 символов, буквы (рус/лат) + пробел + тире.
# Цифры и спецсимволы запрещены (anti-spam, договорено в Q5 владельца).
_NAME_RE = re.compile(r"^[А-Яа-яЁёA-Za-z\s\-]+$")
NAME_MIN = 2
NAME_MAX = 100

# Idempotency TTL — окно в которое второй клик «✅ Да» считается дублем.
# 60 секунд достаточно для двойного клика + retry проксей; следующая
# осознанная попытка через минуту бьёт код заново (новая заявка).
BOOKING_IDEMPOTENCY_TTL = 60


# ─── Awaiting name ──────────────────────────────────────────────────────────


@router.message_created(BookingStates.awaiting_name)
async def on_name_input(event: MessageCreated, context: MemoryContext) -> None:
    """Принимаем имя клиента, валидируем, переходим в awaiting_phone."""
    chat_id = event.message.recipient.chat_id
    raw = (event.message.body.text or "").strip() if event.message.body else ""

    if len(raw) < NAME_MIN or len(raw) > NAME_MAX:
        await event.bot.send_message(chat_id=chat_id, text=texts.BOOKING_NAME_TOO_SHORT)
        return
    if not _NAME_RE.match(raw):
        await event.bot.send_message(chat_id=chat_id, text=texts.BOOKING_NAME_INVALID_CHARS)
        return

    await context.update_data(name=raw)
    await context.set_state(BookingStates.awaiting_phone)
    await event.bot.send_message(chat_id=chat_id, text=texts.BOOKING_ASK_PHONE)


# ─── Awaiting phone ─────────────────────────────────────────────────────────


@router.message_created(BookingStates.awaiting_phone)
async def on_phone_input(event: MessageCreated, context: MemoryContext) -> None:
    """Нормализуем телефон через website.utils.normalize_ru_phone."""
    chat_id = event.message.recipient.chat_id
    raw = (event.message.body.text or "").strip() if event.message.body else ""

    try:
        phone = normalize_ru_phone(raw)
    except ValidationError:
        await event.bot.send_message(chat_id=chat_id, text=texts.BOOKING_PHONE_INVALID)
        return

    await context.update_data(phone=phone)
    await context.set_state(BookingStates.awaiting_confirm)

    data = await context.get_data()
    service_name = await _service_name(data.get("service_id"))
    confirm_text = texts.BOOKING_CONFIRM.format(
        name=data["name"], phone=phone, service=service_name,
    )
    await event.bot.send_message(
        chat_id=chat_id,
        text=confirm_text,
        attachments=[keyboards.confirm_booking_keyboard()],
    )


# ─── Confirm Yes/No ─────────────────────────────────────────────────────────


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_CONFIRM_YES)
async def on_confirm_yes(callback: MessageCallback, context: MemoryContext) -> None:
    """Создаём BookingRequest, шлём уведомления, возвращаем в меню.

    Защита от двойного клика (известный prod bug 2026-04-25):
    - Idempotency через django.core.cache (Redis на проде) с ключом
      maxbot:confirm:{user_id}:{service_id}, TTL 60s. Повторный клик в окне
      → возвращает кэшированный booking_id, НЕ создаёт второй BookingRequest.
    - Immediate feedback («Принимаю заявку...») перед долгими операциями
      (Telegram POST через прокси может занять 5-10 секунд).
    """
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    data = await context.get_data()
    if not all(k in data for k in ("service_id", "name", "phone")):
        logger.warning("on_confirm_yes: неполные данные FSM: %r", data)
        await context.clear()
        return

    user = callback.callback.user

    # Idempotency check ДО любых side-effects
    idem_key = f"maxbot:confirm:{user.user_id}:{data['service_id']}"
    cached_id = await sync_to_async(cache.get)(idem_key)
    if cached_id is not None:
        logger.info("on_confirm_yes: idempotent hit user=%s service=%s booking=%s",
                    user.user_id, data["service_id"], cached_id)
        bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)
        await send_with_main_menu(
            bot=callback.bot, chat_id=chat_id,
            text=texts.BOOKING_DONE.format(request_id=cached_id),
            bot_user=bot_user,
        )
        await context.clear()
        return

    # Сразу даём клиенту знать что заявка принята — до медленных операций
    # (Telegram POST через прокси, ORM, и т.д.)
    await callback.bot.send_message(chat_id=chat_id, text=texts.BOOKING_ACCEPTING)

    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)
    booking = await _create_booking(
        bot_user_id=bot_user.id,
        service_id=data["service_id"],
        client_name=data["name"],
        client_phone=data["phone"],
    )
    # Запомнить ID СРАЗУ — до Telegram чтобы повторный клик отлавливался
    # даже если уведомление зависнет.
    await sync_to_async(cache.set)(idem_key, booking.id, BOOKING_IDEMPOTENCY_TTL)

    # T-09.5: запоминаем client_name/phone в BotUser
    await _persist_client_to_bot_user(
        bot_user_id=bot_user.id,
        client_name=data["name"],
        client_phone=data["phone"],
    )
    await _bump_bookings_count(bot_user.id)
    await sync_to_async(_notify_bot_booking)(booking)
    await context.clear()

    await send_with_main_menu(
        bot=callback.bot, chat_id=chat_id,
        text=texts.BOOKING_DONE.format(request_id=booking.id),
        bot_user=bot_user,
    )


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_CONFIRM_NO)
async def on_confirm_no(callback: MessageCallback, context: MemoryContext) -> None:
    """Отмена — clear state, возврат в меню (без создания BookingRequest)."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    await context.clear()
    if chat_id is None:
        return
    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)
    await send_with_main_menu(
        bot=callback.bot, chat_id=chat_id,
        text=texts.BOOKING_CANCELLED, bot_user=bot_user,
    )


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_CONFIRM_OTHER)
async def on_confirm_other(callback: MessageCallback, context: MemoryContext) -> None:
    """T-09.5: «Указать другие данные» — сбросить name/phone из ctx, начать FSM с awaiting_name."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    data = await context.get_data()
    # Полностью сбрасываем data (set_data, не update_data — нужно стереть name/phone),
    # сохраняя только service_id/name. Переход в awaiting_name.
    await context.set_state(BookingStates.awaiting_name)
    await context.set_data({
        "service_id": data.get("service_id"),
        "service_name": data.get("service_name"),
    })
    await callback.bot.send_message(chat_id=chat_id, text=texts.BOOKING_ASK_NAME)


# ─── Sync helpers ───────────────────────────────────────────────────────────


@sync_to_async
def _service_name(service_id: int | None) -> str:
    if not service_id:
        return "—"
    svc = Service.objects.filter(id=service_id).first()
    return svc.name if svc else "—"


@sync_to_async
def _create_booking(
    *, bot_user_id: int, service_id: int, client_name: str, client_phone: str,
) -> BookingRequest:
    svc = Service.objects.filter(id=service_id).first()
    return BookingRequest.objects.create(
        category_name=svc.category.name if svc and svc.category_id else "",
        service_name=svc.name if svc else "Не указана",
        client_name=client_name,
        client_phone=client_phone,
        source="bot_max",
        bot_user_id=bot_user_id,
    )


@sync_to_async
def _bump_bookings_count(bot_user_id: int) -> None:
    from django.db import transaction
    with transaction.atomic():
        bu = BotUser.objects.select_for_update().get(pk=bot_user_id)
        bu.context["bookings_count"] = bu.context.get("bookings_count", 0) + 1
        bu.save(update_fields=["context", "last_seen"])


@sync_to_async
def _persist_client_to_bot_user(*, bot_user_id: int, client_name: str, client_phone: str) -> None:
    """Запомнить имя/телефон клиента в BotUser для пропуска FSM при повторной записи."""
    BotUser.objects.filter(pk=bot_user_id).update(
        client_name=client_name,
        client_phone=client_phone,
    )


def _notify_bot_booking(booking: BookingRequest) -> None:
    """Telegram + email о новой заявке из MAX-бота. Зеркало wizard-нотификации."""
    from notifications import send_notification_email, send_notification_telegram

    tg_text = (
        f"📋 Заявка из MAX-бота!\n\n"
        f"👤 {booking.client_name}\n"
        f"📞 {booking.client_phone}\n"
        f"💆 {booking.service_name}\n"
    )
    if booking.category_name:
        tg_text += f"📂 {booking.category_name}\n"
    send_notification_telegram(tg_text)

    email_lines = [
        f"Источник:  MAX-бот",
        f"Категория: {booking.category_name or '—'}",
        f"Услуга:    {booking.service_name}",
        f"Клиент:    {booking.client_name}",
        f"Телефон:   {booking.client_phone}",
        f"Время:     {booking.created_at:%d.%m.%Y %H:%M}",
        "",
        "Админка: /admin/services_app/bookingrequest/",
    ]
    send_notification_email(
        subject=f"Заявка из MAX-бота: {booking.service_name}",
        message="\n".join(email_lines),
    )
