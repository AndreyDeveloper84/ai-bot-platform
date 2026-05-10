"""Celery tasks для MAX-бота — N2 система напоминаний.

Запускаются из Celery beat (см. CELERY_BEAT_SCHEDULE в settings/base.py):
- send_due_reminders — каждые 15 минут
- escalate_stale_reminders — каждый час
"""
from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger("maxbot.tasks")


@shared_task(name="maxbot.tasks.send_due_reminders", bind=True, max_retries=2)
def send_due_reminders(self):
    """Отправить все PENDING напоминания у которых scheduled_at <= now.

    T-24h → 3 кнопки [Подтверждаю/Перенести/Отменить] → status=SENT_NO_REPLY.
    T-2h → текст без кнопок → status=SENT.
    Если send_max_message вернул False (token не задан, API упал) — пропускаем,
    статус не меняется. Следующий tick попробует ещё раз.
    Idempotency: только PENDING обрабатываются; ушедшие SENT_NO_REPLY/SENT
    не пытаемся пересылать.
    """
    from notifications.max_bot import send_max_message
    from services_app.models import BookingReminder
    from maxbot.keyboards import reminder_24h_keyboard

    now = timezone.now()
    pending = list(
        BookingReminder.objects
        .filter(status=BookingReminder.Status.PENDING, scheduled_at__lte=now)
        .select_related("bot_user")
        .order_by("scheduled_at")[:100]
    )
    if not pending:
        return {"sent": 0, "checked": 0}

    sent = 0
    failed = 0
    for r in pending:
        if not r.chat_id:
            logger.warning("reminder %s: empty chat_id, marking FAILED", r.id)
            r.status = BookingReminder.Status.FAILED
            r.save(update_fields=["status"])
            continue

        text = _format_reminder_text(r)
        attachments = None
        if r.kind == BookingReminder.Kind.DAY_BEFORE:
            attachments = [reminder_24h_keyboard(str(r.id))]

        ok = send_max_message(chat_id=r.chat_id, text=text, attachments=attachments)
        if ok:
            r.status = (
                BookingReminder.Status.SENT_NO_REPLY
                if r.kind == BookingReminder.Kind.DAY_BEFORE
                else BookingReminder.Status.SENT
            )
            r.sent_at = timezone.now()
            r.save(update_fields=["status", "sent_at"])
            sent += 1
        else:
            failed += 1
            logger.warning(
                "send_due_reminders: send failed for reminder=%s chat_id=%s",
                r.id, r.chat_id,
            )

    logger.info(
        "send_due_reminders: sent=%d failed=%d checked=%d",
        sent, failed, len(pending),
    )
    return {"sent": sent, "failed": failed, "checked": len(pending)}


@shared_task(name="maxbot.tasks.escalate_stale_reminders", bind=True, max_retries=1)
def escalate_stale_reminders(self):
    """Если T-24h отправлено и за 12ч до визита нет ответа → Telegram менеджеру.

    После эскалации статус → ESCALATED, повторно не уведомляем.
    """
    from notifications import send_notification_telegram
    from services_app.models import BookingReminder

    now = timezone.now()
    cutoff = now + timedelta(hours=12)
    stale = list(
        BookingReminder.objects
        .filter(
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.SENT_NO_REPLY,
            visit_at__lte=cutoff,
            visit_at__gte=now,  # не эскалируем уже прошедшие визиты
        )
        .select_related("bot_user")[:50]
    )
    if not stale:
        return {"escalated": 0, "checked": 0}

    escalated = 0
    for r in stale:
        bu = r.bot_user
        client = bu.client_name or bu.display_name or f"#{bu.max_user_id}"
        phone = bu.client_phone or "—"
        msg = (
            f"⚠️ Клиент НЕ подтвердил запись\n\n"
            f"📅 {r.visit_at.strftime('%d.%m.%Y в %H:%M')}\n"
            f"👤 {client}\n"
            f"📞 {phone}\n"
            f"💆 {r.master_name}\n"
            f"📋 {r.service_name}\n\n"
            f"Позвоните клиенту, чтобы уточнить."
        )
        if send_notification_telegram(msg):
            r.status = BookingReminder.Status.ESCALATED
            r.save(update_fields=["status"])
            escalated += 1
        else:
            logger.warning(
                "escalate_stale_reminders: telegram failed for reminder=%s", r.id,
            )

    logger.info(
        "escalate_stale_reminders: escalated=%d checked=%d",
        escalated, len(stale),
    )
    return {"escalated": escalated, "checked": len(stale)}


