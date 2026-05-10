"""Factory для создания BookingReminder — переиспользуется AI Concierge
(execute_confirm_booking) и YClients webhook (record_create/update).

Логика T-24h + T-2h + idempotency через unique_together(yclients_record_id, kind)
вынесена сюда чтобы оба consumer'а вели себя одинаково.
"""
from __future__ import annotations

import logging
from datetime import datetime as dt_cls, timedelta

from django.utils import timezone

from services_app.models import BookingReminder

logger = logging.getLogger("maxbot.reminders_factory")

try:
    from zoneinfo import ZoneInfo
    MSK = ZoneInfo("Europe/Moscow")
except Exception:  # noqa: BLE001
    from datetime import timezone as _tz
    MSK = _tz(timedelta(hours=3))


def create_reminders_for_booking(
    *,
    bot_user,
    booking_request=None,
    master_name: str,
    service_name: str,
    yclients_record_id: str,
    slot_iso: str,
) -> int:
    """Sync-функция: создаёт T-24h + T-2h reminders. Возвращает кол-во созданных.

    bot_user.chat_id обязателен — иначе reminder бесполезен (некуда слать).
    Если slot_iso невалиден → 0 созданий (без exception).
    Reminder с scheduled_at в прошлом не создаётся.
    Idempotent через bulk_create(ignore_conflicts=True) — повторный вызов
    с тем же yclients_record_id не дублирует.
    """
    if not bot_user.chat_id:
        logger.info(
            "create_reminders: skip booking %s — bot_user has no chat_id",
            yclients_record_id,
        )
        return 0

    try:
        visit_at = dt_cls.fromisoformat(slot_iso)
    except (ValueError, TypeError):
        logger.warning(
            "create_reminders: bad slot_iso=%r for record %s — skipped",
            slot_iso, yclients_record_id,
        )
        return 0
    if visit_at.tzinfo is None:
        visit_at = visit_at.replace(tzinfo=MSK)

    visit_local = visit_at.astimezone(MSK)
    day_before_19 = (visit_local - timedelta(days=1)).replace(
        hour=19, minute=0, second=0, microsecond=0,
    )
    two_hours_before = visit_at - timedelta(hours=2)
    now = timezone.now()

    to_create = []
    if day_before_19 > now:
        to_create.append(BookingReminder(
            bot_user=bot_user, booking_request=booking_request,
            yclients_record_id=yclients_record_id, chat_id=bot_user.chat_id,
            visit_at=visit_at, kind=BookingReminder.Kind.DAY_BEFORE,
            scheduled_at=day_before_19,
            master_name=master_name, service_name=service_name,
        ))
    if two_hours_before > now:
        to_create.append(BookingReminder(
            bot_user=bot_user, booking_request=booking_request,
            yclients_record_id=yclients_record_id, chat_id=bot_user.chat_id,
            visit_at=visit_at, kind=BookingReminder.Kind.TWO_HOURS,
            scheduled_at=two_hours_before,
            master_name=master_name, service_name=service_name,
        ))
    if not to_create:
        return 0

    BookingReminder.objects.bulk_create(to_create, ignore_conflicts=True)
    # bulk_create с ignore_conflicts не возвращает PK — считаем сколько реально
    # вставилось через filter.
    inserted = BookingReminder.objects.filter(
        yclients_record_id=yclients_record_id,
    ).count()
    logger.info(
        "create_reminders: yc=%s prepared=%d (now total=%d)",
        yclients_record_id, len(to_create), inserted,
    )
    return len(to_create)


def cancel_reminders_for_record(yclients_record_id: str) -> int:
    """Пометить все reminders YClients-записи как CANCELLED.

    Используется webhook'ом record_delete. Не удаляем — оставляем для аудита.
    Возвращает кол-во обновлённых.
    """
    qs = BookingReminder.objects.filter(yclients_record_id=yclients_record_id).exclude(
        status=BookingReminder.Status.CANCELLED,
    )
    count = qs.update(status=BookingReminder.Status.CANCELLED)
    logger.info("cancel_reminders: yc=%s cancelled=%d", yclients_record_id, count)
    return count


def reschedule_reminders_for_record(
    *, yclients_record_id: str, new_slot_iso: str,
) -> int:
    """Перенести reminders на новую дату визита (webhook record_update).

    Стратегия: удалить existing PENDING/SENT_NO_REPLY reminders + создать
    новые с новой датой. CONFIRMED/CANCELLED/ESCALATED не трогаем.
    """
    try:
        new_visit_at = dt_cls.fromisoformat(new_slot_iso)
    except (ValueError, TypeError):
        return 0
    if new_visit_at.tzinfo is None:
        new_visit_at = new_visit_at.replace(tzinfo=MSK)

    existing = list(
        BookingReminder.objects
        .filter(yclients_record_id=yclients_record_id)
        .order_by("kind")
    )
    if not existing:
        return 0

    proto = existing[0]  # любой — берём bot_user / chat_id / master_name
    BookingReminder.objects.filter(
        yclients_record_id=yclients_record_id,
        status__in=[
            BookingReminder.Status.PENDING,
            BookingReminder.Status.SENT_NO_REPLY,
        ],
    ).delete()

    return create_reminders_for_booking(
        bot_user=proto.bot_user,
        booking_request=proto.booking_request,
        master_name=proto.master_name,
        service_name=proto.service_name,
        yclients_record_id=yclients_record_id,
        slot_iso=new_visit_at.isoformat(),
    )
