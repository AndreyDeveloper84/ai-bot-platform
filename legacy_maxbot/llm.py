"""LLM-утилиты для AI-помощника MAX-бота.

- `get_async_openai_client()` — AsyncOpenAI с поддержкой HTTPS-прокси
  (api.openai.com заблокирован в РФ; паттерн из agents/agents/__init__.py
  но async версия)
- `mcp_tools_to_openai_schema(mcp_tools)` — конвертер MCP tool definitions
  → OpenAI tools schema (оба используют JSON Schema, ~10 строк glue)
- `chat_with_tools(...)` — главный loop: chat.completions.create с tools,
  если tool_calls → выполнить через MCP-клиент, повторить. Защита от
  бесконечной петли через max_iterations.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings
from openai import AsyncOpenAI

from maxbot.mcp_client import MaxbotMCPClient


logger = logging.getLogger("maxbot.llm")

# Дефолтная модель — gpt-4o-mini (дёшево, хороший tool-use)
DEFAULT_MODEL = "gpt-4o-mini"

# Защита от бесконечного цикла tool-use
MAX_TOOL_ITERATIONS = 5

# Сообщение клиенту когда модель не справилась за лимит / RAG-score < threshold.
# Каноническая форма — её мы возвращаем сами. Но LLM также может произнести
# вариант фразы из system_prompt («Не знаю, передам менеджеру») — `is_giveup`
# детектит обе формы, чтобы handler не пропустил giveup и обязательно создал
# BotInquiry.
LLM_GIVEUP_MESSAGE = "Не получилось разобраться. Передаю вопрос менеджеру."

# Маркеры giveup-фраз — нормализованные substring'и (см. is_giveup ниже).
# Если LLM произнёс что-то начинающееся с одной из этих фраз — считаем giveup.
_GIVEUP_MARKERS = (
    "не получилось разобраться",
    "не знаю, передам менеджеру",
    "не знаю передам менеджеру",  # без запятой — на случай если LLM пропустит
)


def is_giveup(answer: str | None) -> bool:
    """Детектор giveup-ответа: каноническая константа ИЛИ фраза из system_prompt.

    Нормализуем (lowercase + collapse whitespace) и проверяем substring,
    чтобы поймать вариации с разной пунктуацией / регистром / обёрткой
    типа «К сожалению, не знаю. Передам менеджеру — он перезвонит».
    """
    if not answer:
        return False
    normalized = " ".join(answer.lower().split())
    return any(marker in normalized for marker in _GIVEUP_MARKERS)

# RAG порог: если max similarity ниже — не зовём LLM, сразу giveup.
# Экономит ~2.3s LLM call'а в случаях когда вопрос явно не из FAQ.
RAG_MIN_SCORE = 0.45

# Сколько top-результатов из FAQ кладём в context для LLM
RAG_TOP_K = 3


def get_async_openai_client() -> AsyncOpenAI:
    """AsyncOpenAI с прокси из OPENAI_PROXY (для async-handler'ов maxbot)."""
    kwargs: dict[str, Any] = {"api_key": settings.OPENAI_API_KEY}
    if getattr(settings, "OPENAI_BASE_URL", ""):
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    proxy = getattr(settings, "OPENAI_PROXY", "")
    if proxy:
        import httpx
        kwargs["http_client"] = httpx.AsyncClient(proxy=proxy)
    return AsyncOpenAI(**kwargs)


def mcp_tools_to_openai_schema(mcp_tools: list[Any]) -> list[dict]:
    """Конвертирует MCP tool definitions в OpenAI tools schema.

    MCP `Tool(name, description, inputSchema)` → OpenAI
    `{type: "function", function: {name, description, parameters: <JSON Schema>}}`.

    Оба используют JSON Schema для parameters → передаём как есть.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": getattr(tool, "description", "") or "",
                "parameters": getattr(tool, "inputSchema", None) or {
                    "type": "object",
                    "properties": {},
                },
            },
        }
        for tool in mcp_tools
    ]


async def chat_with_tools(
    *,
    messages: list[dict],
    mcp_client: MaxbotMCPClient,
    model: str = DEFAULT_MODEL,
    max_iterations: int = MAX_TOOL_ITERATIONS,
    openai_client: AsyncOpenAI | None = None,
) -> str:
    """Tool-use loop: LLM может вызывать MCP-tools для ответа на запрос.

    `messages` стартовый список (system + user). Loop модифицирует копию.
    Возвращает финальный текст для клиента.

    При превышении max_iterations — возвращает LLM_GIVEUP_MESSAGE (caller
    должен создать BotInquiry для менеджера).
    """
    await mcp_client.ensure_started()
    tools_schema = mcp_tools_to_openai_schema(mcp_client.list_tools())
    client = openai_client or get_async_openai_client()
    msgs = list(messages)

    for iteration in range(max_iterations):
        resp = await client.chat.completions.create(
            model=model,
            messages=msgs,
            tools=tools_schema,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            # Финальный ответ
            return msg.content or ""

        # Сохраняем assistant-сообщение с tool_calls
        msgs.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ],
        })

        # Выполняем каждый tool через MCP, ответ кладём в messages
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            try:
                result = await mcp_client.call_tool(tc.function.name, args)
                content = result.content[0].text if result.content else ""
            except Exception as exc:  # noqa: BLE001
                logger.exception("MCP tool %s failed", tc.function.name)
                content = json.dumps({"error": str(exc)})
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": content,
            })

    logger.warning("chat_with_tools: hit max_iterations=%d, giving up", max_iterations)
    return LLM_GIVEUP_MESSAGE


async def chat_rag(
    *,
    user_text: str,
    system_prompt: str,
    mcp_client: MaxbotMCPClient,
    model: str = DEFAULT_MODEL,
    openai_client: AsyncOpenAI | None = None,
    min_score: float = RAG_MIN_SCORE,
    top_k: int = RAG_TOP_K,
) -> str:
    """RAG-as-context: search_faq → context в system → 1 LLM call (без tools).

    Быстрее chat_with_tools на ~30% (3 OpenAI calls → 2). На low-score
    (top-1 < min_score) НЕ возвращаем early giveup, а зовём LLM с пустым
    FAQ-context — пусть LLM по правилам system_prompt'а решит: вежливо
    редиректнуть на off-topic ИЛИ честно сказать «передам менеджеру».
    Раньше early-giveup обижал клиентов на «привет/спасибо» (теперь это
    intent-router'ом перехватывается ДО chat_rag).

    На технические fail (search_faq exception) — всё ещё giveup, иначе
    LLM может галлюцинировать без FAQ-context.
    """
    await mcp_client.ensure_started()

    # 1. Прямой вызов search_faq (минуя LLM-роутер)
    try:
        result = await mcp_client.call_tool("search_faq", {"query": user_text, "k": top_k})
        if not result.content:
            faq_items = []
        else:
            data = json.loads(result.content[0].text)
            # search_faq возвращает {"results": [...]} (dict-обёртка); резерв
            # для случая single dict (legacy) или list (на случай других tools)
            if isinstance(data, dict) and "results" in data:
                faq_items = data["results"]
            elif isinstance(data, list):
                faq_items = data
            elif isinstance(data, dict):
                faq_items = [data]
            else:
                faq_items = []
    except Exception:  # noqa: BLE001
        logger.exception("chat_rag: search_faq failed")
        return LLM_GIVEUP_MESSAGE

    # Low-score / empty FAQ — НЕ giveup, а пустой context для LLM.
    # Он по правилам 2-3 system_prompt'а: либо вежливо редиректит на услуги
    # (off-topic), либо честно говорит «передам менеджеру» (правило 4).
    top_score = max((item.get("score", 0) for item in faq_items), default=0.0)
    if top_score < min_score:
        logger.info("chat_rag: top score %.3f < min %.3f, LLM решит без FAQ-context",
                    top_score, min_score)
        faq_items = []  # пустой контекст — LLM не покажется галлюцинировать про FAQ

    # 2. Формируем context из найденных FAQ (или пустой)
    if faq_items:
        context_lines = ["Найденные FAQ для контекста (используй чтобы ответить):"]
        for i, item in enumerate(faq_items, 1):
            context_lines.append(
                f"\n{i}. Q: {item.get('question', '')}\n   A: {item.get('answer', '')}"
                f"\n   (similarity: {item.get('score', 0):.2f})"
            )
        context = "\n".join(context_lines)
    else:
        context = (
            "Релевантных FAQ не найдено — возможно вопрос не про салон. "
            "Если это так, вежливо верни клиента к услугам (правило 2-3 выше). "
            "Если вопрос явно про салон, но ответа нет — следуй правилу 4."
        )

    # 3. Один LLM call без tools
    client = openai_client or get_async_openai_client()
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt + "\n\n" + context},
            {"role": "user", "content": user_text},
        ],
    )
    return resp.choices[0].message.content or LLM_GIVEUP_MESSAGE
