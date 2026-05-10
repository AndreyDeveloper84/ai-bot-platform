"""Phase 3 T01/T04: Nutrition entry — экран приветствия дневника питания.

Точка входа: callback `cb:menu:nutrition` из main_menu_keyboard().

Flow:
1. Пользователь жмёт «🍎 Дневник питания» в главном меню.
2. Этот handler шлёт welcome screen с двумя кнопками:
   - «📸 Попробовать сразу» (T03/T07 — заглушка пока)
   - «📝 Настроить под себя (30 сек)» (T04 анкета — реализована)
3. on_start_anketa устанавливает state=awaiting_consent и шлёт дисклеймер 152-ФЗ.
   Остальные шаги FSM анкеты — в handlers/nutrition_anketa.py (Phase 3.1 Part 1).

Это маленький router потому что entry-screen самодостаточен и не хочется
смешивать его с food_scanner (там callback'и `cb:nutrition:consent:*` и
`cb:nutrition:log:*` уже жирные).
"""
from __future__ import annotations

import logging

from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback

from maxbot import keyboards
from maxbot.states import NutritionAnketaStates


logger = logging.getLogger("maxbot.handlers.nutrition_entry")

router = Router()


WELCOME_TEXT = (
    "\U0001f34e Дневник питания\n\n"
    "Сфоткай еду — скажу калории, БЖУ "
    "и подскажу как сбалансировать.\n\n"
    "Чтобы считать норму под тебя "
    "(а не средние 2000 ккал) — ответь "
    "на 4 вопроса, 30 секунд."
)


STUB_TRY_NOW_TEXT = (
    "\U0001f4f8 Скоро добавим — пока в разработке.\n\n"
    "Сейчас можно записать фото блюда — я распознаю и посчитаю "
    "калории. Просто пришли фото в чат."
)

RESUME_TEXT = (
    "\U0001f34e Дневник питания\n\n"
    "Дневник уже настроен ✓\n\n"
    "\U0001f4f8 Пришли фото блюда — посчитаю калории.\n"
    "\U0001f4a7 Напиши «вода» или «выпила кофе» — добавлю.\n"
    "\U0001f4ca Команда /день покажет сегодняшние итоги."
)


async def _resolve_bot_user_for_entry(callback):
    """Lazy-load BotUser для проверки nutrition_onboarded_at.

    `get_or_create_bot_user` возвращает кортеж `(BotUser, created)` —
    распаковываем и отдаём только BotUser. Та же ошибка ранее ловилась
    в `nutrition_anketa._resolve_bot_user` (Task 16 fix); тут вторая
    копия helper'а осталась с багом потому что unit-тесты T14
    monkeypatch'или весь helper, не ходили в реальную ORM.
    """
    from maxbot.personalization import get_or_create_bot_user

    sender_id = callback.callback.user.user_id
    bot_user, _ = await get_or_create_bot_user(sender_id)
    return bot_user


COMING_SOON_TEXT = (
    "🍎 Дневник питания\n\n"
    "Скоро будет — фича в разработке. Когда подключим, "
    "посчитаем калории по фото блюда и подберём норму под тебя.\n\n"
    "Пока можешь записаться на массаж или задать вопрос через меню."
)


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_MENU_NUTRITION)
async def on_show_nutrition_welcome(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """Главный entry-screen дневника. Если юзер уже прошёл анкету —
    показываем resume вместо welcome (Design Doc v2 §4.1 учёт history).

    Feature flag NUTRITION_ENABLED: если выключен (default), показываем
    «Скоро будет» — даже если юзер кликнул старую inline-клаву из
    истории чата где кнопка ещё была видна.
    """
    from django.conf import settings

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    if not getattr(settings, "NUTRITION_ENABLED", False):
        await callback.bot.send_message(
            chat_id=chat_id,
            text=COMING_SOON_TEXT,
        )
        return

    bot_user = await _resolve_bot_user_for_entry(callback)
    if bot_user.nutrition_onboarded_at is not None:
        # B24: attach «← Назад в меню» — RESUME-экран без kb был тупиком,
        # юзеры жаловались что некуда вернуться.
        await callback.bot.send_message(
            chat_id=chat_id,
            text=RESUME_TEXT,
            attachments=[keyboards.back_to_menu_keyboard()],
        )
        return

    # Новый юзер — welcome с 2 кнопками
    await callback.bot.send_message(
        chat_id=chat_id,
        text=WELCOME_TEXT,
        attachments=[keyboards.nutrition_welcome_keyboard()],
    )


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_NUTRITION_TRY_NOW)
async def on_try_now_stub(callback: MessageCallback, context: MemoryContext) -> None:
    """Заглушка для «Попробовать сразу» до завершения T03 (food scanner refactor)."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await callback.bot.send_message(chat_id=chat_id, text=STUB_TRY_NOW_TEXT)


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_NUTRITION_START_ANKETA)
async def on_start_anketa(callback: MessageCallback, context: MemoryContext) -> None:
    """Запуск TIER-A анкеты — set state и шлём consent-экран."""
    from maxbot.handlers.nutrition_anketa import CONSENT_TEXT

    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    await context.set_state(NutritionAnketaStates.awaiting_consent)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=CONSENT_TEXT,
        attachments=[keyboards.anketa_consent_keyboard()],
    )
