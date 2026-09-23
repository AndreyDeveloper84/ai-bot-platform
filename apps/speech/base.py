"""Контракт провайдера распознавания."""

from __future__ import annotations

from typing import Protocol

from apps.speech.types import SpeechRefusal, Transcript


class SpeechProvider(Protocol):
    """Один вызов: байты → текст или отказ.

    Реализация **никогда не бросает** — любая ошибка сети, ключа, квоты
    или формата превращается в :class:`SpeechRefusal`. Иначе ключ
    идемпотентности хода останется занятым, а человек не получит ответа
    (§5.2 отчёта этапа 0).

    ``timeout_s`` — бюджет на этот вызов; вызывающий уже вычел время
    скачивания из общего лимита хода.
    """

    name: str

    def transcribe(
        self, audio: bytes, *, mime: str, timeout_s: float
    ) -> Transcript | SpeechRefusal: ...
