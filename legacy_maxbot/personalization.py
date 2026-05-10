"""Персонализация диалога: get_or_create_bot_user, greet_text, update_context.

Основа: services_app.BotUser. Все ORM-операции через sync_to_async — handler'ы
async, ORM sync. Конкурентные обновления context защищены через
transaction.atomic + select_for_update (один webhook на user обычно
сериализуется MAX-ом, но при ретраях/быстрых double-tap'ах race возможен).
"""
from __future__ import annotations

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from maxbot import texts
from services_app.models import BotUser


@sync_to_async
def get_or_create_bot_user(max_user_id: int, display_name: str = "", chat_id: int | None = None):
    """Возвращает (BotUser, created: bool).

    Для existing user — атомарный UPDATE одной командой (не save!) — экономит
    1 query на hot path /start. Не перезаписывает client_name (его клиент
    ввёл боту, а display_name приходит от MAX автоматически).

    chat_id — обновляется на каждом event'е (нужен для проактивных
    напоминаний из Celery). chat_id привязан к диалогу пользователь↔бот,
    в личном чате стабилен; в групповом может отличаться от max_user_id.
    """
    defaults = {"display_name": display_name}
    if chat_id is not None:
        defaults["chat_id"] = chat_id
    user, created = BotUser.objects.get_or_create(
        max_user_id=max_user_id,
        defaults=defaults,
    )
    if not created:
        update_kwargs = {"last_seen": timezone.now()}
        if display_name and user.display_name != display_name:
            update_kwargs["display_name"] = display_name
            user.display_name = display_name
        if chat_id is not None and user.chat_id != chat_id:
            update_kwargs["chat_id"] = chat_id
            user.chat_id = chat_id
        BotUser.objects.filter(pk=user.pk).update(**update_kwargs)
    return user, created


def greet_text(
    bot_user,
    *,
    is_new: bool,
    bookings_count: int = 0,
    nutrition_enabled: bool = False,
) -> str:
    """Текст приветствия с сегментацией по 3 группам (Phase 3 T07).

    Сегменты:
    - `is_new=True` → бот представляется (про массажный салон). Феатуру дневника
      пользователь увидит сам в главном меню.
    - `is_new=False` + `bookings_count >= 1` (returning client) → тёплое приветствие
      по имени + кросс-промо дневника если nutrition_enabled.
    - `is_new=False` + `bookings_count == 0` (молчун) → подталкивание через дневник
      как низкобарьерный entry point если nutrition_enabled, иначе обычный
      `GREETING_RETURNING`.

    `nutrition_enabled` — feature flag (`settings.NUTRITION_ENABLED`). Если выключен
    — приветствие не упоминает дневник, чтобы не пообещать чего бот не сделает.
    """
    if is_new:
        return texts.GREETING_NEW_USER

    name = bot_user.client_name or bot_user.display_name

    # Returning client: есть успешные записи через бот
    if bookings_count >= 1:
        if nutrition_enabled and name:
            return texts.GREETING_RETURNING_CLIENT_WITH_DIARY.format(name=name)
        if name:
            return texts.GREETING_RETURNING.format(name=name)
        return texts.GREETING_NEW_USER

    # Молчун: bookings_count == 0
    if nutrition_enabled:
        return texts.GREETING_SILENT_USER_WITH_DIARY
    if name:
        return texts.GREETING_RETURNING.format(name=name)
    return texts.GREETING_NEW_USER


@sync_to_async
def update_context(bot_user_id: int, **updates) -> None:
    """Atomic merge updates в context dict (защита от race при concurrent webhook'ах)."""
    with transaction.atomic():
        user = BotUser.objects.select_for_update().get(pk=bot_user_id)
        user.context.update(updates)
        user.save(update_fields=["context", "last_seen"])


@sync_to_async
def append_to_context(bot_user_id: int, key: str, value) -> None:
    """Atomic append value в список под ключом key. Без дублей."""
    with transaction.atomic():
        user = BotUser.objects.select_for_update().get(pk=bot_user_id)
        lst = user.context.setdefault(key, [])
        if value not in lst:
            lst.append(value)
            user.save(update_fields=["context", "last_seen"])


@sync_to_async
def get_client_history(bot_user) -> dict:
    """Phase 2.4 T05 — история клиента для consultative AI Concierge.

    Возвращает {
        "bookings_count": int — успешных записей через бот,
        "last_visits": list[dict] — последние 3 визита (date, master, service),
    }.

    Использует BookingRequest.is_processed=True как сигнал успешной записи
    (для bot_max source — это значит yclients_record_id создан).
    """
    from services_app.models import BookingRequest

    qs = BookingRequest.objects.filter(
        bot_user=bot_user, is_processed=True,
    ).order_by("-created_at")
    bookings_count = qs.count()

    last_visits = []
    for br in qs[:3]:
        last_visits.append({
            "date": br.created_at.strftime("%d.%m.%Y") if br.created_at else "",
            "master": br.master_name or "—",
            "service": br.service_name or "—",
        })

    return {"bookings_count": bookings_count, "last_visits": last_visits}
