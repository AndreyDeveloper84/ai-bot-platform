"""OpenAI provider wrapper (DRF-428 / D1).

Sole LLM provider in Sprint 1. Wrapped in the circuit breaker from
``apps.orchestrator.llm.breaker``. When the breaker is open, returns
the static fallback template (no LLM call attempted; latency <10ms).

Shape designed to extract into an ``LLMProvider`` Protocol when Sprint
6 multi-provider routing arrives — ``complete()`` is the method name
that protocol will require.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings

from apps.audit.services import write_audit
from apps.orchestrator.llm.breaker import BreakerOpenError, with_circuit_breaker
from apps.orchestrator.llm.templates import get_fallback

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Provider-agnostic response shape.

    Sprint 1 has just OpenAI; Sprint 6 generalises this into the
    ``LLMProvider`` Protocol's return type and adds tool-call fields.
    """

    content: str
    model: str
    is_fallback: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


_BREAKER_NAME = "openai.complete"


class OpenAIProvider:
    """Async OpenAI ChatCompletion wrapper with circuit breaker.

    Usage:

        provider = OpenAIProvider()
        response = await provider.complete(
            messages=[{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )

    On breaker-open (5 failures in 60s by default): returns
    ``LLMResponse(content=template, is_fallback=True)`` immediately
    without making an HTTP call. A Sentry-instrumented audit log
    records the fallback.
    """

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gpt-4o-mini",
        fallback_lang: str = "ru",
    ):
        self.api_key = api_key or getattr(settings, "OPENAI_API_KEY", "")
        self.default_model = default_model
        self.fallback_lang = fallback_lang

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Run an OpenAI chat completion through the circuit breaker.

        Returns ``LLMResponse``. If the breaker is open, returns the
        cached fallback template; ``is_fallback=True``.
        """

        chosen_model = model or self.default_model
        try:
            return await with_circuit_breaker(
                _BREAKER_NAME,
                self._call_openai,
                messages,
                chosen_model,
                **kwargs,
            )
        except BreakerOpenError:
            # Short-circuit: breaker is open. Return static fallback,
            # log the event for observability. write_audit is sync
            # (Django ORM), so wrap with sync_to_async — we're inside
            # an async method.
            template = get_fallback(self.fallback_lang)
            logger.warning("llm.openai.fallback model=%s reason=breaker_open", chosen_model)
            # thread_sensitive=False — Django's sync_to_async runs the
            # call on a fresh worker thread without an asyncio event loop,
            # which is what the Django ORM requires.
            await sync_to_async(write_audit, thread_sensitive=False)(
                "llm.openai.fallback_served",
                target="OpenAIProvider",
                payload={"model": chosen_model, "reason": "breaker_open"},
            )
            return LLMResponse(
                content=template,
                model=chosen_model,
                is_fallback=True,
            )

    async def _call_openai(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> LLMResponse:
        """Actual OpenAI API call.

        Sprint 1 uses the async OpenAI SDK directly. Sprint 6 swaps this
        for a router that picks the provider from per-skill config.
        """

        # Local import — avoid loading openai client at module import
        # time (it pulls heavy deps; we don't always need it).
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.api_key)
        response = await client.chat.completions.create(
            messages=messages,  # type: ignore[arg-type]
            model=model,
            **kwargs,
        )
        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            is_fallback=False,
            tokens_in=response.usage.prompt_tokens if response.usage else 0,
            tokens_out=response.usage.completion_tokens if response.usage else 0,
        )
