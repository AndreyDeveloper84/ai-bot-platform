"""Результат распознавания: расшифровка или именованный отказ."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RefusalCode(StrEnum):
    """Коды отказов из ТЗ (§«Этап 1», п.5). Человек всегда получает фразу, не молчание.

    Тексты фраз — в PR 3, здесь только код и причина для метрики/лога.
    """

    TOO_LONG = "voice_too_long"
    UNSUPPORTED_FORMAT = "voice_unsupported_format"
    EMPTY = "voice_empty"
    UNRECOGNIZED = "voice_unrecognized"
    PROVIDER_UNAVAILABLE = "voice_provider_unavailable"


@dataclass(frozen=True, slots=True)
class Transcript:
    """Успешная расшифровка.

    ``text`` — уже после нормализации чисел, готов к подстановке в ход.
    ``duration_s`` — длительность аудио по контейнеру (``None``, если не
    удалось прочитать). ``latency_ms`` — время вызова провайдера.
    """

    text: str
    provider: str
    model: str
    duration_s: float | None
    latency_ms: int


@dataclass(frozen=True, slots=True)
class SpeechRefusal:
    """Именованный отказ. ``reason`` — короткая техническая причина для лога, без текста речи."""

    code: RefusalCode
    reason: str = ""

    @property
    def is_refusal(self) -> bool:
        return True
