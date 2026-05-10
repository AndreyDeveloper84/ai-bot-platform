"""AI-помощник handler — свободный текст → AI Concierge (Phase 2.3 §T10).

Триггеры:
1. Кнопка «💬 Задать вопрос» в главном меню → state `awaiting_question`
2. Любой text-message без активного booking-state (вытесняет старый fallback)

Pipeline (T10):
1. send_action TYPING_ON (typing-индикатор пока LLM думает)
2. context.clear() — диалог one-shot со state-стороны (FSM не нужен,
   AI Concierge сам управляет history через Conversation+Message)
3. intent-router → если phatic (привет/спасибо/как дела) → canned response,
   AI Concierge не зовётся (fast path, $0 OpenAI)
4. get_or_create_bot_user → нужен для AI Concierge + menu_state
5. ai_concierge.send_message(bot_user, message_text) → ChatResponseDTO
6. if dto.action_type → render_action() → (text, action_attachments)
7. else → text=dto.content, action_attachments=[]
8. is_giveup(content) AND action_type=None → BotInquiry + Telegram + canned text
9. send_with_main_menu(text, extra_attachments=action_attachments)

Backward-compat: chat_rag и response_cache остаются в llm.py / response_cache.py
для warmup (10 hot questions, отдельный preload-пайплайн).
"""
from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from maxapi import F, Router
from maxapi.context.context import MemoryContext
from maxapi.enums.sender_action import SenderAction
from maxapi.types import MessageCallback, MessageCreated

from maxbot import ai_concierge, ai_ui, keyboards, texts
from maxbot.ai_action_service import close_active_conversation
from maxbot.food_drink_hints import looks_like_food_drink
from maxbot.handlers.health_screening import start_health_screening
from maxbot.handlers.water import try_handle_water_text
from maxbot.intents import detect_intent
from maxbot.keyboards import food_drink_clarify_keyboard
from maxbot.llm import LLM_GIVEUP_MESSAGE, is_giveup
from maxbot.menu_state import send_with_main_menu
from maxbot.personalization import get_or_create_bot_user
from maxbot.states import AskStates
from maxbot.tier_b_triggers import detect_health_signal
from notifications import send_notification_telegram
from services_app.models import BotInquiry, Conversation


logger = logging.getLogger("maxbot.ai")
router = Router()


