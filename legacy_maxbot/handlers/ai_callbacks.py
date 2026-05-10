"""AI Concierge callback handlers — клики по cb:ai:* кнопкам.

Phase 2.3 §T08. Регистрируется ПЕРЕД ai_assistant_router в get_routers(),
чтобы матчить специфичные callback-payload'ы раньше general message_created.

Callback payloads (стабильный wire-format с ai_ui.py):
- cb:ai:pick_master:{conv}:{master_id} — клиент выбрал мастера → AI turn
- cb:ai:pick_slot:{conv}:{slot_iso}    — клиент выбрал слот → AI turn
- cb:ai:answer:{conv}:{option_idx}     — ответ на ask_clarification → AI turn
- cb:ai:confirm:{conv}                  — подтверждение → execute_confirm_booking
- cb:ai:cancel:{conv}                   — отмена → close conversation
- cb:ai:edit:{conv}                     — изменить → ask_clarification

pick/answer переводят выбор клиента в pseudo-user-message и зовут
ai_concierge через run_ai_turn (диалог продолжается с тем же conversation,
LLM учитывает в контексте что клиент выбрал).
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback

from maxbot import ai_action_service, ai_ui
from maxbot.handlers.ai_assistant import run_ai_turn
from maxbot.menu_state import send_with_main_menu
from maxbot.personalization import get_or_create_bot_user
from services_app.models import Conversation, Master, Message


logger = logging.getLogger("maxbot.ai_callbacks")
router = Router()


CB_PREFIX = "cb:ai:"


# ─── Helpers ───────────────────────────────────────────────────────────────


def _parse_payload(payload: str) -> tuple[str, list[str]]:
    """Разбирает cb:ai:{kind}:{conv}[:rest...] → (kind, [conv, *rest])."""
    parts = (payload or "").split(":")
    # parts: ["cb", "ai", kind, conv, ...]
    if len(parts) < 4 or parts[0] != "cb" or parts[1] != "ai":
        return "", []
    return parts[2], parts[3:]


@sync_to_async
def _load_active_conversation(conv_id: str) -> Conversation | None:
    return Conversation.objects.filter(id=conv_id, is_active=True).first()


@sync_to_async
def _master_label(master_id: int) -> str:
    m = Master.objects.filter(id=master_id).first()
    return m.name if m else f"#{master_id}"


@sync_to_async
def _service_label(service_id: int) -> str:
    from services_app.models import Service
    s = Service.objects.filter(id=service_id).first()
    return s.name if s else f"#{service_id}"


@sync_to_async
def _last_clarification_options(conv_id: str) -> list[str]:
    """Загружаем options последнего ask_clarification message — для маппинга idx → text."""
    msg = (
        Message.objects
        .filter(conversation_id=conv_id, role=Message.Role.ASSISTANT,
                action_type="ask_clarification")
        .order_by("-created_at")
        .first()
    )
    if not msg or not msg.action_data:
        return []
    return list(msg.action_data.get("options") or [])


# ─── Pick master ───────────────────────────────────────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:ai:pick_master:"))
async def on_pick_master(callback: MessageCallback, context: MemoryContext) -> None:
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "pick_master" or len(rest) < 2:
        return
    conv_id, master_id_str = rest[0], rest[1]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)
    master_name = await _master_label(int(master_id_str))

    # Pseudo-user-message — продолжение диалога в AI Concierge
    pseudo = f"Выбираю мастера {master_name} (id={master_id_str})"
    logger.info("ai_callbacks.pick_master conv=%s master=%s", conv_id, master_id_str)
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=pseudo,
        original_user_text=f"выбор мастера {master_name}",
    )


# ─── Pick service (Phase 2.4 T02) ─────────────────────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:ai:pick_service:"))
async def on_pick_service(callback: MessageCallback, context: MemoryContext) -> None:
    """Клик по карточке услуги в recommend_services → AI turn с псевдо-выбором."""
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "pick_service" or len(rest) < 2:
        return
    conv_id, service_id_str = rest[0], rest[1]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)
    service_name = await _service_label(int(service_id_str))

    pseudo = f"Выбираю услугу «{service_name}» (id={service_id_str}). Покажи мастеров."
    logger.info("ai_callbacks.pick_service conv=%s service=%s", conv_id, service_id_str)
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=pseudo,
        original_user_text=f"выбор услуги {service_name}",
    )


# ─── Pick slot ─────────────────────────────────────────────────────────────


@sync_to_async
def _pick_slot_to_confirm(conv_id: str, slot_str: str) -> dict | None:
    """Детерминированный путь: show_slots → confirm_booking без LLM.

    1. Загружает последний show_slots action_data из DB (master_id, service_id, date).
    2. Собирает полный ISO datetime: "{date}T{slot}:00".
    3. Вызывает handle_confirm_booking (sync ORM) — проверка + имена.
    4. Сохраняет confirm Message в conversation.
    Возвращает action_data или None при ошибке (→ fallback на LLM).
    """
    from datetime import datetime as dt_cls

    from django.utils import timezone as dj_tz

    from maxbot.ai_context import build_master_context
    from maxbot.ai_tool_handlers import handle_confirm_booking
    from maxbot.ai_tools import ActionType

    # 1. Последний show_slots
    show_msg = (
        Message.objects
        .filter(conversation_id=conv_id, role=Message.Role.ASSISTANT, action_type="show_slots")
        .order_by("-created_at")
        .first()
    )
    if not show_msg or not show_msg.action_data:
        return None

    slots_data = show_msg.action_data
    date_str = slots_data.get("date") or ""
    master_id = slots_data.get("master_id")
    service_id = slots_data.get("service_id")
    if not (date_str and master_id and service_id):
        return None

    # 2. Полный ISO datetime (формат YClients: YYYY-MM-DDThh:mm:00)
    try:
        if "T" in slot_str:
            slot_dt = dt_cls.fromisoformat(slot_str)
            datetime_iso = slot_dt.strftime("%Y-%m-%dT%H:%M:00")
        else:
            datetime_iso = f"{date_str}T{slot_str}:00"
    except (ValueError, TypeError):
        return None

    # 3. Validate + enrich (имена мастера/услуги, цена, длительность)
    master_context = build_master_context()
    result = handle_confirm_booking(
        {"master_id": master_id, "service_id": service_id, "datetime": datetime_iso},
        master_context,
    )
    if result.action_type != ActionType.CONFIRM_BOOKING:
        return None

    # 4. Сохранить confirm Message в conversation
    conv = Conversation.objects.filter(id=conv_id, is_active=True).first()
    if conv is None:
        return None

    msg = Message.objects.create(
        conversation=conv,
        role=Message.Role.ASSISTANT,
        action_type=ActionType.CONFIRM_BOOKING,
        action_data=result.action_data,
        content="",
    )
    conv.last_message_at = msg.created_at
    conv.save(update_fields=["last_message_at"])

    return result.action_data


@router.message_callback(F.callback.payload.startswith("cb:ai:pick_slot:"))
async def on_pick_slot(callback: MessageCallback, context: MemoryContext) -> None:
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "pick_slot" or len(rest) < 2:
        return
    conv_id = rest[0]
    slot_str = ":".join(rest[1:])  # время может содержать ":" (10:00)

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)

    # Детерминированный путь: show_slots data → confirm_booking без LLM
    confirm_data = await _pick_slot_to_confirm(conv_id, slot_str)
    if confirm_data is not None:
        text, attachments = ai_ui.render_action(
            conversation_id=conv_id,
            action_type="confirm_booking",
            action_data=confirm_data,
        )
        # MAX API: только 1 inline_keyboard на сообщение.
        # confirm_booking уже содержит keyboard → отправляем без main_menu.
        has_keyboard = any(
            getattr(att, "type", None) == "inline_keyboard" for att in attachments
        )
        if has_keyboard:
            await callback.bot.send_message(
                chat_id=chat_id, text=text, attachments=attachments,
            )
        else:
            await send_with_main_menu(
                bot=callback.bot, chat_id=chat_id,
                text=text, extra_attachments=attachments, bot_user=bot_user,
            )
        logger.info("ai_callbacks.pick_slot conv=%s slot=%s → confirm_booking (direct)", conv_id, slot_str)
        return

    # Fallback: нет show_slots в истории → LLM
    pseudo = f"Хочу записаться на {slot_str}"
    logger.info("ai_callbacks.pick_slot conv=%s slot=%s → LLM fallback", conv_id, slot_str)
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=pseudo,
        original_user_text=f"выбор слота {slot_str}",
    )


# ─── Answer (ask_clarification option) ─────────────────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:ai:answer:"))
async def on_answer(callback: MessageCallback, context: MemoryContext) -> None:
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "answer" or len(rest) < 2:
        return
    conv_id, idx_str = rest[0], rest[1]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)

    options = await _last_clarification_options(conv_id)
    try:
        chosen = options[int(idx_str)]
    except (ValueError, IndexError):
        chosen = "(непонятный выбор)"

    pseudo = chosen
    logger.info("ai_callbacks.answer conv=%s idx=%s text=%r",
                conv_id, idx_str, chosen[:60])
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=pseudo,
        original_user_text=f"ответ на уточнение: {chosen}",
    )


# ─── Confirm booking ───────────────────────────────────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:ai:confirm:"))
async def on_confirm(callback: MessageCallback, context: MemoryContext) -> None:
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "confirm" or not rest:
        return
    conv_id = rest[0]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)

    conversation = await _load_active_conversation(conv_id)
    if conversation is None:
        await send_with_main_menu(
            bot=callback.bot, chat_id=chat_id,
            text="Эта запись уже завершена. Если хотите, начнём заново — спросите про услуги.",
            bot_user=bot_user,
        )
        return

    logger.info("ai_callbacks.confirm conv=%s user=%s", conv_id, user.user_id)
    result = await ai_action_service.execute_confirm_booking(
        conversation=conversation, bot_user=bot_user,
    )
    await send_with_main_menu(
        bot=callback.bot, chat_id=chat_id,
        text=result.user_message, bot_user=bot_user,
    )


# ─── Cancel ────────────────────────────────────────────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:ai:cancel:"))
async def on_cancel(callback: MessageCallback, context: MemoryContext) -> None:
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "cancel" or not rest:
        return
    conv_id = rest[0]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)

    conversation = await _load_active_conversation(conv_id)
    if conversation is None:
        # Уже закрыто — мягкий ответ
        await send_with_main_menu(
            bot=callback.bot, chat_id=chat_id,
            text="Запись уже отменена. Чем ещё помочь?",
            bot_user=bot_user,
        )
        return

    msg = await ai_action_service.cancel_conversation(conversation)
    await send_with_main_menu(
        bot=callback.bot, chat_id=chat_id, text=msg, bot_user=bot_user,
    )


# ─── Suggest alternative date / master (Phase 0 — empty slots fallback) ───


_OFFSET_LABELS = {
    "+1": "завтра",
    "+3": "через 3 дня",
    "+7": "через неделю",
}


@router.message_callback(F.callback.payload.startswith("cb:ai:suggest_date:"))
async def on_suggest_date(callback: MessageCallback, context: MemoryContext) -> None:
    """Empty slots fallback: клиент выбрал альтернативную дату → AI пересчитает."""
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "suggest_date" or len(rest) < 2:
        return
    conv_id, offset = rest[0], rest[1]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)

    label = _OFFSET_LABELS.get(offset, f"через {offset.lstrip('+')} дней")
    pseudo = f"Покажи свободное время {label}"
    logger.info("ai_callbacks.suggest_date conv=%s offset=%s", conv_id, offset)
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=pseudo,
        original_user_text=f"выбор даты {label}",
    )


@router.message_callback(F.callback.payload.startswith("cb:ai:suggest_master:"))
async def on_suggest_master(callback: MessageCallback, context: MemoryContext) -> None:
    """Empty slots fallback: подобрать другого мастера."""
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "suggest_master" or not rest:
        return
    conv_id = rest[0]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)

    pseudo = "Покажи других мастеров для этой услуги"
    logger.info("ai_callbacks.suggest_master conv=%s", conv_id)
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=pseudo,
        original_user_text="выбор другого мастера",
    )


# ─── Edit ──────────────────────────────────────────────────────────────────


@router.message_callback(F.callback.payload.startswith("cb:ai:edit:"))
async def on_edit(callback: MessageCallback, context: MemoryContext) -> None:
    """Изменить детали — отправляем pseudo-message «хочу изменить» в AI Concierge."""
    payload = callback.callback.payload
    kind, rest = _parse_payload(payload)
    if kind != "edit" or not rest:
        return
    conv_id = rest[0]

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)

    pseudo = "Хочу изменить детали записи"
    logger.info("ai_callbacks.edit conv=%s user=%s", conv_id, user.user_id)
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=pseudo,
        original_user_text="изменение деталей записи",
    )
