"""Точка входа: байты голосового → расшифровка или именованный отказ.

Порядок проверок — от дешёвых к дорогим, деньги тратятся последними:

1. длительность по контейнеру (:mod:`apps.speech.ogg`) против
   ``VOICE_MAX_DURATION_S`` → ``voice_too_long`` без вызова провайдера;
2. месячный потолок минут (:mod:`apps.speech.budget`) →
   ``voice_provider_unavailable`` с причиной ``budget``;
3. провайдер (:mod:`apps.speech.registry`) с бюджетом времени вызывающего;
4. нормализация чисел (:mod:`apps.speech.numbers`, K15); пустой после
   всего текст → ``voice_empty``.

Функция никогда не бросает. В логе — только коды, длительности и размер,
никогда текст речи.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from django.conf import settings

from apps.speech import budget
from apps.speech.numbers import normalize_numbers
from apps.speech.ogg import ogg_duration_s
from apps.speech.registry import get_provider
from apps.speech.types import RefusalCode, SpeechRefusal, Transcript

logger = logging.getLogger(__name__)

DEFAULT_MAX_DURATION_S = 60.0
DEFAULT_TIMEOUT_S = 15.0
OGG_MIME = "audio/ogg"


def max_duration_s() -> float:
    return float(
        getattr(settings, "VOICE_MAX_DURATION_S", DEFAULT_MAX_DURATION_S) or DEFAULT_MAX_DURATION_S
    )


def default_timeout_s() -> float:
    return float(getattr(settings, "VOICE_STT_TIMEOUT_S", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S)


def transcribe_voice(
    audio: bytes,
    *,
    mime: str = OGG_MIME,
    timeout_s: float | None = None,
) -> Transcript | SpeechRefusal:
    """Распознать голосовое. ``timeout_s`` — остаток бюджета хода; ``None`` — из настроек."""
    started = time.perf_counter()
    budget_s = default_timeout_s() if timeout_s is None else timeout_s
    duration = ogg_duration_s(audio)
    limit = max_duration_s()

    if duration is not None and duration > limit:
        _log("too_long", started, duration, len(audio))
        return SpeechRefusal(RefusalCode.TOO_LONG, f"{duration:.1f}s>{limit:.0f}s")

    if not budget.reserve(duration if duration is not None else limit):
        _log("budget", started, duration, len(audio))
        return SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "budget")

    provider = get_provider()
    if provider is None:
        _log("no_provider", started, duration, len(audio))
        return SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "no_provider")

    result = provider.transcribe(audio, mime=mime, timeout_s=budget_s)
    if isinstance(result, SpeechRefusal):
        _log(str(result.code), started, duration, len(audio), reason=result.reason)
        return result

    text = normalize_numbers(result.text).strip()
    if not text:
        _log("empty", started, duration, len(audio))
        return SpeechRefusal(RefusalCode.EMPTY, "empty_after_normalize")
    _log(
        "ok",
        started,
        duration,
        len(audio),
        provider=result.provider,
        latency_ms=result.latency_ms,
    )
    return replace(result, text=text, duration_s=duration)


def _log(
    outcome: str,
    started: float,
    duration: float | None,
    size: int,
    *,
    reason: str = "",
    provider: str = "",
    latency_ms: int | None = None,
) -> None:
    logger.info(
        "speech.transcribe outcome=%s audio_s=%s bytes=%d total_ms=%d provider_ms=%s provider=%s reason=%s",
        outcome,
        f"{duration:.2f}" if duration is not None else "-",
        size,
        int((time.perf_counter() - started) * 1000),
        latency_ms if latency_ms is not None else "-",
        provider or "-",
        reason or "-",
    )