# ─── Кнопка «Задать вопрос» — переход в state awaiting_question ────────────


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_MENU_ASK)
async def on_ask_button(callback: MessageCallback, context: MemoryContext) -> None:
    """Клик «💬 Задать вопрос» в главном меню → ждём текст."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await context.set_state(AskStates.awaiting_question)
    await callback.bot.send_message(chat_id=chat_id, text=texts.AI_ASK_PROMPT)


@router.message_callback(F.callback.payload == keyboards.PAYLOAD_MENU_MY_BOOKINGS)
async def on_my_bookings_button(callback: MessageCallback, context: MemoryContext) -> None:
    """Клик «📋 Мои записи» → AI Concierge с pseudo-text «покажи мои записи»."""
    chat_id = callback.message.recipient.chat_id if callback.message else None
    if chat_id is None:
        return
    await context.clear()
    user = callback.callback.user
    bot_user, _ = await get_or_create_bot_user(user.user_id, user.full_name, chat_id=chat_id)
    await run_ai_turn(
        bot=callback.bot, chat_id=chat_id, bot_user=bot_user,
        user_text="Покажи мои записи (предстоящие и прошедшие)",
        original_user_text="cb:menu:my_bookings",
    )


# ─── Free-text → AI Concierge ──────────────────────────────────────────────


@router.message_created()
async def on_free_text(event: MessageCreated, context: MemoryContext) -> None:
    if event.message.sender is None:
        return  # системные

    chat_id = event.message.recipient.chat_id
    user_text = (event.message.body.text or "").strip() if event.message.body else ""
    if not user_text:
        return

    # Phase 3.1 Part 2D.1: попытка обработать как water entry ДО AI.
    # Если parse_beverage hit → try_handle_water_text возвращает True,
    # AI не вызывается (water-handler владеет всем UX-flow).
    if await try_handle_water_text(event, context):
        return

    # DRF-358: friendly food/drink fallback. Когда text похож на еду/напиток
    # но parse_beverage его не поймал — рендерим clarification card вместо
    # передачи в AI Concierge (где юзер получает корпоративный «не могу
    # с заказом» ответ — диалог 2026-05-08 09:10/09:11/09:11:47).
    # Gated на NUTRITION_ENABLED — на salon-only mode card неприменим.
    if (
        getattr(django_settings, "NUTRITION_ENABLED", False)
        and looks_like_food_drink(user_text)
    ):
        await event.bot.send_message(
            chat_id=chat_id,
            text=(
                "😄 Я не курьер из доставки! Но если ты хотел записать "
                f"«{user_text}» в дневник питания — могу занести."
            ),
            attachments=[food_drink_clarify_keyboard()],
        )
        return

    # 1. MARK_SEEN + TYPING_ON (best-effort, порядок важен — typing должен
    # быть последним active sender_action чтобы UI показал «печатает»)
    try:
        await event.bot.send_action(chat_id=chat_id, action=SenderAction.MARK_SEEN)
    except Exception as exc:  # noqa: BLE001
        logger.debug("send_action MARK_SEEN failed: %s", exc)
    try:
        await event.bot.send_action(chat_id=chat_id, action=SenderAction.TYPING_ON)
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_action TYPING_ON failed: %s", exc)

    sender = event.message.sender
    bot_user, _ = await get_or_create_bot_user(sender.user_id, sender.full_name, chat_id=chat_id)

    # Phase 3.2A T08: TIER-B health-signal pre-hook (ДО context.clear/intent —
    # screening flow владеет state самостоятельно через
    # NutritionAnketaStates.awaiting_health_consent). Если текст содержит
    # сигнал (беременность, диабет, гипертония, ED…) И клиент ещё не прошёл
    # и не отказался от screening'а — переключаемся на screening вместо AI.
    #
    # NUTRITION_ENABLED gate (PR #135 pre-flight fix): screening upsert'ит
    # профиль в Ayla на финале. Без AYLA_BASE_URL это ValueError, которого
    # `_complete_tier_b` не ловит. Если nutrition выключен на проде — skip
    # pre-hook полностью, AI ответит как обычно.
    if getattr(django_settings, "NUTRITION_ENABLED", False):
        text_body = (event.message.body.text or "") if event.message.body else ""
        signal = detect_health_signal(text_body)
        if signal is not None:
            flags = bot_user.health_flags or {}
            already_consented = bool(flags.get("health_consent_acked_at"))
            already_declined = bool(flags.get("health_consent_declined_at"))
            if not already_consented and not already_declined:
                logger.info(
                    "ai_assistant: TIER-B health signal=%s user_id=%s — starting screening",
                    signal, sender.user_id,
                )
                await start_health_screening(
                    bot=event.bot, chat_id=chat_id, context=context,
                )
                return

    # 2. Чистим state — AI Concierge управляет multi-turn через Conversation.
    await context.clear()

    # 3. Intent-router (regex, ~1ms): phatic phrases → canned, без OpenAI
    intent_response = detect_intent(user_text)
    if intent_response is not None:
        logger.info("ai_assistant: INTENT MATCH user_id=%s text=%r",
                    sender.user_id, user_text[:60])
        await send_with_main_menu(
            bot=event.bot, chat_id=chat_id,
            text=intent_response, bot_user=bot_user,
        )
        return

    # 4-7. Прогон через AI Concierge + рендер + отправка
    await _invoke_ai_concierge(
        bot=event.bot, chat_id=chat_id,
        bot_user=bot_user, user_text=user_text,
        original_user_text=user_text,
    )


async def _invoke_ai_concierge(
    *,
    bot,
    chat_id: int,
    bot_user,
    user_text: str,
    original_user_text: str | None = None,
) -> None:
    """Thin wrapper around run_ai_turn — extraction point for tests/pre-hooks.

    Phase 3.2A T08: tests monkeypatch this symbol to assert pre-hook routing.
    Production-side it's a 1-line passthrough to run_ai_turn so we don't
    duplicate AI Concierge logic. Future: cross-cutting concerns (degraded
    advice_mode injection, etc.) могут жить здесь.
    """
    await run_ai_turn(
        bot=bot, chat_id=chat_id, bot_user=bot_user,
        user_text=user_text, original_user_text=original_user_text,
    )


# ─── DRF-354 master photos resolver (used for show_masters card) ──────────


async def _resolve_master_attachments(bot, action_data: dict) -> dict[int, object]:
    """Async-fetch master photos для show_masters.action_data.masters.

    Возвращает {master_id: Attachment} (только non-None). Кэш в Redis,
    повторные показы тех же мастеров — без upload-round-trip.

    Best-effort: любая ошибка загрузки одного фото → пропускаем мастера,
    остальные карточки рендерятся нормально (см. master_image.get_master_attachment).
    """
    from maxbot.master_image import get_master_attachment
    from services_app.models import Master

    masters_items = action_data.get("masters") or []
    master_ids = [
        item.get("master", {}).get("id")
        for item in masters_items
        if item.get("master", {}).get("id") is not None
    ]
    if not master_ids:
        return {}

    masters_qs = await sync_to_async(list)(
        Master.objects.filter(id__in=master_ids, is_active=True)
    )
    masters_by_id = {m.id: m for m in masters_qs}

    result: dict[int, object] = {}
    # Резолвим по одному (не gather), чтобы один failing upload не убил
    # остальные через future.cancellation. Latency не критична — кэш hot.
    for mid in master_ids:
        master_obj = masters_by_id.get(mid)
        if master_obj is None:
            continue
        try:
            att = await get_master_attachment(bot, master_obj)
        except Exception as exc:  # noqa: BLE001
            logger.warning("master photo resolve failed master=%s: %s", mid, exc)
            continue
        if att is not None:
            result[mid] = att
    return result


# ─── Public helper — переиспользуется в ai_callbacks.py ────────────────────


async def run_ai_turn(
    *,
    bot,
    chat_id: int,
    bot_user,
    user_text: str,
    original_user_text: str | None = None,
) -> None:
    """Один turn AI Concierge: AI Concierge → render → send_with_main_menu.

    Используется и в on_free_text (свободный ввод), и в callback'ах
    cb:ai:pick_master/pick_slot/answer — там pseudo-user-text формируется
    из выбора клиента и передаётся в AI как продолжение диалога.

    original_user_text — что записать в BotInquiry если AI упал. Для
    callback'ов это краткое описание выбора, для free-text — то же что
    user_text.
    """
    inquiry_question = original_user_text or user_text

    try:
        dto = await ai_concierge.send_message(
            bot_user=bot_user, message_text=user_text,
        )
    except Exception:  # noqa: BLE001
        logger.exception("ai_assistant: AI Concierge crashed text=%r",
                         user_text[:80])
        await close_active_conversation(bot_user, outcome=Conversation.Outcome.ERROR.value)
        await _create_bot_inquiry(
            user_id=bot_user.max_user_id,
            full_name=bot_user.display_name or "",
            chat_id=chat_id, question=inquiry_question,
        )
        await send_with_main_menu(
            bot=bot, chat_id=chat_id,
            text=texts.AI_FORWARDED_TO_MANAGER, bot_user=bot_user,
        )
        return

    if dto.action_type:
        # DRF-354: для show_masters resolve photo attachments (top-N мастеров).
        # Pre-async fetch + cache, чтобы render_action остался sync.
        master_attachments = None
        if dto.action_type == "show_masters":
            master_attachments = await _resolve_master_attachments(
                bot, dto.action_data or {},
            )
        rendered_text, action_attachments = ai_ui.render_action(
            conversation_id=str(dto.conversation_id),
            action_type=dto.action_type,
            action_data=dto.action_data or {},
            master_attachments=master_attachments,
        )
        # Преамбула от LLM перед tool_call (например «Конечно, давайте
        # подберём!») — клеим к карточке, иначе бот звучит как робот.
        preamble = (dto.content or "").strip()
        if preamble:
            text = f"{preamble}\n\n{rendered_text}"
        else:
            text = rendered_text
    elif ai_concierge._looks_like_promise_without_tool(dto.content):
        # Last-resort safety net: ai_concierge.send_message сделал retry с
        # tool_choice="required" но даже тогда LLM не выдал валидный tool
        # (или retry упал). Клиент получит content + явное приглашение
        # выбрать из меню вместо тишины.
        logger.warning(
            "ai_assistant: promise-without-action AFTER retry, fallback to main menu. "
            "content=%r", (dto.content or "")[:120],
        )
        text = (dto.content or "").strip() + (
            "\n\n(Уточните, что именно подобрать — выберите из меню или "
            "опишите подробнее.)"
        )
        action_attachments = []
    else:
        text = dto.content
        action_attachments = []

    # Сохраняем итоговый текст в Message.rendered_text — для admin-аудита
    # (особенно важно для tool-call: content="" а карточки видны только в текст'е).
    if dto.assistant_message_id is not None:
        try:
            await ai_concierge.update_message_rendered_text(
                dto.assistant_message_id, text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("update_message_rendered_text failed: %s", exc)

    if dto.action_type is None and is_giveup(text):
        await close_active_conversation(bot_user, outcome=Conversation.Outcome.REDIRECTED.value)
        await _create_bot_inquiry(
            user_id=bot_user.max_user_id,
            full_name=bot_user.display_name or "",
            chat_id=chat_id, question=inquiry_question,
        )
        await send_with_main_menu(
            bot=bot, chat_id=chat_id,
            text=texts.AI_FORWARDED_TO_MANAGER, bot_user=bot_user,
        )
        return

    # MAX API ограничение: только ОДИН inline_keyboard на сообщение.
    # Если у action есть свои кнопки (карточка с CallbackButton'ами) — отправляем
    # БЕЗ main_menu (оно «плавает» на предыдущем сообщении). Если action без
    # кнопок (show_my_bookings empty, ask_clarification без options) — приклеиваем
    # main_menu для удобства навигации.
    has_card_keyboard = any(
        getattr(att, "type", None) == "inline_keyboard"
        for att in action_attachments
    )
    if has_card_keyboard:
        await bot.send_message(
            chat_id=chat_id, text=text, attachments=action_attachments,
        )
    else:
        await send_with_main_menu(
            bot=bot, chat_id=chat_id, text=text, bot_user=bot_user,
        )


# ─── Helpers ────────────────────────────────────────────────────────────────


async def _create_bot_inquiry(*, user_id: int, full_name: str, chat_id: int,
                              question: str) -> None:
    """Создаём BotInquiry для менеджера + Telegram-алерт."""
    bot_user, _ = await get_or_create_bot_user(user_id, full_name, chat_id=chat_id)
    inquiry = await sync_to_async(BotInquiry.objects.create)(
        bot_user=bot_user,
        chat_id=chat_id,
        question=question,
    )
    await sync_to_async(send_notification_telegram)(
        f"🤖 Вопрос для менеджера от MAX-бота\n\n"
        f"👤 {bot_user.client_name or bot_user.display_name or f'#{user_id}'}\n"
        f"❓ {question}\n\n"
        f"Ответить: /admin/services_app/botinquiry/{inquiry.id}/change/"
    )
