"""Провайдер без сети — для тестов и локальной работы (``VOICE_STT_PROVIDER=fake``).

Отдаёт заранее заданный текст или отказ; запоминает вызовы, чтобы тесты
могли проверить «провайдер вызван один раз» и «не вызван вовсе».
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from apps.speech.types import SpeechRefusal, Transcript


@dataclass
class FakeSpeechProvider:
    name: str = "fake"
    model: str = "fake-1"
    text: str = ""
    refusal: SpeechRefusal | None = None
    calls: list[tuple[int, str, float]] = field(default_factory=list)

    def transcribe(
        self, audio: bytes, *, mime: str, timeout_s: float
    ) -> Transcript | SpeechRefusal:
        self.calls.append((len(audio), mime, timeout_s))
        if self.refusal is not None:
            return self.refusal
        started = time.perf_counter()
        return Transcript(
            text=self.text,
            provider=self.name,
            model=self.model,
            duration_s=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
