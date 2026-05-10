"""Fallback handler — любой текст БЕЗ активного FSM-state ведёт в главное меню.

Регистрируется ПОСЛЕДНИМ в get_routers() — все более специфичные handler'ы
(BookingStates.awaiting_*, /start, callback'и) уже отработали к этому моменту.
"""
from __future__ import annotations

import logging

from maxapi import Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCreated

from maxbot import texts
from maxbot.menu_state import send_with_main_menu
from maxbot.personalization import get_or_create_bot_user


logger = logging.getLogger("maxbot.fallback")
router = Router()


@router.message_created()
async def on_fallback(event: MessageCreated, context: MemoryContext) -> None:
    """Безусловный fallback — все text-message без других matching handler'ов."""
    if event.message.sender is None:
        return  # системные сообщения

    sender = event.message.sender
    chat_id = event.message.recipient.chat_id
    bot_user, _ = await get_or_create_bot_user(sender.user_id, sender.full_name)
    await send_with_main_menu(
        bot=event.bot, chat_id=chat_id,
        text=texts.FALLBACK_UNKNOWN_INPUT, bot_user=bot_user,
    )
