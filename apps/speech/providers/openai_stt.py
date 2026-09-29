"""OpenAI ``/v1/audio/transcriptions`` — провайдер по решению владельца (K1, 18.09.2026).

Тот же ключ и тот же прокси, что у текстового провайдера
(``apps/llm/providers/openai_provider.py``): ``OPENAI_API_KEY``,
``OPENAI_PROXY``. Клиент синхронный — handler MAX работает в синхронном
воркере (см. ``apps/channels/max/photo.py``). HTTP-клиент строится из
``openai.DefaultHttpxClient`` по той же причине, что и в текстовом
провайдере (DRF-1437): SDK держит свой стек httpx.

Замер этапа 0 (§2.5): норма 1–2 с на голосовое, один запрос из 198 повис
до таймаута SDK. Поэтому ``max_retries=0`` — повторы решает вызывающий
в рамках общего бюджета хода, а не SDK втихую.

Файл отправляется как есть (ogg/opus из MAX принимается без
перекодировки — проверено). ``language="ru"``, ``temperature=0``.
Модель — ``VOICE_STT_MODEL``, по умолчанию ``gpt-transcribe``:
``whisper-1`` и ``gpt-4o-*-transcribe`` OpenAI отключает 26.02.2027.

В лог не попадает ни текст речи, ни ключ, ни прокси — только класс
ошибки и длительности.
"""

from __future__ import annotations

import io
import logging
import time
from typing import Any

from django.conf import settings

from apps.speech.types import RefusalCode, SpeechRefusal, Transcript

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-transcribe"


class OpenAISpeechProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        proxy: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key or getattr(settings, "OPENAI_API_KEY", "") or ""
        self._proxy = proxy if proxy is not None else (getattr(settings, "OPENAI_PROXY", "") or "")
        self.model = model or getattr(settings, "VOICE_STT_MODEL", "") or DEFAULT_MODEL
        self._client: Any = None
        self._client_timeout: float | None = None

    def _get_client(self, timeout_s: float) -> Any:
        """Ленивый клиент; пересобирается, если бюджет вызова изменился."""
        if self._client is not None and self._client_timeout == timeout_s:
            return self._client
        # Ленивый импорт — SDK тяжёлый, тесты с fake-провайдером его не платят.
        from openai import (  # type: ignore[import-not-found]
            DefaultHttpxClient,
            OpenAI,
        )

        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": timeout_s,
            "max_retries": 0,
        }
        if self._proxy:
            kwargs["http_client"] = DefaultHttpxClient(proxy=self._proxy, timeout=timeout_s)
        self._client = OpenAI(**kwargs)
        self._client_timeout = timeout_s
        return self._client

    def transcribe(
        self, audio: bytes, *, mime: str, timeout_s: float
    ) -> Transcript | SpeechRefusal:
        if not self._api_key:
            logger.warning("speech.openai.no_api_key")
            return SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "no_api_key")
        if timeout_s <= 0:
            return SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, "no_time_budget")

        import openai  # type: ignore[import-not-found]

        started = time.perf_counter()
        try:
            client = self._get_client(timeout_s)
            response = client.audio.transcriptions.create(
                model=self.model,
                file=("voice.ogg", io.BytesIO(audio), mime),
                language="ru",
                response_format="json",
                temperature=0,
            )
        except openai.RateLimitError as exc:
            return self._refuse("rate_limited", exc, started)
        except openai.AuthenticationError as exc:
            return self._refuse("auth", exc, started)
        except (openai.APITimeoutError, openai.APIConnectionError) as exc:
            return self._refuse("transport", exc, started)
        except openai.APIStatusError as exc:
            return self._refuse(f"http_{exc.status_code}", exc, started)
        except Exception as exc:  # noqa: BLE001 — провайдер никогда не бросает
            return self._refuse("unexpected", exc, started)

        latency_ms = int((time.perf_counter() - started) * 1000)
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            logger.info("speech.openai.empty latency_ms=%d bytes=%d", latency_ms, len(audio))
            return SpeechRefusal(RefusalCode.EMPTY, "empty_text")
        logger.info(
            "speech.openai.ok latency_ms=%d bytes=%d chars=%d model=%s",
            latency_ms,
            len(audio),
            len(text),
            self.model,
        )
        return Transcript(
            text=text,
            provider=self.name,
            model=self.model,
            duration_s=None,
            latency_ms=latency_ms,
        )

    def _refuse(self, reason: str, exc: BaseException, started: float) -> SpeechRefusal:
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.warning(
            "speech.openai.failed reason=%s exc=%s latency_ms=%d model=%s",
            reason,
            type(exc).__name__,
            latency_ms,
            self.model,
        )
        return SpeechRefusal(RefusalCode.PROVIDER_UNAVAILABLE, reason)