def _format_reminder_text(r) -> str:
    """Текст напоминания (зависит от kind)."""
    when = r.visit_at.strftime("%d.%m в %H:%M")
    if r.kind == "day_before":
        return (
            f"📅 Напоминаем: завтра ({when}) у вас запись\n"
            f"💆 Мастер: {r.master_name}\n"
            f"📋 Услуга: {r.service_name}\n\n"
            f"Подтвердите, что придёте 🙏"
        )
    return (
        f"⏰ Через 2 часа ({when}) у вас запись к {r.master_name}\n"
        f"📋 {r.service_name}\n\n"
        f"Ждём вас!"
    )


# ─── T07: post-visit follow-up + repeat offer ─────────────────────────────


@shared_task(name="maxbot.tasks.send_post_visit_followups", bind=True, max_retries=1)
def send_post_visit_followups(self):
    """Daily 19:00: «как прошёл визит?» клиентам у которых вчера был визит.

    Idempotent через BookingReminder с kind=DAY_BEFORE и status=CONFIRMED/
    SENT_NO_REPLY/ESCALATED у которых visit_at — вчерашняя дата.
    Проверяем bot_user.context["last_followup_sent_at"] чтобы не дублировать
    если task запустился дважды.
    """
    from datetime import date, datetime
    from notifications.max_bot import send_max_message
    from services_app.models import BookingReminder

    yesterday = date.today() - timedelta(days=1)
    yesterday_start = datetime.combine(yesterday, datetime.min.time())
    yesterday_end = datetime.combine(yesterday, datetime.max.time())

    # Только T-24h reminders (kind=DAY_BEFORE) — гарантируют что есть chat_id
    qs = (
        BookingReminder.objects
        .filter(
            kind=BookingReminder.Kind.DAY_BEFORE,
            visit_at__gte=yesterday_start,
            visit_at__lte=yesterday_end,
        )
        .select_related("bot_user")
    )

    sent = 0
    skipped = 0
    for r in qs:
        bu = r.bot_user
        if not bu.chat_id:
            skipped += 1
            continue
        # Idempotency через context
        ctx = bu.context or {}
        last_sent = ctx.get("last_followup_sent_at", "")
        if last_sent == yesterday.isoformat():
            skipped += 1
            continue

        text = (
            f"Здравствуйте! Как прошёл вчерашний визит "
            f"к {r.master_name} ({r.service_name})? 🙏\n\n"
            f"Будем благодарны за обратную связь — это помогает нам "
            f"стать лучше. Напишите пару слов, или приходите к нам снова!"
        )
        if send_max_message(chat_id=bu.chat_id, text=text):
            ctx["last_followup_sent_at"] = yesterday.isoformat()
            bu.context = ctx
            bu.save(update_fields=["context", "last_seen"])
            sent += 1
        else:
            logger.warning(
                "send_post_visit_followups: send failed for bot_user=%s", bu.id,
            )

    logger.info("send_post_visit_followups: sent=%d skipped=%d", sent, skipped)
    return {"sent": sent, "skipped": skipped}


@shared_task(name="maxbot.tasks.send_repeat_offers", bind=True, max_retries=1)
def send_repeat_offers(self):
    """Weekly Monday 12:00: «время повторить?» клиентам с последним визитом
    21-28 дней назад. Не чаще 1 раза в 30 дней (через
    bot_user.context.last_repeat_offer_at).
    """
    from datetime import date, datetime
    from notifications.max_bot import send_max_message
    from services_app.models import BookingRequest

    today = date.today()
    cutoff_min = today - timedelta(days=28)
    cutoff_max = today - timedelta(days=21)
    rate_limit_after = today - timedelta(days=30)

    # Берём для каждого bot_user самую свежую processed BookingRequest
    # за окно [cutoff_min, cutoff_max]. Используем values_list trick.
    from services_app.models import BotUser

    qs = (
        BotUser.objects
        .filter(
            chat_id__isnull=False,
            booking_requests__is_processed=True,
            booking_requests__created_at__gte=cutoff_min,
            booking_requests__created_at__lte=cutoff_max,
        )
        .distinct()
    )

    sent = 0
    skipped = 0
    for bu in qs:
        ctx = bu.context or {}
        last_offer_str = ctx.get("last_repeat_offer_at", "")
        if last_offer_str:
            try:
                last_offer = date.fromisoformat(last_offer_str)
                if last_offer >= rate_limit_after:
                    skipped += 1
                    continue
            except ValueError:
                pass

        # Найдём последнюю запись для текста
        last_br = (
            BookingRequest.objects
            .filter(bot_user=bu, is_processed=True)
            .order_by("-created_at")
            .first()
        )
        if not last_br:
            skipped += 1
            continue

        client = bu.client_name or bu.display_name or ""
        greeting = f"Здравствуйте, {client}! " if client else "Здравствуйте! "
        text = (
            f"{greeting}С последнего визита к {last_br.master_name} "
            f"({last_br.service_name}) прошло около месяца. "
            f"Время повторить? Или попробуем что-то новое — расскажите, "
            f"что вас сейчас интересует 🌸"
        )
        if send_max_message(chat_id=bu.chat_id, text=text):
            ctx["last_repeat_offer_at"] = today.isoformat()
            bu.context = ctx
            bu.save(update_fields=["context", "last_seen"])
            sent += 1
        else:
            logger.warning(
                "send_repeat_offers: send failed for bot_user=%s", bu.id,
            )

    logger.info("send_repeat_offers: sent=%d skipped=%d", sent, skipped)
    return {"sent": sent, "skipped": skipped}


