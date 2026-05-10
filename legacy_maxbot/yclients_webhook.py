"""YClients webhook handler — приём событий записей из YClients (admin).

Когда администратор салона создаёт/изменяет/отменяет запись через
YClients-кабинет, YClients шлёт POST на наш URL. Мы:
1. Парсим payload (resource=record + status=create/update/delete)
2. Если client.phone совпадает с BotUser.client_phone — связываем запись
   с ботом (создаём BookingRequest source='yclients_admin')
3. Создаём 2 BookingReminder (T-24h + T-2h) — N2 reminder cycle
4. Шлём приветственное сообщение в личку через MAX-бот
5. На update — пересоздаём reminders с новой датой
6. На delete — помечаем reminders CANCELLED

YClients webhook payload (реальный формат — см. https://developers.yclients.com/ru/#tag/Webhooki):
{
    "company_id": 123,
    "resource": "record",
    "status": "create",  # create / update / delete
    "data": {
        "id": 12345,                       # yclients_record_id
        "datetime": "2026-05-15 14:00:00", # naive Moscow
        "client": {"phone": "+79991234567", "name": "Анна"},
        "staff": {"id": 67890, "name": "Мария"},
        "services": [{"id": 11111, "title": "Массаж спины"}],
    }
}

Endpoint: POST /api/yclients/webhook/

ВАЖНО: всегда возвращаем 200 (даже на ошибку) — иначе YClients ретраит
бесконечно. Идемпотентность через unique_together(yclients_record_id, kind)
в BookingReminder.
"""
from __future__ import annotations

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from notifications.max_bot import send_max_message
from services_app.models import (
    BookingRequest,
    BookingReminder,
    BotUser,
    Master,
    Service,
)

logger = logging.getLogger("maxbot.yclients_webhook")


def _normalize_phone(phone: str) -> str:
    """Только цифры, ведущая 8 → 7; 10 цифр → 7XXXXXXXXXX."""
    if not phone:
        return ""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


def _phone_matches(stored: str, incoming: str) -> bool:
    """Совпадает ли BotUser.client_phone с phone из webhook (по последним 10 цифрам)."""
    if not stored or not incoming:
        return False
    stored_norm = _normalize_phone(stored)
    incoming_norm = _normalize_phone(incoming)
    if not stored_norm or not incoming_norm:
        return False
    return stored_norm.endswith(incoming_norm[-10:])


def _find_bot_user_by_phone(phone: str) -> BotUser | None:
    """Поиск BotUser по нормализованному phone. Возвращает первый match с chat_id."""
    if not phone:
        return None
    digits = _normalize_phone(phone)
    if not digits:
        return None
    last10 = digits[-10:]
    for bu in BotUser.objects.exclude(chat_id__isnull=True).exclude(client_phone=""):
        if _phone_matches(bu.client_phone, phone):
            return bu
    # Fallback — точный текстовый поиск с возможными вариациями формата
    return BotUser.objects.filter(client_phone__contains=last10).first()


def _slot_iso_from_yclients(datetime_str: str) -> str:
    """YClients шлёт naive 'YYYY-MM-DD HH:MM:SS' — приводим к ISO-aware Moscow."""
    if not datetime_str:
        return ""
    # Replace space → T для fromisoformat
    return datetime_str.replace(" ", "T")


def _resolve_master_service(staff_id, services_list) -> tuple[str, str, int | None]:
    """Из YClients staff/services → (master_name, service_name, master_id_local).

    Если у нас есть Master с yclients_staff_id=staff_id — используем имя из БД.
    Иначе — имя из YClients payload (snapshot).
    """
    master_name = ""
    master_id_local = None
    if staff_id:
        local_master = Master.objects.filter(yclients_staff_id=str(staff_id)).first()
        if local_master:
            master_name = local_master.name
            master_id_local = local_master.id

    service_name = ""
    if services_list:
        service_name = services_list[0].get("title", "")
    return master_name, service_name, master_id_local


