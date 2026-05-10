"""Intent-роутер: phatic phrases (приветствие/благодарность/small-talk) → canned response.

Запускается ПЕРЕД RAG/LLM в `_get_ai_answer`. Простые greeting-фразы не должны
тратить $0.0001 + 3-6s на OpenAI и не должны порождать BotInquiry. Канонический
дружелюбный ответ + nudge к услугам.

Для всего остального возвращаем None — handler идёт через chat_rag (response
cache → RAG-as-context → LLM).

Регексы конечны и легко расширяются. Якоримся к началу строки (`^\s*`) и
требуем word-boundary, чтобы «приветик в честь скидки» НЕ матчилось как simple
greeting.
"""
from __future__ import annotations

import re


_GREETING_RE = re.compile(
    r"^\s*("
    r"привет(ствую|ик|ы)?"
    r"|здравствуй(те)?"
    r"|здарова|здаровеньки"
    r"|добрый\s+(день|вечер|утро|ночь)"
    r"|доброе\s+(утро|время)"
    r"|доброго\s+(дня|вечера|утра|времени)"
    r"|hi|hello|hey"
    r")\b[\s!.,?]*$",
    re.IGNORECASE | re.UNICODE,
)

_THANKS_RE = re.compile(
    r"^\s*("
    r"спасибо|благодарю|спс|сенкс|сенкью"
    r"|thanks|thx|thank\s+you"
    r")\b[\s!.,?]*$",
    re.IGNORECASE | re.UNICODE,
)

_SMALL_TALK_RE = re.compile(
    r"^\s*("
    r"как\s+(дела|жизнь|ты|поживаешь|настроение)"
    r"|что\s+нового"
    r"|как\s+у\s+тебя\s+дела"
    r")\b[\s!.,?]*$",
    re.IGNORECASE | re.UNICODE,
)


GREETING_RESPONSE = (
    "Здравствуйте! 👋 Я помощник салона «Формула тела» в Пензе. "
    "Расскажу об услугах, помогу записаться и отвечу на вопросы. "
    "Чем могу помочь?"
)

THANKS_RESPONSE = (
    "Пожалуйста! 😊 Если будут вопросы об услугах или записи — задавайте."
)

SMALL_TALK_RESPONSE = (
    "Спасибо, всё хорошо! 😊 Я помогаю записаться в салон «Формула тела» "
    "и отвечаю на вопросы об услугах. Что вас интересует?"
)


def detect_intent(text: str) -> str | None:
    """Возвращает canned response или None если intent не распознан.

    Срабатывает ТОЛЬКО на чисто phatic фразы (приветствие отдельно, без
    содержательного вопроса). «привет, сколько стоит» → None — пусть
    RAG обрабатывает целиком, ответит и на приветствие, и на вопрос.
    """
    if not text or not text.strip():
        return None
    if _GREETING_RE.match(text):
        return GREETING_RESPONSE
    if _THANKS_RE.match(text):
        return THANKS_RESPONSE
    if _SMALL_TALK_RE.match(text):
        return SMALL_TALK_RESPONSE
    return None
