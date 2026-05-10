"""Response cache для AI-ответов: повторные «как записаться?» — мгновенно.

Кэшируем пары (нормализованный_вопрос → ответ) в Django cache (Redis на проде)
на 24 часа. Цель — отвечать на популярные вопросы за ~50ms вместо ~6.7s
(embeddings ~1.5s + chat.completions ~3.2s + IPC/parsing).

Что НЕ кэшируем:
- LLM_GIVEUP_MESSAGE — пусть retry даёт шанс LLM ответить (BotInquiry уже создан)
- Слишком короткие вопросы (< MIN_QUESTION_LEN после нормализации) — бессмысленно

Нормализация = устойчивость к косметике («Как записаться?», «как  записаться  »,
«КАК ЗАПИСАТЬСЯ.» — один и тот же ключ).
"""
from __future__ import annotations

import hashlib
import logging
import re

from asgiref.sync import sync_to_async
from django.core.cache import cache


logger = logging.getLogger("maxbot.cache")

CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h
CACHE_KEY_PREFIX = "maxbot:ai:answer:"
MIN_QUESTION_LEN = 3  # < 3 chars после нормализации — не кэшируем

# Хвостовая пунктуация которую съедаем при нормализации
_TRAILING_PUNCT_RE = re.compile(r"[?!.,;:\s]+$")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_question(text: str) -> str:
    """lowercase, schwa whitespace, strip trailing punctuation.

    «Как записаться?», «как  ЗАПИСАТЬСЯ  ?», «как записаться.» → «как записаться».
    """
    text = text.lower().strip()
    text = _WHITESPACE_RE.sub(" ", text)
    text = _TRAILING_PUNCT_RE.sub("", text)
    return text


def _cache_key(normalized: str) -> str:
    """md5 от нормализованного вопроса — устойчиво к кириллице/длине."""
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()
    return f"{CACHE_KEY_PREFIX}{digest}"


async def get_cached_answer(question: str) -> str | None:
    """Кэшированный ответ или None. Не падает на пустых/коротких вопросах."""
    norm = normalize_question(question)
    if len(norm) < MIN_QUESTION_LEN:
        return None
    return await sync_to_async(cache.get)(_cache_key(norm))


async def set_cached_answer(question: str, answer: str) -> None:
    """Кладёт пару в кэш на CACHE_TTL_SECONDS. Игнорирует слишком короткое."""
    norm = normalize_question(question)
    if len(norm) < MIN_QUESTION_LEN:
        return
    await sync_to_async(cache.set)(_cache_key(norm), answer, CACHE_TTL_SECONDS)