def _handle_record_create(data: dict) -> dict:
    """Создать BookingRequest + reminders + приветствие."""
    from maxbot.reminders_factory import create_reminders_for_booking

    yc_id = str(data.get("id") or "")
    if not yc_id:
        return {"status": "skipped", "reason": "no_record_id"}

    # Idempotency: уже обработали?
    if BookingReminder.objects.filter(yclients_record_id=yc_id).exists():
        return {"status": "skipped", "reason": "already_synced"}

    client = data.get("client") or {}
    phone = client.get("phone") or ""
    bot_user = _find_bot_user_by_phone(phone)
    if bot_user is None:
        logger.info("yclients_webhook.create: no BotUser for phone=%s yc=%s",
                    phone[:4] + "..." if phone else "—", yc_id)
        return {"status": "skipped", "reason": "no_bot_user_match"}
    if not bot_user.chat_id:
        return {"status": "skipped", "reason": "bot_user_no_chat_id"}

    master_name, service_name, _ = _resolve_master_service(
        data.get("staff", {}).get("id"),
        data.get("services") or [],
    )
    slot_iso = _slot_iso_from_yclients(data.get("datetime") or "")
    if not slot_iso:
        return {"status": "skipped", "reason": "no_datetime"}

    # Создаём BookingRequest для админ-видимости + связь с reminders.
    booking = BookingRequest.objects.create(
        category_name="",
        service_name=service_name or "—",
        master_name=master_name or "—",
        client_name=client.get("name") or bot_user.client_name or "Клиент",
        client_phone=phone or bot_user.client_phone or "",
        comment=f"YClients admin booking | yclients_record_id={yc_id}",
        source="yclients_admin",
        bot_user=bot_user,
        is_processed=True,
    )

    created = create_reminders_for_booking(
        bot_user=bot_user, booking_request=booking,
        master_name=master_name or "—", service_name=service_name or "—",
        yclients_record_id=yc_id, slot_iso=slot_iso,
    )

    # Приветствие в личку
    try:
        from datetime import datetime as dt_cls
        visit_at = dt_cls.fromisoformat(slot_iso)
        when = visit_at.strftime("%d.%m в %H:%M")
    except (ValueError, TypeError):
        when = slot_iso

    greeting_name = bot_user.client_name or bot_user.display_name or ""
    salute = f"Здравствуйте, {greeting_name}! " if greeting_name else "Здравствуйте! "
    text = (
        f"{salute}Вы записаны на {when}\n"
        f"💆 Мастер: {master_name or '—'}\n"
        f"📋 Услуга: {service_name or '—'}\n\n"
        f"Накануне в 19:00 пришлю напоминание. Если планы поменяются — "
        f"напишите, мы поможем перенести."
    )
    send_max_message(chat_id=bot_user.chat_id, text=text)

    logger.info(
        "yclients_webhook.create: yc=%s booking=%s reminders=%d bot_user=%s",
        yc_id, booking.id, created, bot_user.id,
    )
    return {
        "status": "created", "yclients_record_id": yc_id,
        "booking_request_id": booking.id, "reminders_created": created,
    }


def _format_dt(dt) -> str:
    try:
        return dt.strftime("%d.%m в %H:%M")
    except Exception:  # noqa: BLE001
        return str(dt)


def _get_reminder_snapshot(yc_id: str) -> dict | None:
    """Снимок данных из любого существующего reminder'а (для уведомлений
    при update/delete — нужны bot_user.chat_id + service_name + visit_at)."""
    r = (
        BookingReminder.objects
        .filter(yclients_record_id=yc_id)
        .select_related("bot_user")
        .first()
    )
    if r is None:
        return None
    return {
        "chat_id": r.bot_user.chat_id if r.bot_user else r.chat_id,
        "client_name": (r.bot_user.client_name or r.bot_user.display_name or "")
                       if r.bot_user else "",
        "master_name": r.master_name,
        "service_name": r.service_name,
        "visit_at": r.visit_at,
    }


