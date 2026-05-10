"""Прогрев response cache при старте процесса MAX-бота.

После `python -m maxbot.main` (systemd restart / deploy) Redis-кэш с
ответами AI-помощника может быть холодным. Эта функция фоном прогоняет
~10 hot-вопросов через `chat_rag` и кладёт ответы в `response_cache`,
чтобы первый клиент по «как записаться?» получил мгновенный ответ
вместо ~6.7s OpenAI/MCP round-trip.

Запускается из `maxbot.main` через `asyncio.create_task` — не блокирует
старт webhook listener'а.
"""
from __future__ import annotations

import asyncio
import logging
import time

from maxbot import texts
from maxbot.llm import chat_rag, is_giveup
from maxbot.mcp_client import MaxbotMCPClient
from maxbot.popular_questions import POPULAR_QUESTIONS
from maxbot.response_cache import get_cached_answer, set_cached_answer


logger = logging.getLogger("maxbot.warmup")


async def warmup_response_cache(
    *,
    mcp_client: MaxbotMCPClient,
) -> dict[str, int]:
    """Прогревает cache для POPULAR_QUESTIONS. Возвращает {cached, skipped, failed}.

    - skipped: уже был в cache (Redis выжил рестарт или прошлый warmup)
    - cached: chat_rag успешно отдал ответ ≠ GIVEUP, положили в cache
    - failed: exception в chat_rag ИЛИ ответ == GIVEUP (не кэшируем)

    Best-effort: exception на одном вопросе НЕ ломает прогрев остальных.
    """
    cached = skipped = failed = 0
    started = time.perf_counter()

    for q in POPULAR_QUESTIONS:
        if await get_cached_answer(q) is not None:
            skipped += 1
            logger.debug("warmup skip (already cached): %r", q)
            continue

        try:
            answer = await chat_rag(
                user_text=q,
                system_prompt=texts.AI_SYSTEM_PROMPT,
                mcp_client=mcp_client,
            )
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("warmup failed on %r: %s", q, exc)
            continue

        if is_giveup(answer):
            failed += 1
            logger.debug("warmup giveup (not cached): %r", q)
            continue

        await set_cached_answer(q, answer)
        cached += 1
        logger.info("warmup cached: %r", q)

        # Не DDoS'им OpenAI — даём прокси/rate-limit'у вздохнуть. Минимум.
        await asyncio.sleep(0.1)

    elapsed = time.perf_counter() - started
    logger.info(
        "warmup done in %.1fs: cached=%d skipped=%d failed=%d (total=%d)",
        elapsed, cached, skipped, failed, len(POPULAR_QUESTIONS),
    )
    return {"cached": cached, "skipped": skipped, "failed": failed}
