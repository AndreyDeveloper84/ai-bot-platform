"""AIConcierge — главный chat-pipeline (заменит chat_rag в ai_assistant.py).

Phase 2.3 §T06. Адаптация Ayla/djangoproject/ai/application/services/chat_service.py.

Pipeline на каждый user-message:
1. _resolve_conversation(bot_user) → existing active OR create new
2. save Message(role=user) + получить msg_id (для exclude_id в истории)
3. build_master_context() → MasterContext (Top-N мастеров для anti-hallucination)
4. _load_recent_history(conv, exclude_id=user_msg.id, limit=10)
5. render_system_prompt(today, client_name, bookings_count, master_context)
6. _compose_messages(system, history, user_text) → [{role, content}, ...]
7. openai_client.chat.completions.create(tools=TOOL_DEFINITIONS) async
8. measure latency_ms
9. parse completion: content + (optional tool_call → dispatch_tool_call → action)
10. save Message(role=assistant, content, action_type, action_data, tool_call,
    tokens_in/out, latency_ms)
11. update conversation.last_message_at
12. return ChatResponseDTO

Не делает рендер UI (T07) и не пишет BookingRequest (T09) — только Conversation
и Messages в БД + DTO для caller'а (handler в ai_assistant.py).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.utils import timezone

from maxbot.ai_context import MasterContext, build_master_context as _default_build_master_context
from maxbot.ai_prompts import render_system_prompt
from maxbot.ai_tool_handlers import dispatch_tool_call
from maxbot.ai_tools import TOOL_DEFINITIONS
from services_app.models import BotUser, Conversation, Message


logger = logging.getLogger("maxbot.ai_concierge")

DEFAULT_HISTORY_LIMIT = 10


@dataclass(frozen=True)
class ChatResponseDTO:
    """Что AIConcierge возвращает caller'у (handler'у в ai_assistant.py)."""

    conversation_id: UUID
    content: str
    action_type: str | None
    action_data: dict[str, Any] | None
    # ID assistant-сообщения для post-update rendered_text (после render_action)
    assistant_message_id: UUID | None = None


@sync_to_async
def update_message_rendered_text(message_id: UUID, rendered_text: str) -> None:
    """Сохраняет в Message.rendered_text финальный текст что увидел клиент.

    Вызывается из ai_assistant.run_ai_turn после render_action — для
    tool-call сообщений `content` пустой, а человекочитаемая выдача (карточки
    мастеров, слотов, и т.д.) живёт только в rendered_text → admin-аудит
    конкретного сообщения становится наглядным.
    """
    Message.objects.filter(id=message_id).update(rendered_text=rendered_text)


# ─── ORM helpers (sync_to_async wrappers) ────────────────────────────────


@sync_to_async
def _resolve_conversation(bot_user: BotUser) -> Conversation:
    """Existing active OR create new. Один активный на bot_user."""
    existing = (
        Conversation.objects
        .filter(bot_user=bot_user, is_active=True)
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    return Conversation.objects.create(bot_user=bot_user, is_active=True)


@sync_to_async
def _save_message(
    conversation: Conversation,
    *,
    role: str,
    content: str,
    action_type: str = "",
    action_data: dict | None = None,
    tool_call: dict | None = None,
    tool_call_id: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int | None = None,
) -> Message:
    msg = Message.objects.create(
        conversation=conversation,
        role=role,
        content=content,
        action_type=action_type,
        action_data=action_data,
        tool_call=tool_call,
        tool_call_id=tool_call_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
    )
    # Conversation.last_message_at — обновляем для admin-listing'а
    conversation.last_message_at = msg.created_at
    conversation.save(update_fields=["last_message_at"])
    return msg


@sync_to_async
def _load_recent_history(
    conversation: Conversation,
    *,
    exclude_id: UUID | None = None,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[Message]:
    """Last N messages в хронологическом порядке (ASC) для LLM context.

    Берём last N (DESC), потом reverse — корректно сохраняет хронологию даже
    когда сообщений > N (старые вытесняются).
    """
    qs = Message.objects.filter(conversation=conversation)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    recent = list(qs.order_by("-created_at")[:limit])
    recent.reverse()
    return recent


# ─── LLM helpers ──────────────────────────────────────────────────────────


def _compose_messages(
    *,
    system_prompt: str,
    history: list[Message],
    user_text: str,
) -> list[dict[str, Any]]:
    """OpenAI-format messages: system + history + new user."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for h in history:
        # tool/system messages в истории — пропускаем (на текущей итерации
        # не передаём tool_call_id'ы обратно, многоступенчатый tool-use не
        # делаем; T06.5 если понадобится)
        if h.role not in ("user", "assistant"):
            continue
        messages.append({"role": h.role, "content": h.content or ""})
    messages.append({"role": "user", "content": user_text})
    return messages


