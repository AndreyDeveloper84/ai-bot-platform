"""ActionService — обрабатывает confirm callback + booking creation.

Phase 2.3 §T09. Аналог Ayla/ai/application/services/action_service.py.

execute_confirm_booking(conversation):
1. Load последний confirm_booking action_data из messages (DESC)
2. Idempotency check: cache.get(f"ai-{conv_id}") → return existing если есть
3. Load Master + Service из БД, validate активные
4. Если Master.yclients_staff_id пустой → graceful: создаём BookingRequest
   без yclients_record_id, помечаем comment'ом, Telegram админу
5. YClientsAPI.create_booking(staff_id, services, datetime, client) → record_id
6. На YClients exception → graceful то же что и в (4)
7. Save BookingRequest(source='bot_max', yclients_record_id, comment='AI conv ...')
8. cache.set idempotency 1h
9. Save Message(role=tool, content=success/failure summary)
10. Close conversation (is_active=False)
11. Telegram админу про новую запись
12. Return ActionResultDTO
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime as dt_cls
from typing import Any

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone

from notifications import send_notification_telegram
from services_app.models import (
    BookingRequest,
    BookingReminder,
    Conversation,
    Master,
    Message,
    Service,
    ServiceOption,
)


logger = logging.getLogger("maxbot.ai_action")

IDEMPOTENCY_TTL_SECONDS = 3600  # 1h — двойные клики по одному conv


@dataclass(frozen=True)
class ActionResultDTO:
    """Результат execute_confirm_booking — для рендера ответа клиенту."""

    success: bool
    booking_request_id: int | None
    yclients_record_id: str | None
    requires_manual: bool
    user_message: str  # что показать клиенту
    error_code: str | None = None


def _idempotency_key(conv_id) -> str:
    return f"maxbot:ai:confirm:{conv_id}"


@sync_to_async
def _load_last_confirm_action(conv: Conversation) -> dict[str, Any] | None:
    """Последний assistant Message с action_type=confirm_booking → action_data."""
    msg = (
        Message.objects
        .filter(conversation=conv, role=Message.Role.ASSISTANT,
                action_type="confirm_booking")
        .order_by("-created_at")
        .first()
    )
    if msg is None or not msg.action_data:
        return None
    return msg.action_data


@sync_to_async
def _idempotent_existing(conv_id) -> int | None:
    """Возвращает booking_request_id если уже создан (двойной клик)."""
    return cache.get(_idempotency_key(conv_id))


@sync_to_async
def _save_idempotency(conv_id, booking_request_id: int) -> None:
    cache.set(_idempotency_key(conv_id), booking_request_id, IDEMPOTENCY_TTL_SECONDS)


async def _create_reminders(*, bot_user, booking, master_name: str,
                            service_name: str, yclients_record_id: str,
                            slot_iso: str) -> None:
    """Создать T-24h + T-2h reminders. Делегирует в reminders_factory."""
    from maxbot.reminders_factory import create_reminders_for_booking
    await sync_to_async(create_reminders_for_booking)(
        bot_user=bot_user, booking_request=booking,
        master_name=master_name, service_name=service_name,
        yclients_record_id=yclients_record_id, slot_iso=slot_iso,
    )


@sync_to_async
def _close_conversation(conv: Conversation, outcome: str = "") -> None:
    """Закрыть conversation + установить outcome для analytics (Phase 1).

    outcome: success / abandoned / redirected / error / "" (если не известно).
    """
    conv.is_active = False
    if outcome:
        conv.outcome = outcome
    conv.save(update_fields=["is_active", "outcome"] if outcome else ["is_active"])


@sync_to_async
def _close_active_by_bot_user(bot_user: BotUser, outcome: str) -> None:
    conv = (
        Conversation.objects
        .filter(bot_user=bot_user, is_active=True)
        .order_by("-created_at")
        .first()
    )
    if conv:
        conv.is_active = False
        conv.outcome = outcome
        conv.save(update_fields=["is_active", "outcome"])


async def close_active_conversation(bot_user: BotUser, outcome: str) -> None:
    """Закрыть активную conversation bot_user с outcome.

    Вызывается из ai_assistant при crash (outcome=error) и is_giveup (outcome=redirected).
    """
    await _close_active_by_bot_user(bot_user, outcome)


@sync_to_async
def _save_tool_message(conv: Conversation, content: str) -> None:
    Message.objects.create(
        conversation=conv, role=Message.Role.TOOL, content=content,
    )


@sync_to_async
def _create_booking_request(
    *,
    bot_user,
    master: Master,
    service: Service,
    slot_iso: str,
    yclients_record_id: str | None,
    requires_manual: bool,
    conv_id,
) -> BookingRequest:
    """Создать BookingRequest. comment маркируем источником и conv-id."""
    comment_parts = [f"AI Concierge | conversation={conv_id}"]
    if requires_manual:
        comment_parts.append("requires_manual_booking")
    comment = " | ".join(comment_parts)
    return BookingRequest.objects.create(
        category_name="",
        service_name=service.name,
        master_name=master.name,
        client_name=bot_user.client_name or bot_user.display_name or "Клиент бота",
        client_phone=bot_user.client_phone or "",
        comment=f"{comment}\nСлот: {slot_iso}\nyclients_record_id={yclients_record_id or 'NONE'}",
        source="bot_max",
        bot_user=bot_user,
        is_processed=bool(yclients_record_id),
    )


def _format_admin_alert(*, success: bool, master_name: str, service_name: str,
                        slot_iso: str, client_label: str, booking_id: int,
                        yclients_record_id: str | None,
                        requires_manual: bool) -> str:
    if success:
        return (
            f"🤖✅ AI Concierge создал запись\n"
            f"Клиент: {client_label}\n"
            f"Мастер: {master_name}\n"
            f"Услуга: {service_name}\n"
            f"Когда: {slot_iso}\n"
            f"YClients record: {yclients_record_id}\n"
            f"BookingRequest #{booking_id}"
        )
    return (
        f"⚠️ AI Concierge — нужна РУЧНАЯ запись\n"
        f"Клиент: {client_label}\n"
        f"Мастер: {master_name}\n"
        f"Услуга: {service_name}\n"
        f"Когда: {slot_iso}\n"
        f"Причина: {'нет yclients_staff_id' if requires_manual else 'YClients API недоступен'}\n"
        f"BookingRequest #{booking_id} (требует ручного бронирования)"
    )


async def execute_confirm_booking(
    *,
    conversation: Conversation,
    bot_user,
    yclients_api=None,
) -> ActionResultDTO:
    """Подтверждение записи: AI conv → BookingRequest + YClients (если есть mapping).

    yclients_api — для DI в тестах. Default — get_yclients_api() singleton.
    """
    conv_id = conversation.id

    # 1. Idempotency check (двойной клик)
    existing_id = await _idempotent_existing(conv_id)
    if existing_id:
        logger.info("execute_confirm_booking: idempotent hit conv=%s booking=%s",
                    conv_id, existing_id)
        # Не повторяем YClients call, не шлём Telegram. Возвращаем «уже записан».
        return ActionResultDTO(
            success=True,
            booking_request_id=int(existing_id),
            yclients_record_id=None,  # unknown в idempotent path
            requires_manual=False,
            user_message="Вы уже записаны. Ждём вас!",
        )

    # 2. Load action_data
    action_data = await _load_last_confirm_action(conversation)
    if action_data is None:
        logger.warning("execute_confirm_booking: no confirm action_data conv=%s", conv_id)
        return ActionResultDTO(
            success=False, booking_request_id=None, yclients_record_id=None,
            requires_manual=False,
            user_message="Не удалось найти данные записи. Попробуйте начать сначала.",
            error_code="no_action_data",
        )

    master_id = action_data.get("master_id")
    service_id = action_data.get("service_id")
    slot_iso = action_data.get("datetime") or ""

    # 3. Load Master + Service
    master = await sync_to_async(
        lambda: Master.objects.filter(id=master_id, is_active=True).first()
    )()
    service = await sync_to_async(
        lambda: Service.objects.filter(id=service_id, is_active=True).first()
    )()
    if master is None or service is None:
        return ActionResultDTO(
            success=False, booking_request_id=None, yclients_record_id=None,
            requires_manual=False,
            user_message="Услуга или мастер уже недоступны. Попробуйте выбрать другие.",
            error_code="orm_not_found",
        )

    # yclients_service_id живёт на ServiceOption (вариант услуги — длительность/цена),
    # а не на Service. Берём первый active вариант.
    yclients_service_id = await sync_to_async(
        lambda: (
            ServiceOption.objects
            .filter(service_id=service.id, yclients_service_id__isnull=False)
            .exclude(yclients_service_id="")
            .order_by("order", "id")
            .values_list("yclients_service_id", flat=True)
            .first()
        )
    )()

    requires_manual = (not master.yclients_staff_id) or (not yclients_service_id)
    yclients_record_id: str | None = None

    if not requires_manual:
        # 4. Try YClients booking
        if yclients_api is None:
            from services_app.yclients_api import get_yclients_api
            yclients_api = get_yclients_api()

        try:
            booking_data = await sync_to_async(yclients_api.create_booking)(
                staff_id=int(master.yclients_staff_id),
                services=[int(yclients_service_id)],
                datetime=slot_iso,
                client={
                    "phone": bot_user.client_phone or "",
                    "name": bot_user.client_name or bot_user.display_name or "Клиент",
                },
                comment=f"AI Concierge MAX-bot conv={conv_id}",
            )
            yclients_record_id = str(booking_data.get("record_id") or "")
        except Exception as exc:  # noqa: BLE001
            logger.exception("execute_confirm_booking: YClients API failed conv=%s", conv_id)
            requires_manual = True

    # 5. Save BookingRequest (всегда — для admin visibility)
    booking = await _create_booking_request(
        bot_user=bot_user, master=master, service=service,
        slot_iso=slot_iso,
        yclients_record_id=yclients_record_id,
        requires_manual=requires_manual,
        conv_id=conv_id,
    )

    # 6. Idempotency
    await _save_idempotency(conv_id, booking.id)

    # 6b. Reminder system (N2): создаём 2 напоминания только если запись
    # в YClients прошла И chat_id известен (нужен для проактивных сообщений).
    if yclients_record_id and bot_user.chat_id:
        try:
            await _create_reminders(
                bot_user=bot_user, booking=booking,
                master_name=master.name, service_name=service.name,
                yclients_record_id=yclients_record_id, slot_iso=slot_iso,
            )
        except Exception:  # noqa: BLE001
            # Reminders — не блокирующий feature; ошибка не должна сломать booking.
            logger.exception(
                "execute_confirm_booking: _create_reminders failed conv=%s booking=%s",
                conv_id, booking.id,
            )

    # 7. Tool-message в conversation для audit
    tool_content = (
        f"booking_created id={booking.id} yclients_record_id={yclients_record_id} "
        f"requires_manual={requires_manual}"
    )
    await _save_tool_message(conversation, tool_content)

    # 8. Close conversation + outcome (Phase 1):
    # - yclients_record_id есть → success (запись создана нативно)
    # - yclients_record_id нет (graceful) → redirected (менеджер запишет вручную)
    outcome = (
        Conversation.Outcome.SUCCESS.value if yclients_record_id
        else Conversation.Outcome.REDIRECTED.value
    )
    await _close_conversation(conversation, outcome=outcome)

    # 9. Telegram admin
    client_label = bot_user.client_name or bot_user.display_name or f"#{bot_user.max_user_id}"
    alert = _format_admin_alert(
        success=bool(yclients_record_id),
        master_name=master.name, service_name=service.name,
        slot_iso=slot_iso, client_label=client_label,
        booking_id=booking.id,
        yclients_record_id=yclients_record_id,
        requires_manual=requires_manual,
    )
    await sync_to_async(send_notification_telegram)(alert)

    # 10. User-friendly message
    if yclients_record_id:
        try:
            pretty = dt_cls.fromisoformat(slot_iso).strftime("%d.%m.%Y в %H:%M")
        except (ValueError, TypeError):
            pretty = slot_iso
        user_message = (
            f"✅ Записал вас на {pretty} — {master.name}, {service.name}.\n"
            f"Если планы изменятся — напишите, отменим."
        )
    else:
        user_message = (
            "Не получилось автоматически записать вас в систему. "
            "Менеджер свяжется с вами в ближайшее время — ваша заявка принята."
        )

    return ActionResultDTO(
        success=bool(yclients_record_id),
        booking_request_id=booking.id,
        yclients_record_id=yclients_record_id or None,
        requires_manual=requires_manual,
        user_message=user_message,
    )


async def cancel_conversation(conversation: Conversation) -> str:
    """User кликнул [❌ Отмена] на confirm_booking. Закрываем conv + outcome=abandoned."""
    await _save_tool_message(conversation, "user_cancelled_booking")
    await _close_conversation(conversation, outcome=Conversation.Outcome.ABANDONED.value)
    return "Отменено. Если передумаете — я тут!"
