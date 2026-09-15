"""DRF-1939 — голосовое сообщение в MAX: честный ответ вместо пустого хода.

Голосовое приходит ходом без текста с вложением ``type == "audio"`` (MAX Bot API
v0.0.33, ``docs/RECON_MAX_VOICE.md``; форма не подтверждена живым вебхуком).
Раньше такой ход считался «фото без картинки»: на глобальном пути уходил
консьержу с пустой строкой, на per-tenant — в ``food_scanner`` как «фото не
скачалось».

Временная заглушка до голосового ввода (эпик DRF-1940, этап 1 — DRF-1942):
ответ детерминированный, без LLM. Аудио не скачивается и не хранится;
``transcription`` из вебхука не читается. Есть картинка — фото-путь как сегодня
(голосовое рядом с фото не слушаем); есть текст — это текстовый ход.
"""

from __future__ import annotations

from typing import Any

#: Черновик на утверждение владельца (DRF-1939). ``audio`` — любой аудиофайл, не только
#: голосовое, поэтому «аудио и голосовые».
VOICE_NOT_SUPPORTED_TEXT = (
    "Я пока не умею разбирать голосовые и аудиофайлы. Напиши, пожалуйста, текстом — я помогу."
)
VOICE_ACTION_TYPE = "voice_not_supported"


def is_voice_only(text: str, attachments: list[Any] | None) -> bool:
    """Ход — только голосовые: нет текста, и КАЖДОЕ вложение — ``audio``."""
    if (text or "").strip() or not attachments:
        return False
    return all(isinstance(att, dict) and att.get("type") == "audio" for att in attachments)
