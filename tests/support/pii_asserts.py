"""Где искать хвост телефона в ответе — в видимом тексте, не в сыром JSON (DRF-2278).

Проверка ``assert "5544" not in raw`` по сырому ответу падает без утечки: в
ответе лежат случайные UUID, дайджесты и токены, и четыре цифры хвоста изредка
оказываются внутри них. :func:`visible_text` собирает то, что может прочитать
человек, — ключи и значения JSON, — и отбрасывает только значения случайной
формы ЦЕЛИКОМ: UUID, hex от 16 символов, токен от 24 символов без пробелов из
букв и цифр вперемешку. Строка с пробелами, одни цифры и числа — текст.

Предел по построению: номер, склеенный внутрь токеноподобной строки,
``visible_text`` не видит — случайную строку от утечки внутри неё не отличить.
Поэтому полные номера (``+7…`` и 10 цифр) вызывающие проверяют по сырому
ответу: там случайное совпадение 10 цифр пренебрежимо.
"""

from __future__ import annotations

import re
from typing import Any

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX = re.compile(r"[0-9a-fA-F]{16,}")
_TOKEN = re.compile(r"[A-Za-z0-9_\-.:=+/]{24,}")


def _is_random_id(value: str) -> bool:
    if _UUID.fullmatch(value) or (_HEX.fullmatch(value) and not value.isdigit()):
        return True
    return bool(
        _TOKEN.fullmatch(value)
        and any(ch.isalpha() for ch in value)
        and any(ch.isdigit() for ch in value)
    )


def visible_text(payload: Any) -> str:
    """Ключи и значения JSON без значений случайной формы — одной строкой."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                parts.append(str(key))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str):
            if not _is_random_id(node):
                parts.append(node)
        elif node is not None:
            parts.append(str(node))

    walk(payload)
    return "\n".join(parts)
