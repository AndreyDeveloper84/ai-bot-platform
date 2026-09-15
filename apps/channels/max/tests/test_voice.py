"""DRF-1939 — признак «ход — только голосовые» (``is_voice_only``).

Форма вложения — по документации MAX Bot API v0.0.33, не подтверждена живым
вебхуком. Голосовым считается ход без текста, где КАЖДОЕ вложение — ``audio``:
картинка рядом — фото-путь, текст рядом — текстовый ход, прочие типы рядом
(sticker, file) — прежний путь (известный остаток).
"""

from __future__ import annotations

import pytest

from apps.channels.max.voice import is_voice_only

AUDIO = {"type": "audio", "payload": {"url": "https://cdn.max.test/v.ogg"}, "transcription": None}


@pytest.mark.parametrize(
    ("text", "attachments", "expected"),
    [
        ("", [AUDIO], True),
        ("", [AUDIO, AUDIO], True),
        ("   \n", [AUDIO], True),
        ("привет", [AUDIO], False),
        ("", [], False),
        ("", None, False),
        ("", ["audio"], False),
        ("", [AUDIO, {"type": "sticker", "payload": {}}], False),
        ("", [AUDIO, {"type": "image", "payload": {"url": "https://cdn.max.test/p.jpg"}}], False),
        ("", [{"type": "file", "payload": {"url": "https://cdn.max.test/f.pdf"}}], False),
    ],
    ids=[
        "one-audio",
        "two-audio",
        "blank-text-audio",
        "text-with-audio",
        "no-attachments",
        "attachments-none",
        "non-dict-attachment",
        "audio-with-sticker",
        "audio-with-image",
        "file-only",
    ],
)
def test_voice_only_predicate(text, attachments, expected) -> None:
    assert is_voice_only(text, attachments) == expected
