"""Голосовое → текстовый ход (DRF-1942, этап 1, PR 3 из 3).

Одна точка входа для обоих путей handler'а (глобального и салонного):
:func:`resolve_voice_turn`. На входе событие, у которого ``text`` пуст и
все вложения — ``audio`` (:func:`apps.channels.max.voice.is_voice_only`).
На выходе либо :class:`VoiceResolved` — то же событие, но с текстом
расшифровки и без аудио-вложения, либо :class:`VoiceRefused` — код и
фраза для человека.

После :class:`VoiceResolved` ход идёт **как текстовый**: запись
сообщения, гейт safety, дневник, консьерж — ни одного ``if voice`` ниже.
Единственное исключение — K19-Б (решение владельца 22.09): гейту
отдаётся копия текста без знаков препинания (``gate_text``), потому что
исключение для гиперболы «умираю как хочу…» в ``pre_check.py`` не
допускает запятую, а OpenAI ставит знаки всегда. Когда окно safety
поправит регулярку (вариант А), ``VOICE_GATE_STRIP_PUNCT=false`` — и
голос с текстом проверяются одинаково.

Бюджет времени на весь ход — ``VOICE_TURN_BUDGET_S`` (20 с): скачивание
(``audio.py``, ≤ 8 с) плюс распознавание (``apps/speech``, ≤ 15 с) в
пределах общего остатка; потребитель очереди один на всех
(``apps/workers/consumer.py``), и зависший ход задерживает всех.

Флаги (все выключены по умолчанию, см. ``config/settings/base.py``):

* ``VOICE_INPUT_ENABLED`` — главный. Выключен → отказ ``voice_disabled``
  с текстом заглушки DRF-1939, байт в байт как до этого PR.
* ``VOICE_CROSS_BORDER_ALLOWED`` — отдельное разрешение на передачу за
  рубеж: без него провайдер ``openai`` не вызывается и файл даже не
  скачивается.
* ``VOICE_ECHO_MODE`` — «Я услышала: …» перед ответом (``always``) или
  нет (``never``); режим «при неуверенности» невозможен, провайдер
  уверенность не отдаёт.

Тексты отказов — **черновики на утверждение владельца** (вопрос 9 ТЗ),
кроме ``voice_disabled``: он утверждён в DRF-1349, раздел P.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Final

from django.conf import settings

from apps.channels.max.audio import (
    AUDIO_DOWNLOAD_DEADLINE_S,
    AudioDownloadError,
    AudioFormatError,
    AudioTooLargeError,
    download_audio,
    extract_first_audio,
)
from apps.channels.max.parser import CanonicalEvent
from apps.channels.max.voice import VOICE_ACTION_TYPE, VOICE_NOT_SUPPORTED_TEXT
from apps.speech.registry import configured_provider_name
from apps.speech.service import default_timeout_s, max_duration_s, transcribe_voice
from apps.speech.types import RefusalCode, SpeechRefusal, Transcript

logger = logging.getLogger(__name__)

TRUE_VALUES: Final[frozenset[str]] = frozenset({"true", "1", "yes"})
DEFAULT_TURN_BUDGET_S: Final[float] = 20.0
#: Повторить запрос к провайдеру после сетевого сбоя только если осталось
#: хотя бы столько: короче — второй запрос сам упрётся в дедлайн.
RETRY_MIN_REMAINING_S: Final[float] = 6.0

CODE_DISABLED: Final[str] = "voice_disabled"
CODE_CONSENT_MISSING: Final[str] = "voice_consent_missing"  # слот этапа 2, здесь не выдаётся
CODE_TOO_LARGE: Final[str] = "voice_too_large"
CODE_DOWNLOAD_FAILED: Final[str] = "voice_download_failed"
CODE_UNSUPPORTED_FORMAT: Final[str] = "voice_unsupported_format"

#: Фразы человеку по коду отказа. ЧЕРНОВИКИ на утверждение владельца
#: (вопрос 9 ТЗ), кроме ``voice_disabled`` — утверждён (DRF-1349, раздел P).
REFUSAL_TEXTS: Final[dict[str, str]] = {
    CODE_DISABLED: VOICE_NOT_SUPPORTED_TEXT,
    CODE_CONSENT_MISSING: "Чтобы разбирать голосовые, мне нужно твоё согласие. Пока напиши, пожалуйста, текстом.",
    str(RefusalCode.TOO_LONG): (
        "Голосовое длиннее {limit} секунд — такое не разберу. Запиши короче или напиши текстом."
    ),
    CODE_TOO_LARGE: "Не получилось загрузить голосовое — файл слишком большой. Напиши, пожалуйста, текстом.",
    CODE_DOWNLOAD_FAILED: "Не получилось загрузить голосовое. Попробуй ещё раз или напиши текстом.",
    CODE_UNSUPPORTED_FORMAT: "Такой аудиофайл не разберу. Запиши голосовое прямо в MAX или напиши текстом.",
    str(RefusalCode.EMPTY): "В голосовом не расслышала слов. Скажи ещё раз или напиши текстом.",
    str(
        RefusalCode.UNRECOGNIZED
    ): "Не смогла разобрать голосовое. Скажи ещё раз или напиши текстом.",
    str(RefusalCode.PROVIDER_UNAVAILABLE): (
        "Сейчас не могу разобрать голосовое. Напиши, пожалуйста, текстом — я помогу."
    ),
}

ECHO_LINE: Final[str] = "Я услышала: «{text}»"

# Только знаки препинания МЕЖДУ словами. Апостроф и дефис внутри слова
# остаются: «don't want to live» и «self-harm» — шаблоны гейта, и без
# апострофа кризис по-английски терялся (проба DRF-2423 это поймала).
_PUNCT = re.compile(r"[,.!?;:…\"«»“”„()\[\]{}<>—–]|(?<!\w)['\-’]|['\-’](?!\w)")
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class VoiceResolved:
    """Событие с текстом расшифровки вместо аудио + копия текста для гейта."""

    event: CanonicalEvent
    transcript: Transcript
    gate_text: str


@dataclass(frozen=True, slots=True)
class VoiceRefused:
    """Отказ: код (он же ``action_type`` записи), фраза человеку, причина для лога."""

    code: str
    text: str
    action_type: str
    reason: str = ""


def _flag(name: str) -> bool:
    value = getattr(settings, name, False)
    if isinstance(value, str):
        return value.strip().lower() in TRUE_VALUES
    return bool(value)


def voice_input_enabled() -> bool:
    return _flag("VOICE_INPUT_ENABLED")


def cross_border_allowed() -> bool:
    return _flag("VOICE_CROSS_BORDER_ALLOWED")


def gate_strip_punct() -> bool:
    value = getattr(settings, "VOICE_GATE_STRIP_PUNCT", True)
    if isinstance(value, str):
        return value.strip().lower() in TRUE_VALUES
    return bool(value)


def echo_mode() -> str:
    return str(getattr(settings, "VOICE_ECHO_MODE", "never") or "never").strip().lower()


def turn_budget_s() -> float:
    return float(
        getattr(settings, "VOICE_TURN_BUDGET_S", DEFAULT_TURN_BUDGET_S) or DEFAULT_TURN_BUDGET_S
    )


def strip_for_gate(text: str) -> str:
    """Копия текста без знаков препинания между словами для ``evaluate_inbound`` (K19-Б).

    Апостроф и дефис внутри слова сохраняются («don't», «self-harm») — иначе
    английские шаблоны гейта перестают срабатывать.
    """
    return _WS.sub(" ", _PUNCT.sub(" ", text)).strip()


def with_voice_echo(reply_text: str, transcript_text: str) -> str:
    """Добавить «Я услышала: …» перед ответом, если ``VOICE_ECHO_MODE=always``."""
    if echo_mode() != "always" or not transcript_text:
        return reply_text
    return f"{ECHO_LINE.format(text=transcript_text)}\n\n{reply_text}"


def _refused(code: str, reason: str) -> VoiceRefused:
    text = REFUSAL_TEXTS.get(code) or REFUSAL_TEXTS[str(RefusalCode.PROVIDER_UNAVAILABLE)]
    if code == str(RefusalCode.TOO_LONG):
        text = text.format(limit=int(max_duration_s()))
    action_type = VOICE_ACTION_TYPE if code == CODE_DISABLED else code
    return VoiceRefused(code=code, text=text, action_type=action_type, reason=reason)


def _without_audio(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in attachments if not (isinstance(a, dict) and a.get("type") == "audio")]


def resolve_voice_turn(
    event: CanonicalEvent,
    *,
    trace_id: Any = None,
    budget_s: float | None = None,
) -> VoiceResolved | VoiceRefused:
    """Скачать, распознать и подставить текст — или объяснить человеку, почему нет.

    Никогда не бросает: любая ошибка ниже становится :class:`VoiceRefused`.
    Вызывать **внутри** ``with_idempotency`` — повтор вебхука тогда не
    распознаёт аудио второй раз.
    """
    started = time.monotonic()
    budget = turn_budget_s() if budget_s is None else budget_s

    if not voice_input_enabled():
        return _refused(CODE_DISABLED, "flag_off")

    ref = extract_first_audio(event.attachments)
    if ref is None:
        return _refused(CODE_UNSUPPORTED_FORMAT, "no_audio_url")

    if configured_provider_name() == "openai" and not cross_border_allowed():
        logger.warning("channels.max.voice.cross_border_not_allowed")
        return _refused(str(RefusalCode.PROVIDER_UNAVAILABLE), "cross_border")

    try:
        audio = download_audio(ref.url, deadline_s=min(AUDIO_DOWNLOAD_DEADLINE_S, budget))
    except AudioTooLargeError:
        return _log_refusal(_refused(CODE_TOO_LARGE, "too_large"), started)
    except AudioFormatError:
        return _log_refusal(_refused(CODE_UNSUPPORTED_FORMAT, "not_ogg"), started)
    except AudioDownloadError as exc:
        return _log_refusal(_refused(CODE_DOWNLOAD_FAILED, str(exc).split(":")[0]), started)
    except Exception as exc:  # noqa: BLE001 — граница: ход не должен упасть
        logger.warning("channels.max.voice.download_unexpected exc=%s", type(exc).__name__)
        return _log_refusal(_refused(CODE_DOWNLOAD_FAILED, "unexpected"), started)
    download_ms = int((time.monotonic() - started) * 1000)

    remaining = budget - (time.monotonic() - started)
    if remaining <= 0:
        return _log_refusal(
            _refused(str(RefusalCode.PROVIDER_UNAVAILABLE), "no_time_budget"), started
        )

    result = transcribe_voice(audio, timeout_s=min(default_timeout_s(), remaining))
    remaining = budget - (time.monotonic() - started)
    if (
        isinstance(result, SpeechRefusal)
        and result.code == RefusalCode.PROVIDER_UNAVAILABLE
        and result.reason == "transport"
        and remaining >= RETRY_MIN_REMAINING_S
    ):
        logger.info("channels.max.voice.retry remaining_s=%.1f", remaining)
        result = transcribe_voice(audio, timeout_s=min(default_timeout_s(), remaining))

    if isinstance(result, SpeechRefusal):
        return _log_refusal(
            _refused(str(result.code), result.reason), started, download_ms=download_ms
        )

    text = result.text
    resolved_event = replace(event, text=text, attachments=_without_audio(event.attachments))
    gate_text = strip_for_gate(text) if gate_strip_punct() else text
    logger.info(
        "channels.max.voice.resolved provider=%s audio_s=%s download_ms=%d stt_ms=%d total_ms=%d chars=%d",
        result.provider,
        f"{result.duration_s:.1f}" if result.duration_s is not None else "-",
        download_ms,
        result.latency_ms,
        int((time.monotonic() - started) * 1000),
        len(text),
    )
    return VoiceResolved(event=resolved_event, transcript=result, gate_text=gate_text)


def _log_refusal(
    refusal: VoiceRefused, started: float, *, download_ms: int | None = None
) -> VoiceRefused:
    logger.info(
        "channels.max.voice.refused code=%s reason=%s total_ms=%d download_ms=%s",
        refusal.code,
        refusal.reason or "-",
        int((time.monotonic() - started) * 1000),
        download_ms if download_ms is not None else "-",
    )
    return refusal
