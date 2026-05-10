"""Управление состоянием главного меню — «плавающее меню» (Вариант B).

Цель: меню всегда висит у самого свежего бот-сообщения, никогда не
дублируется в чате. При отправке нового сообщения с главным меню —
старое меню снимается через `edit_message(attachments=[])`.

Порядок (минимизирует визуальное «моргание»):
1. **send_message** новое (с меню) — клиент мгновенно видит ответ внизу
2. **gather(edit_prev, save_state)** — edit убирает меню сверху ВНЕ фокуса
   клиента, save_state пишет в Postgres. Два non-dependent op'а параллельно.

Раньше делали edit → send последовательно — между ними было 300-500ms
без меню вообще, глаз ловил «пусто». Теперь меню всегда видно (либо у
prev, либо у new), edit отрабатывает «в фон».

State хранится в `BotUser.context["last_main_menu_msg_id"]` +
`["last_main_menu_msg_text"]` (нужен текст т.к. edit_message требует text).

Race conditions: при concurrent сообщениях от одного user'а две handler'ы
могут одновременно прочитать тот же `last_main_menu_msg_id` и обе
попытаться edit + send. Edge case даёт максимум один лишний дубль
(редко). Не используем DB lock из-за async-вызовов внутри транзакции —
оставляем lockless для простоты, реальная нагрузка ~100 msg/day.

НЕ для контекстных клавиатур (categories/services/faq/booking-confirm) —
они быстро сменяются и tracking их через state — overkill.
"""
from __future__ import annotations

import asyncio
import logging

from asgiref.sync import sync_to_async
from django.db import transaction

from maxbot import keyboards
from services_app.models import BotUser


logger = logging.getLogger("maxbot.menu")

CTX_LAST_MENU_MID = "last_main_menu_msg_id"
CTX_LAST_MENU_TEXT = "last_main_menu_msg_text"


async def _edit_prev_safe(bot, prev_mid: str | None, prev_text: str | None) -> None:
    """Best-effort снятие меню с prev. Exception поглощаем (msg удалён/old/etc)."""
    if not prev_mid or not prev_text:
        return
    try:
        await bot.edit_message(
            message_id=prev_mid,
            text=prev_text,
            attachments=[],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("edit_message(prev menu) failed: %s", exc)


@sync_to_async
def _save_menu_state(bot_user_id: int, msg_id: str, text: str) -> None:
    """Atomic update двух полей контекста — защита от race с другими update_context."""
    with transaction.atomic():
        user = BotUser.objects.select_for_update().get(pk=bot_user_id)
        user.context[CTX_LAST_MENU_MID] = msg_id
        user.context[CTX_LAST_MENU_TEXT] = text
        user.save(update_fields=["context", "last_seen"])


async def send_with_main_menu(
    *,
    bot,
    chat_id: int,
    text: str,
    bot_user: BotUser,
    extra_attachments: list | None = None,
) -> None:
    """Отправить сообщение с главным меню, сняв меню с предыдущего бот-ответа.

    Шаги (порядок важен для UX — минимизирует «моргание»):
    1. send_message новое (с меню) — клиент мгновенно видит ответ
    2. Если send упал — exception вверх, state не трогаем
    3. gather(edit prev attachments=[], save state) — параллельно

    extra_attachments: например welcome-картинка для нового user'а — пойдёт
    ПЕРЕД меню (отображается над клавиатурой). Если None — только меню.
    """
    prev_mid = bot_user.context.get(CTX_LAST_MENU_MID) if bot_user.context else None
    prev_text = bot_user.context.get(CTX_LAST_MENU_TEXT) if bot_user.context else None

    # 1. Отправить новое сообщение с меню СНАЧАЛА (юзер сразу видит ответ
    # внизу чата, фокус там). Если send упадёт — RuntimeError вверх,
    # старое меню НЕ трогаем (state preserved для retry).
    attachments = list(extra_attachments) if extra_attachments else []
    attachments.append(keyboards.main_menu_keyboard(bot_user=bot_user))
    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        attachments=attachments,
    )

    new_mid = _extract_message_id(sent)
    if new_mid is None:
        logger.warning("send_message returned without mid; menu state not updated "
                       "(prev menu remains, will be cleaned next turn)")
        return

    # 2. Параллельно: edit_prev + save_state. Edit идёт ВЫШЕ в чате (вне
    # фокуса), save_state — DB запись. Оба независимые ~100ms — gather
    # экономит ~100ms vs sequential.
    await asyncio.gather(
        _edit_prev_safe(bot, prev_mid, prev_text),
        _save_menu_state(bot_user.id, new_mid, text),
    )

    # Sync in-memory копию для caller'а (на случай если bot_user используется дальше)
    if bot_user.context is None:
        bot_user.context = {}
    bot_user.context[CTX_LAST_MENU_MID] = new_mid
    bot_user.context[CTX_LAST_MENU_TEXT] = text


def _extract_message_id(sent) -> str | None:
    """Достаёт mid из ответа send_message. SDK обёртка типа SendedMessage.

    Возвращает только если mid — реальная строка, иначе None. Защита от
    тестовых MagicMock'ов, у которых getattr(body, "mid") возвращает не None.
    """
    if sent is None:
        return None
    for path in (("body", "mid"), ("message", "body", "mid")):
        node = sent
        for attr in path:
            node = getattr(node, attr, None)
            if node is None:
                break
        if isinstance(node, str) and node:
            return node
    return None
