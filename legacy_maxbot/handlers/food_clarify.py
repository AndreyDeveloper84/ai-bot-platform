"""DRF-358 T02: food/drink clarification card callbacks.

Two handlers for the inline card shown когда text похож на еду/напиток
но parse_beverage не распознал.

«📝 В дневник» → bot shows: «Скинь фото блюда — посчитаю калории!». Text
food log не реализован (это EPIC-Q Q-4 backlog), photo scanner — текущий
способ.

«Это была опечатка» → silent soft ack — минимально, чтоб не утомлять.
"""
from __future__ import annotations

import logging

from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback

from maxbot import keyboards
from maxbot.personalization import get_or_create_bot_user


logger = logging.getLogger("maxbot.food_clarify")
router = Router()


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_FOOD_TO_DIARY)
async def on_food_to_diary(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """[📝 В дневник] → redirect на photo scanner."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    user_id = callback.callback.user.user_id
    full_name = callback.callback.user.full_name
    await get_or_create_bot_user(user_id, full_name, chat_id=chat_id)
    await callback.bot.send_message(
        chat_id=chat_id,
        text=(
            "📸 Скинь фото блюда — посчитаю калории и БЖУ. "
            "Через текст пока не умею распознавать (это в работе)."
        ),
    )


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_FOOD_TYPO)
async def on_food_typo(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    """[Это была опечатка] → silent soft ack."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    user_id = callback.callback.user.user_id
    full_name = callback.callback.user.full_name
    await get_or_create_bot_user(user_id, full_name, chat_id=chat_id)
    await callback.bot.send_message(
        chat_id=chat_id,
        text="Поняла 🙂",
    )
