"""OpenAI provider wrapper (DRF-428 / D1).

Sole LLM provider in Sprint 1. Wrapped in the circuit breaker from
``apps.orchestrator.llm.breaker``. When the breaker is open, returns
the static fallback template (no LLM call attempted; latency <10ms).

Shape designed to extract into an ``LLMProvider`` Protocol when Sprint
6 multi-provider routing arrives — ``complete()`` is the method name
that protocol will require.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings

from apps.audit.services import write_audit
from apps.llm.model_tiers import resolve_model
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

        # DRF-1443 — this wrapper is OpenAI-only by construction, so a
        # vendor id needs no translation here. It still runs the shared
        # resolver for one reason: the vendor-neutral tier names are now
        # the vocabulary of ``apps.orchestrator.intent_router``, and that
        # module reaches BOTH the router-resolved provider and this one.
        # Without this line a legacy-path classify would post the literal
        # string "fast" to OpenAI and 400.
        chosen_model = resolve_model(
            model,
            vendor="openai",
            fast=self.default_model,
            smart=self.default_model,
        )
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

    def _build_client(self) -> Any:
        """Build the SDK client the way the production provider does.

        DRF-1436. Sprint 1 built ``AsyncOpenAI(api_key=...)`` and nothing
        else, and the Sprint 6 router that was meant to replace it never
        took this call site over. On the hosts this runs on that omission
        is not cosmetic:

        * ``api.openai.com`` refuses Russian addresses outright
          (``unsupported_country_region_territory``), so without
          ``OPENAI_PROXY`` the call cannot succeed at all;
        * without a timeout a stalled path — a proxy that accepts the TCP
          connection and then never completes ``CONNECT``, the 2026-08-13
          incident — hangs for the SDK default rather than failing, and a
          hang writes nothing to any journal.

        Both settings are read exactly as
        :meth:`apps.llm.providers.openai_provider.OpenAIProvider._get_client`
        reads them, so the two implementations cannot drift apart on the
        road they travel. ``max_retries=0`` for the same reason it is
        zero there: the circuit breaker around this call owns the retry
        and failure accounting, and a second, invisible SDK-level retry
        loop would multiply every outage by three.
        """

        # Local import — avoid loading openai client at module import
        # time (it pulls heavy deps; we don't always need it).
        from openai import AsyncOpenAI

        timeout = getattr(settings, "LLM_REQUEST_TIMEOUT_S", 30.0)
        client_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": timeout,
            "max_retries": 0,
        }
        proxy = getattr(settings, "OPENAI_PROXY", "") or ""
        if proxy:
            import httpx

            # The proxy client carries the same timeout, or the bound we
            # just set on the SDK would stop at the SDK's own layer while
            # the transport underneath it hung on indefinitely.
            client_kwargs["http_client"] = httpx.AsyncClient(proxy=proxy, timeout=timeout)
        return AsyncOpenAI(**client_kwargs)

    async def _call_openai(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> LLMResponse:
        """Actual OpenAI API call.

        Sprint 1 used the async OpenAI SDK directly and Sprint 6 was to
        swap this for a router. The router arrived
        (:mod:`apps.llm.router`) and took over the pipeline and the
        concierge, but not this wrapper, which the intent router still
        falls back to when it is handed no tenant. So it stays, and it
        travels the same road as the router's provider — see
        :meth:`_build_client`.
        """

        client = self._build_client()
        try:
            response = await client.chat.completions.create(
                messages=messages,  # type: ignore[arg-type]
                model=model,
                **kwargs,
            )
        except Exception as exc:
            # DRF-1436 — an LLM refusal must never be silent. The circuit
            # breaker logs only on state TRANSITIONS, so before this line
            # the opening failures of an outage produced no journal entry
            # at all: the owner learned of the outage from the alert in
            # the chat and there was nothing to corroborate it with.
            # WARNING, not exception: the breaker and the caller decide
            # severity; this line only guarantees the failure left a mark.
            from apps.llm.health import redact_secrets

            logger.warning(
                "orchestrator.llm.openai.call_failed model=%s exc=%s msg=%s",
                model,
                type(exc).__name__,
                redact_secrets(str(exc))[:300],
            )
            raise
        finally:
            await self._aclose_client(client)

        choice = response.choices[0]
        return LLMResponse(
            content=choice.message.content or "",
            model=response.model,
            is_fallback=False,
            tokens_in=response.usage.prompt_tokens if response.usage else 0,
            tokens_out=response.usage.completion_tokens if response.usage else 0,
        )

    @staticmethod
    async def _aclose_client(client: Any) -> None:
        """Close the per-call SDK client and the httpx pool under it.

        The client is built per call (this provider is constructed per
        call by the intent router), so without this every classification
        leaked a connection pool. Best-effort: teardown must never turn a
        served answer into an error.
        """

        close = getattr(client, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 — teardown must never break the caller
            logger.warning("orchestrator.llm.openai.aclose_failed", exc_info=True)
