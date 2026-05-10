"""Handler FAQ — список вопросов из HelpArticle + ответ.

Сценарий:
- cb:menu:faq    → on_show_faq         → клавиатура HelpArticle.objects.active()
- cb:faq:{id}    → on_show_faq_answer  → ответ + кнопка Назад
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.types import MessageCallback

from maxbot import keyboards
from maxbot.personalization import append_to_context, get_or_create_bot_user
from services_app.models import HelpArticle


logger = logging.getLogger("maxbot.faq")
router = Router()


@sync_to_async
def _list_active_articles() -> list[HelpArticle]:
    return list(HelpArticle.objects.active().order_by("order", "id"))


@sync_to_async
def _get_article(article_id: int) -> HelpArticle | None:
    return HelpArticle.objects.active().filter(id=article_id).first()


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_MENU_FAQ)
async def on_show_faq(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    articles = await _list_active_articles()
    if articles:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Выберите вопрос — отвечу сразу:",
            attachments=[keyboards.faq_keyboard(articles)],
        )
    else:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Раздел вопросов пока пуст. Если что — звоните по телефону из «Контактов».",
            attachments=[keyboards.back_to_menu_keyboard()],
        )


@router.message_callback(F.callback.payload.startswith(keyboards.PAYLOAD_FAQ_PREFIX))
async def on_show_faq_answer(callback: MessageCallback, context: MemoryContext) -> None:
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return

    payload = callback.callback.payload or ""
    raw_id = payload[len(keyboards.PAYLOAD_FAQ_PREFIX):]
    try:
        article_id = int(raw_id)
    except ValueError:
        logger.warning("on_show_faq_answer: некорректный payload %r", payload)
        return

    article = await _get_article(article_id)
    if article is None:
        await callback.bot.send_message(
            chat_id=chat_id,
            text="Этот вопрос больше недоступен. Выберите другой из меню.",
            attachments=[keyboards.back_to_menu_keyboard()],
        )
        return

    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name)
    await append_to_context(bot_user.id, "faqs_viewed", article.id)

    await callback.bot.send_message(
        chat_id=chat_id,
        text=article.answer,
        attachments=[keyboards.back_to_menu_keyboard()],
    )
