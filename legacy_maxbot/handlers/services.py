"""Handler «Услуги» — двухуровневое меню (категории → услуги) → старт FSM заявки.

Сценарий:
1. cb:menu:services       → on_show_categories  → клавиатура ServiceCategory.objects.active()
2. cb:cat:{id}            → on_show_services    → клавиатура Service.active() в категории
3. cb:svc:{id}            → on_pick_service     → FSM awaiting_name + "Как к вам обращаться?"

Двухуровневое меню — обязательно: MAX API хард-лимит 30 рядов на inline keyboard,
а в БД может быть 100+ услуг (см. ошибку errors.maxRows которую ловили без него).
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback

from maxbot import keyboards, texts
from maxbot.personalization import append_to_context, get_or_create_bot_user
from maxbot.states import BookingStates
from services_app.models import Service, ServiceCategory


def _has_full_client_data(bot_user) -> bool:
    """True если в BotUser сохранены имя клиента (как он сам назвался) + телефон."""
    return bool(bot_user.client_name and bot_user.client_phone)


logger = logging.getLogger("maxbot.services")
router = Router()


@sync_to_async
def _list_active_categories() -> list[ServiceCategory]:
    return list(
        ServiceCategory.objects.active()
        .filter(services__is_active=True)
        .distinct()
        .order_by("order", "name")
    )


@sync_to_async
def _get_category_or_none(cat_id: int) -> ServiceCategory | None:
    return ServiceCategory.objects.active().filter(id=cat_id).first()


@sync_to_async
def _list_services_in_category(cat_id: int) -> list[Service]:
    return list(
        Service.objects.active()
        .filter(category_id=cat_id)
        .order_by("name")
    )


@sync_to_async
def _get_service_or_none(service_id: int) -> Service | None:
    return Service.objects.filter(id=service_id, is_active=True).first()


# Обе кнопки главного меню — «📅 Записаться» и «ℹ️ Услуги» — ведут на список
# категорий. С точки зрения UX это одно действие: «выбрать что хочу записать».
@router.message_callback(F.callback.payload == keyboards.PAYLOAD_MENU_BOOK)
@router.message_callback(F.callback.payload == keyboards.PAYLOAD_MENU_SERVICES)
async def on_show_categories(callback: MessageCallback, context: MemoryContext) -> None:
    """Шаг 1: показываем список категорий."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    cats = await _list_active_categories()
    if cats:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Выберите категорию услуг:",
            attachments=[keyboards.categories_keyboard(cats)],
        )
    else:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Сейчас услуги не настроены. Позвоните по телефону из «Контактов».",
            attachments=[keyboards.back_to_menu_keyboard()],
        )


@router.message_callback(F.callback.payload.startswith(keyboards.PAYLOAD_CAT_PREFIX))
async def on_show_services(callback: MessageCallback, context: MemoryContext) -> None:
    """Шаг 2: показываем услуги выбранной категории."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    payload = callback.callback.payload or ""
    raw_id = payload[len(keyboards.PAYLOAD_CAT_PREFIX):]
    try:
        cat_id = int(raw_id)
    except ValueError:
        logger.warning("on_show_services: некорректный category payload %r", payload)
        return

    cat = await _get_category_or_none(cat_id)
    if cat is None:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Категория недоступна. Выберите другую.",
            attachments=[keyboards.back_to_menu_keyboard()],
        )
        return

    services = await _list_services_in_category(cat_id)
    if not services:
        await callback.bot.send_message(
            chat_id=chat_id,
            text=f"В категории «{cat.name}» сейчас нет доступных услуг.",
            attachments=[keyboards.back_to_menu_keyboard()],
        )
        return

    if len(services) > keyboards.MAX_KEYBOARD_ROWS:
        logger.warning(
            "on_show_services: cat=%s содержит %d услуг — обрезано до %d (MAX API limit)",
            cat.name, len(services), keyboards.MAX_KEYBOARD_ROWS,
        )

    await callback.bot.send_message(
        chat_id=chat_id,
        text=f"«{cat.name}» — выберите услугу:",
        attachments=[keyboards.services_keyboard(services)],
    )


@router.message_callback(F.callback.payload.startswith(keyboards.PAYLOAD_SVC_PREFIX))
async def on_pick_service(callback: MessageCallback, context: MemoryContext) -> None:
    """Шаг 3: услуга выбрана → старт FSM (ASK_NAME)."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    payload = callback.callback.payload or ""
    raw_id = payload[len(keyboards.PAYLOAD_SVC_PREFIX):]
    try:
        service_id = int(raw_id)
    except ValueError:
        logger.warning("on_pick_service: некорректный payload %r", payload)
        return

    svc = await _get_service_or_none(service_id)
    if svc is None:
        logger.warning("on_pick_service: Service id=%s не найден или не активен", service_id)
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Услуга больше недоступна. Выберите из главного меню.",
            attachments=[keyboards.back_to_menu_keyboard()],
        )
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)
    await append_to_context(bot_user.id, "services_viewed", svc.slug)

    # T-09.5: «бот помнит». Если у нас уже есть полные данные клиента из
    # прошлой записи — пропускаем FSM, сразу к подтверждению со старыми
    # name/phone. Кнопка «Указать другие» сбрасывает в начало FSM.
    if _has_full_client_data(bot_user):
        await context.set_state(BookingStates.awaiting_confirm)
        await context.update_data(
            service_id=svc.id,
            service_name=svc.name,
            name=bot_user.client_name,
            phone=bot_user.client_phone,
        )
        confirm_text = texts.BOOKING_CONFIRM.format(
            name=bot_user.client_name,
            phone=bot_user.client_phone,
            service=svc.name,
        )
        await callback.bot.send_message(
            chat_id=chat_id,
            text=confirm_text,
            attachments=[keyboards.confirm_booking_keyboard(with_other=True)],
        )
        return

    # Новый клиент или нет данных — обычный FSM
    await context.set_state(BookingStates.awaiting_name)
    await context.update_data(service_id=svc.id, service_name=svc.name)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=texts.BOOKING_ASK_NAME,
    )