# ─── Phase 3.1 Part 2C: daily report push 21:00 МСК ────────────────────────


def _task_now_msk():
    """Helper для mocking в тестах. Returns current datetime в Europe/Moscow."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Europe/Moscow"))


@shared_task(name="maxbot.tasks.send_daily_reports", bind=True, max_retries=1)
def send_daily_reports(self):
    """Phase 3.1 Part 2C+2D.2: hourly push дневного отчёта.

    Beat schedule: crontab(minute=0) — каждый час в :00. Filter:
      - settings.NUTRITION_ENABLED == True
      - BotUser.nutrition_onboarded_at IS NOT NULL
      - BotUser.chat_id IS NOT NULL
      - nutrition_settings["daily_report_time"] == "{HH}:00" matching now's local hour

    Default daily_report_time = "21:00" (если key отсутствует).
    Value "off" → не шлём.

    Per-user TZ через bot_user.timezone (default 'Europe/Moscow').
    """
    import asyncio
    from zoneinfo import ZoneInfo

    from django.conf import settings as django_settings
    from notifications.max_bot import send_max_message
    from services_app.models import BotUser

    from maxbot import ai_ui, keyboards
    from maxbot.services.ayla_user_proxy import external_user_id_for
    from maxbot.services.nutrition_client import (
        NutritionAPIError,
        NutritionUnavailableError,
        get_nutrition_client,
    )

    if not getattr(django_settings, "NUTRITION_ENABLED", False):
        logger.info("daily_reports.skipped — NUTRITION_ENABLED=False")
        return

    eligible = BotUser.objects.filter(
        nutrition_onboarded_at__isnull=False,
        chat_id__isnull=False,
    )
    sent = 0
    skipped = 0

    client = get_nutrition_client()

    for bot_user in eligible.iterator():
        # Per-user time check — ТZ юзера + setting
        time_setting = (bot_user.nutrition_settings or {}).get(
            "daily_report_time", "21:00",
        )
        if time_setting == "off":
            skipped += 1
            continue
        try:
            target_hour = int(time_setting.split(":")[0])
        except (ValueError, AttributeError):
            target_hour = 21  # corrupt setting → default

        try:
            user_tz = ZoneInfo(bot_user.timezone or "Europe/Moscow")
        except Exception:  # noqa: BLE001
            user_tz = ZoneInfo("Europe/Moscow")
        now_local = _task_now_msk().astimezone(user_tz)
        if now_local.hour != target_hour:
            skipped += 1
            continue

        extid = external_user_id_for(bot_user)
        try:
            summary, water_today = asyncio.run(_fetch_daily_data(client, extid))
        except (NutritionUnavailableError, NutritionAPIError) as exc:
            logger.warning(
                "daily_reports.fetch_failed user=%s err=%s",
                bot_user.max_user_id, exc,
            )
            skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "daily_reports.fetch_unexpected user=%s err=%s",
                bot_user.max_user_id, exc,
            )
            skipped += 1
            continue

        eating_disorder = bool(
            (bot_user.health_flags or {}).get("eating_disorder", False)
        )
        text = ai_ui.render_daily_full_report(
            summary, water_today, eating_disorder=eating_disorder,
        )
        try:
            send_max_message(
                bot_user.chat_id, text,
                attachments=[keyboards.daily_report_footer_keyboard()],
            )
            sent += 1
        except Exception:  # noqa: BLE001
            # B-19 (DRF-298): catch-all → logger.exception для traceback
            # в Sentry. NutritionUnavailable/APIError ловятся отдельно выше
            # (там logger.warning без traceback корректен).
            logger.exception(
                "daily_reports.send_failed user=%s chat=%s",
                bot_user.max_user_id, bot_user.chat_id,
            )
            skipped += 1

    logger.info("daily_reports.done sent=%d skipped=%d", sent, skipped)
    return {"sent": sent, "skipped": skipped}


async def _fetch_daily_data(client, external_user_id):
    """Fetch summary + water_today. Water — optional graceful skip."""
    from maxbot.services.nutrition_client import (
        NutritionAPIError,
        NutritionUnavailableError,
    )

    summary = await client.daily_summary(
        external_user_id=external_user_id, with_comment=True,
    )
    water_today = None
    try:
        water_today = await client.get_water_today(external_user_id=external_user_id)
    except (NutritionUnavailableError, NutritionAPIError):
        # Water gracefully skipped — render handles None
        pass
    return summary, water_today


# ─── Phase 3.1 Part 2D.2 T08: adaptive water reminders 4h ──────────────────


@shared_task(name="maxbot.tasks.send_water_reminders", bind=True, max_retries=1)
def send_water_reminders(self):
    """Phase 3.1 Part 2D.2: adaptive water reminders каждые 4 часа.

    Beat schedule: crontab(minute=0, hour="0,4,8,12,16,20") — UTC × 6/day.
    Per-user filter:
      - settings.NUTRITION_ENABLED == True
      - nutrition_onboarded_at IS NOT NULL
      - chat_id IS NOT NULL
      - nutrition_settings["water_reminders_enabled"] == True (opt-in)
      - НЕ quiet hours (22:00–09:00 local time)
      - today_total_ml < 50% от proportional_norm

    Sends inline keyboard [+250 мл][+500 мл][Уже пью].
    """
    import asyncio
    from zoneinfo import ZoneInfo

    from django.conf import settings as django_settings

    from notifications.max_bot import send_max_message
    from services_app.models import BotUser

    from maxbot import keyboards
    from maxbot.nutrition_settings_helpers import (
        calc_proportional_norm,
        is_quiet_hours_for_user,
    )
    from maxbot.services.ayla_user_proxy import external_user_id_for
    from maxbot.services.nutrition_client import (
        NutritionAPIError,
        NutritionUnavailableError,
        get_nutrition_client,
    )

    if not getattr(django_settings, "NUTRITION_ENABLED", False):
        logger.info("water_reminders.skipped — NUTRITION_ENABLED=False")
        return

    eligible = BotUser.objects.filter(
        nutrition_onboarded_at__isnull=False,
        chat_id__isnull=False,
    )
    sent = 0
    skipped = 0

    client = get_nutrition_client()
    now_msk = _task_now_msk()
    now_utc = now_msk.astimezone(ZoneInfo("UTC"))

    for bot_user in eligible.iterator():
        opt_in = bool(
            (bot_user.nutrition_settings or {}).get("water_reminders_enabled", False)
        )
        if not opt_in:
            skipped += 1
            continue

        if is_quiet_hours_for_user(bot_user, now_utc=now_utc):
            skipped += 1
            continue

        try:
            user_tz = ZoneInfo(bot_user.timezone or "Europe/Moscow")
        except Exception:  # noqa: BLE001
            user_tz = ZoneInfo("Europe/Moscow")

        # Same-day dismiss skip (Design §7.7 «Уже пью» → до завтра молчим)
        last_dismissed_iso = (bot_user.nutrition_settings or {}).get(
            "last_water_dismissed_at"
        )
        if last_dismissed_iso:
            try:
                from datetime import datetime
                last_dismissed = datetime.fromisoformat(last_dismissed_iso)
                # Если same-day local — skip
                local_now = now_utc.astimezone(user_tz)
                local_dismissed = last_dismissed.astimezone(user_tz)
                if local_dismissed.date() == local_now.date():
                    skipped += 1
                    continue
            except (ValueError, AttributeError):
                pass  # corrupt timestamp — ignore

        local_hour = now_utc.astimezone(user_tz).hour

        extid = external_user_id_for(bot_user)
        try:
            today = asyncio.run(client.get_water_today(external_user_id=extid))
        except (NutritionUnavailableError, NutritionAPIError) as exc:
            logger.warning(
                "water_reminders.fetch_failed user=%s err=%s",
                bot_user.max_user_id, exc,
            )
            skipped += 1
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "water_reminders.fetch_unexpected user=%s err=%s",
                bot_user.max_user_id, exc,
            )
            skipped += 1
            continue

        proportional = calc_proportional_norm(
            today.norm_ml, current_local_hour=local_hour,
        )
        threshold = proportional * 0.5
        if today.total_ml >= threshold:
            skipped += 1
            continue

        # Compose reminder text — Design §7.7
        deficit = max(0, today.norm_ml - today.total_ml)
        text = (
            f"💧 Сегодня выпито: {today.total_ml} / {today.norm_ml} мл.\n"
            f"До нормы — ещё {deficit} мл."
        )

        try:
            send_max_message(
                bot_user.chat_id, text,
                attachments=[keyboards.water_reminder_buttons_keyboard()],
            )
            sent += 1
        except Exception:  # noqa: BLE001
            # B-19 (DRF-298): catch-all → logger.exception для traceback в Sentry.
            logger.exception(
                "water_reminders.send_failed user=%s",
                bot_user.max_user_id,
            )
            skipped += 1

    logger.info("water_reminders.done sent=%d skipped=%d", sent, skipped)
    return {"sent": sent, "skipped": skipped}