def _handle_record_update(data: dict) -> dict:
    """Обновление записи в YClients — если изменился datetime, пересоздать
    reminders + уведомить клиента «было / стало»."""
    from datetime import datetime as dt_cls
    from maxbot.reminders_factory import reschedule_reminders_for_record

    yc_id = str(data.get("id") or "")
    if not yc_id:
        return {"status": "skipped", "reason": "no_record_id"}

    new_slot_iso = _slot_iso_from_yclients(data.get("datetime") or "")
    if not new_slot_iso:
        return {"status": "skipped", "reason": "no_datetime"}

    # Берём snapshot ДО reschedule (там старая visit_at)
    snapshot = _get_reminder_snapshot(yc_id)
    if snapshot is None:
        # Не было синка — создаём как новую (на случай если create event пропустили)
        return _handle_record_create(data)

    old_visit_at = snapshot["visit_at"]
    count = reschedule_reminders_for_record(
        yclients_record_id=yc_id, new_slot_iso=new_slot_iso,
    )

    # Уведомление клиенту о переносе
    chat_id = snapshot["chat_id"]
    if chat_id:
        try:
            new_visit_at = dt_cls.fromisoformat(new_slot_iso)
        except (ValueError, TypeError):
            new_visit_at = None
        # Если дата не поменялась (секунды отбрасываем) — это update других
        # полей (мастер/услуга), не дёргаем клиента сообщением. Threshold 5 мин
        # достаточно — реальный перенос всегда на десятки минут+.
        date_changed = False
        if new_visit_at is not None and old_visit_at is not None:
            new_aware = (
                new_visit_at.replace(tzinfo=old_visit_at.tzinfo)
                if new_visit_at.tzinfo is None
                else new_visit_at
            )
            delta_sec = abs((new_aware - old_visit_at).total_seconds())
            date_changed = delta_sec > 300
        if date_changed:
            client = snapshot.get("client_name") or ""
            salute = f"{client}, " if client else ""
            text = (
                f"🔄 {salute}ваша запись перенесена\n"
                f"Было: {_format_dt(old_visit_at)}\n"
                f"Стало: {_format_dt(new_visit_at)}\n"
                f"💆 Мастер: {snapshot['master_name'] or '—'}\n"
                f"📋 Услуга: {snapshot['service_name'] or '—'}\n\n"
                f"Если новая дата не подходит — напишите, мы поможем подобрать "
                f"другое время."
            )
            send_max_message(chat_id=chat_id, text=text)

    logger.info("yclients_webhook.update: yc=%s rescheduled=%d", yc_id, count)
    return {"status": "updated", "yclients_record_id": yc_id, "rescheduled": count}


def _handle_record_delete(data: dict) -> dict:
    """Запись отменена в YClients — помечаем reminders CANCELLED + уведомляем."""
    from maxbot.reminders_factory import cancel_reminders_for_record

    yc_id = str(data.get("id") or "")
    if not yc_id:
        return {"status": "skipped", "reason": "no_record_id"}

    # Snapshot ДО cancel — нужен chat_id и текст для сообщения
    snapshot = _get_reminder_snapshot(yc_id)
    count = cancel_reminders_for_record(yc_id)

    notified = False
    if snapshot and snapshot.get("chat_id"):
        client = snapshot.get("client_name") or ""
        salute = f"{client}, " if client else ""
        text = (
            f"❌ {salute}ваша запись отменена\n"
            f"📅 {_format_dt(snapshot['visit_at'])}\n"
            f"💆 Мастер: {snapshot['master_name'] or '—'}\n"
            f"📋 Услуга: {snapshot['service_name'] or '—'}\n\n"
            f"Если отмена ошибочная — напишите, мы поможем перезаписаться. "
            f"Будем рады видеть вас снова!"
        )
        send_max_message(chat_id=snapshot["chat_id"], text=text)
        notified = True

    logger.info(
        "yclients_webhook.delete: yc=%s cancelled=%d notified=%s",
        yc_id, count, notified,
    )
    return {
        "status": "deleted", "yclients_record_id": yc_id,
        "cancelled": count, "notified": notified,
    }


@csrf_exempt
@require_POST
def yclients_webhook(request):
    """POST /api/yclients/webhook/ — приём событий из YClients.

    Всегда отвечает 200 (даже на ошибку) — иначе YClients ретраит бесконечно.
    Идемпотентность обеспечена unique_together в BookingReminder.
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("yclients_webhook: invalid JSON: %s", exc)
        return JsonResponse({"status": "error", "reason": "invalid_json"}, status=200)

    resource = payload.get("resource") or ""
    status = payload.get("status") or ""
    data = payload.get("data") or {}

    if resource != "record":
        # Игнорируем не-record события (clients, etc)
        return JsonResponse({"status": "ignored", "reason": f"resource={resource}"})

    try:
        if status == "create":
            result = _handle_record_create(data)
        elif status == "update":
            result = _handle_record_update(data)
        elif status == "delete":
            result = _handle_record_delete(data)
        else:
            result = {"status": "ignored", "reason": f"unknown_status={status}"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("yclients_webhook: handler crashed: %s", exc)
        result = {"status": "error", "reason": "handler_exception"}

    return JsonResponse(result)
