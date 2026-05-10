"""Phase 3.2B T09: mute callback handlers."""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback

from maxbot.nudges.mute import apply_mute
from maxbot.personalization import get_or_create_bot_user


logger = logging.getLogger("maxbot.nudges.mute_handlers")
router = Router()


def _parse_kind(payload: str | None, prefix: str) -> str | None:
    if not payload or not payload.startswith(prefix):
        return None
    return payload[len(prefix):] or None


@router.message_callback(F.callback.payload.startswith("cb:nudge:mute:off:"))
async def on_nudge_mute_off(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    kind = _parse_kind(callback.callback.payload, "cb:nudge:mute:off:")
    if kind is None:
        return
    bot_user, _ = await get_or_create_bot_user(
        callback.callback.user.user_id, callback.callback.user.full_name,
    )
    await sync_to_async(apply_mute)(
        bot_user, kind=kind, mode="off", reason="user_explicit_off",
    )
    await callback.bot.send_message(
        chat_id=chat_id, text="🔕 Поняла, такое больше не покажу.",
    )


@router.message_callback(F.callback.payload.startswith("cb:nudge:mute:less:"))
async def on_nudge_mute_less_often(
    callback: MessageCallback, context: MemoryContext,
) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None or callback.callback.user is None:
        return
    kind = _parse_kind(callback.callback.payload, "cb:nudge:mute:less:")
    if kind is None:
        return
    bot_user, _ = await get_or_create_bot_user(
        callback.callback.user.user_id, callback.callback.user.full_name,
    )
    await sync_to_async(apply_mute)(
        bot_user, kind=kind, mode="less_often", reason="user_explicit_less",
    )
    await callback.bot.send_message(
        chat_id=chat_id, text="Хорошо, буду пореже.",
    )