_PROMISE_STEMS = (
    # «подберу/подбираю/подобрала/подбери...» — все варианты
    "подбер", "подбираю", "подбирает", "подобра",
    # «посмотрю/посмотрим/посмотрите...»
    "посмотр",
    # «гляну/поглядим...»
    "гляну", "погляд",
    # явный wait — без tool это всегда bug
    "подождит", "подожди", "минуточк", "минутк", "одну минут",
    "одну секунд", "секундочк",
    # «вот варианты», «вот кто подойдёт»
    "вот вариант", "вот кто",
    # «давайте уточним/подберём»
    "давайте уточн", "давай уточн", "давайте подбер", "давай подбер",
    # «уточню/найду/помогу выбрать»
    "уточню", "найду подходящ", "помогу выбрать", "помогу подобрать",
    # «записываю — проверьте»
    "записываю — проверьте", "записываю - проверьте",
    # «рассмотрим/рассмотрите»
    "рассмотр",
)


def _looks_like_promise_without_tool(content: str | None) -> bool:
    """Stem-based detect: assistant написал обещание-к-действию без tool_call.

    Используется в send_message для retry с tool_choice="required" и в
    handler ai_assistant для last-resort fallback. Stem-based — переживает
    разные формы глагола («подберу/подбираю/подобрал»).
    """
    if not content:
        return False
    text = content.lower().strip()
    return any(stem in text for stem in _PROMISE_STEMS)


async def _parse_completion(completion, master_context: MasterContext):
    """Returns (content, tool_call_raw, action_type, action_data).

    dispatch_tool_call синхронный и трогает Django ORM (recommend_services,
    confirm_booking). В async-контексте ORM raise'ит SynchronousOnlyOperation
    — оборачиваем в sync_to_async чтобы запросы шли в thread-executor.
    """
    msg = completion.choices[0].message
    tool_calls = getattr(msg, "tool_calls", None)
    content = msg.content or ""

    if not tool_calls:
        return content, None, None, None

    # Берём первый tool_call (наш flow одношаговый — LLM либо отвечает
    # текстом, либо одним tool-call'ом, не цепочкой)
    tc = tool_calls[0]
    result = await sync_to_async(dispatch_tool_call)(tc, master_context)

    # Сериализуем raw tool_call для аудита (str-arguments)
    raw = {
        "id": getattr(tc, "id", ""),
        "name": getattr(tc.function, "name", ""),
        "arguments": getattr(tc.function, "arguments", ""),
    }
    return content, raw, result.action_type, result.action_data


async def _fetch_deficit_hint(bot_user: BotUser) -> str:
    """Best-effort cross-domain hint fetch from Ayla nutrition (DRF-248).

    Returns empty string on any failure (network, circuit open, missing
    config). The bot must remain functional when Ayla is unreachable.
    """
    try:
        from maxbot.services.ayla_user_proxy import external_user_id_for
        from maxbot.services.nutrition_client import (
            NutritionAPIError, NutritionUnavailableError, get_nutrition_client,
        )
        client = get_nutrition_client()
    except Exception as exc:  # noqa: BLE001
        # Misconfigured env (no AYLA_BASE_URL / token) — silent skip.
        logger.debug("ai_concierge: deficit_hint disabled: %s", exc)
        return ""
    try:
        deficits = await client.weekly_deficits(
            external_user_id=external_user_id_for(bot_user),
        )
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.info("ai_concierge: deficit_hint skip user=%s reason=%s",
                    bot_user.max_user_id, exc)
        return ""
    except Exception:  # noqa: BLE001
        logger.exception("ai_concierge: deficit_hint unexpected error")
        return ""
    return deficits.hint or ""


# ─── Main entry point ─────────────────────────────────────────────────────


