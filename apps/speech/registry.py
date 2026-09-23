"""Выбор провайдера по настройке ``VOICE_STT_PROVIDER``."""

from __future__ import annotations

import logging

from django.conf import settings

from apps.speech.base import SpeechProvider
from apps.speech.providers.fake import FakeSpeechProvider
from apps.speech.providers.openai_stt import OpenAISpeechProvider

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "openai"
KNOWN_PROVIDERS = frozenset({"openai", "fake"})

_cached: SpeechProvider | None = None
_cached_name: str | None = None


def configured_provider_name() -> str:
    return (getattr(settings, "VOICE_STT_PROVIDER", "") or DEFAULT_PROVIDER).strip().lower()


def get_provider() -> SpeechProvider | None:
    """Провайдер по настройке; ``None`` и ERROR в логе, если имя неизвестно.

    Экземпляр кэшируется на процесс (клиент SDK держит пул соединений),
    но пересоздаётся, если настройку поменяли — так тесты с
    ``override_settings`` видят свой провайдер.
    """
    global _cached, _cached_name
    name = configured_provider_name()
    if _cached is not None and _cached_name == name:
        return _cached
    if name == "openai":
        _cached = OpenAISpeechProvider()
    elif name == "fake":
        _cached = FakeSpeechProvider()
    else:
        logger.error("speech.registry.unknown_provider name=%s", name)
        _cached, _cached_name = None, None
        return None
    _cached_name = name
    return _cached


def set_provider_for_tests(provider: SpeechProvider | None) -> None:
    """Подменить провайдер в тестах (обход настроек). ``None`` — сбросить кэш."""
    global _cached, _cached_name
    _cached = provider
    _cached_name = configured_provider_name() if provider is not None else None