async def send_message(
    *,
    bot_user: BotUser,
    message_text: str,
    openai_client=None,
    context_builder=None,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> ChatResponseDTO:
    """Один turn AI Concierge: user message → assistant response с opt action.

    DI:
    - openai_client: AsyncOpenAI — default из get_async_openai_client()
    - context_builder: callable() → MasterContext — default ai_context.build_master_context

    Caller (handler ai_assistant.py / T10) ловит exception от openai_client
    и graceful'но реагирует (LLM_GIVEUP_MESSAGE → BotInquiry, как сейчас).
    """
    # 1. Resolve conversation
    conversation = await _resolve_conversation(bot_user)

    # 2. Save user message (получим id для exclude из history)
    user_msg = await _save_message(
        conversation,
        role=Message.Role.USER,
        content=message_text,
    )

    # 3. Build context (Top-N мастеров с реальными ID)
    builder = context_builder or _default_build_master_context
    master_context = await sync_to_async(builder)()

    # 4. Load recent history (не включая только что сохранённое user message)
    history = await _load_recent_history(
        conversation, exclude_id=user_msg.id, limit=history_limit,
    )

    # 5. Render system prompt + client history (T05) + cross-domain hint (DRF-248)
    from maxbot.personalization import get_client_history
    history_data = await get_client_history(bot_user)
    bookings_count = history_data.get("bookings_count", 0)
    last_visits = history_data.get("last_visits", [])

    # DRF-248: best-effort fetch of nutrition deficit hint. Failures are
    # silently ignored — bot must work even if Ayla nutrition endpoint is
    # down or misconfigured. Empty hint is the common case (no signal yet).
    extra_hint = await _fetch_deficit_hint(bot_user)

    # Phase 3.2A T08: degraded-advice mode для пользователей без TIER-B consent.
    # Не блокирует общение — просто запрещает LLM давать персональные nutrition-
    # советы пока screening не пройден. Если acked → "full" (нормальный prompt).
    health_flags = bot_user.health_flags or {}
    advice_mode = (
        "full" if health_flags.get("health_consent_acked_at") else "degraded"
    )

    system_prompt = render_system_prompt(
        today=timezone.localdate(),
        client_name=bot_user.client_name or bot_user.display_name or "",
        bookings_count=bookings_count,
        master_context=master_context,
        last_visits=last_visits,
        extra_hint=extra_hint,
        advice_mode=advice_mode,
    )

    # 6. Compose for OpenAI
    llm_messages = _compose_messages(
        system_prompt=system_prompt,
        history=history,
        user_text=message_text,
    )

    # 7. Call OpenAI с tools
    if openai_client is None:
        from maxbot.llm import get_async_openai_client
        openai_client = get_async_openai_client()

    started = time.monotonic()
    completion = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=llm_messages,
        tools=TOOL_DEFINITIONS,
    )
    latency_ms = int((time.monotonic() - started) * 1000)

    # 8. Parse: content + opt action (через dispatch_tool_call)
    content, tool_call_raw, action_type, action_data = await _parse_completion(
        completion, master_context,
    )

    # 8.1 Retry с tool_choice="required" если LLM написал promise-content
    # без tool_call (silent failure). Bot повторяет тот же запрос форсируя
    # модель эмиттить tool. Cost: +1 LLM call (~$0.0001), latency +1-2s,
    # но клиент получает рабочий ответ вместо тишины.
    if not action_type and _looks_like_promise_without_tool(content):
        logger.warning(
            "ai_concierge: promise-without-tool detected, retrying with "
            "tool_choice=required. content=%r", (content or "")[:120],
        )
        try:
            retry_completion = await openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=llm_messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="required",
            )
            r_content, r_tc, r_action_type, r_action_data = await _parse_completion(
                retry_completion, master_context,
            )
            if r_action_type:
                # Сохраняем preamble от 1-го вызова если он был, action — от 2-го
                content = (content or "").strip() or r_content
                tool_call_raw = r_tc
                action_type = r_action_type
                action_data = r_action_data
                latency_ms = int((time.monotonic() - started) * 1000)
                # Перезаписываем usage чтобы учесть retry-токены
                usage = getattr(retry_completion, "usage", None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_concierge: retry with tool_choice=required failed: %s", exc)

    # 8.5. Enrichment через YClients (slots/bookings) — handler'ы side-effect-free,
    # реальный fetch здесь чтобы action_data.slots/bookings были готовы для render.
    if action_type and action_data is not None:
        from maxbot import ai_tools, ai_yclients
        if action_type == ai_tools.ActionType.SHOW_MASTERS:
            action_data = await ai_yclients.enrich_show_masters(action_data)
        elif action_type == ai_tools.ActionType.SHOW_SLOTS:
            action_data = await ai_yclients.enrich_show_slots(action_data)
        elif action_type == ai_tools.ActionType.SHOW_MY_BOOKINGS:
            action_data = await ai_yclients.enrich_show_my_bookings(action_data, bot_user)

    # 9. Telemetry
    usage = getattr(completion, "usage", None)
    tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
    tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0

    # 10. Save assistant message (с action и raw tool_call для audit)
    assistant_msg = await _save_message(
        conversation,
        role=Message.Role.ASSISTANT,
        content=content,
        action_type=action_type or "",
        action_data=action_data,
        tool_call=tool_call_raw,
        tool_call_id=(tool_call_raw or {}).get("id", "") if tool_call_raw else "",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
    )

    logger.info(
        "ai_concierge: conv=%s action=%s tokens=%d/%d latency=%dms",
        conversation.id, action_type or "text", tokens_in, tokens_out, latency_ms,
    )

    return ChatResponseDTO(
        conversation_id=conversation.id,
        content=content,
        action_type=action_type,
        action_data=action_data,
        assistant_message_id=assistant_msg.id,
    )
