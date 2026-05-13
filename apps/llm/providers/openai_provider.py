"""OpenAI provider implementing :class:`apps.llm.protocol.LLMProvider`.

(DRF-581 / Sprint 7 / L2.)

Sprint 1 / D1 shipped ``apps.orchestrator.llm.openai_provider.OpenAIProvider``
— a circuit-breaker-wrapped wrapper around the OpenAI SDK. This module
is the Sprint 7 evolution: same vendor, same SDK underneath, but
exposes the provider-agnostic :class:`apps.llm.protocol.LLMProvider`
shape (``complete`` + ``embedding`` + ``ToolCall`` parsing) so the
L5 router can swap us out for Anthropic at run time.

### Why a parallel module instead of patching Sprint 1's class

* The Sprint 1 wrapper is wired into ``apps.orchestrator.intent_router``
  and a few other call sites that depend on its specific
  ``LLMResponse`` shape. Changing those mid-Sprint-7 would cascade
  through Sprint 6 tests.
* L1 (DRF-580) introduced new DTOs (``CompletionResult`` / ``ToolCall``)
  + new exception classes (``LLMError`` / ``LLMTransportError`` / …).
  A new module is cheaper to type than a heroic refactor.
* When Sprint 8 deprecates Sprint 1's wrapper, the call-site migration
  is mechanical (drop-in via the new Protocol).

### Tool-calling round-trip

The platform passes function-call specs in the canonical OpenAI shape
already (see :mod:`apps.llm.protocol` docstring). The conversion to
the SDK's ``tools`` kwarg is therefore a one-line wrap. The reverse
direction (``tool_calls`` → :class:`ToolCall`) is where the work is:

* parse the JSON ``arguments`` string into a dict
* malformed JSON raises :class:`LLMError` — the caller knows the
  LLM produced unusable output and can re-prompt / fall back
* multi-tool calls preserve order

### Proxy support

`api.openai.com` is blocked on Russian-hosted runners. The
constructor reads ``OPENAI_PROXY`` from settings and threads it into
the underlying httpx client. Same behaviour as Sprint 1 — preserved
so a parallel call to ``OpenAIProvider`` from Sprint 6 tests still
works through the same proxy.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings

from apps.llm.protocol import (
    CompletionResult,
    LLMError,
    LLMQuotaError,
    LLMTransportError,
    ToolCall,
)

logger = logging.getLogger(__name__)


_DEFAULT_COMPLETION_MODEL = "gpt-4o-mini"
_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class OpenAIProvider:
    """LLMProvider implementation backed by the async OpenAI SDK.

    Construction is cheap — the SDK client is lazily built on first
    call so module import doesn't fail on a missing API key in tests
    that mock the call entirely.
    """

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        proxy: str | None = None,
        default_completion_model: str = _DEFAULT_COMPLETION_MODEL,
        default_embedding_model: str = _DEFAULT_EMBEDDING_MODEL,
    ) -> None:
        self._api_key = api_key or getattr(settings, "OPENAI_API_KEY", "") or ""
        self._proxy = proxy if proxy is not None else getattr(settings, "OPENAI_PROXY", "") or ""
        self.default_completion_model = default_completion_model
        self.default_embedding_model = default_embedding_model
        # Cached SDK client — built on first call.
        self._client: Any = None

    # ------------------------------------------------------------------
    # LLMProvider — embedding
    # ------------------------------------------------------------------

    async def embedding(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> list[float]:
        """Embed ``text``. Returns the raw vector (no normalisation here —
        ChromaDB applies cosine on its end).
        """
        chosen_model = model or self.default_embedding_model
        client = self._get_client()
        try:
            response = await client.embeddings.create(model=chosen_model, input=text)
        except Exception as exc:
            self._reraise_as_llm_error(exc, op="embedding", model=chosen_model)
        return list(response.data[0].embedding)

    # ------------------------------------------------------------------
    # LLMProvider — complete
    # ------------------------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Chat completion + optional function-calling.

        Returns :class:`CompletionResult` regardless of whether the
        model emitted natural-language text, tool calls, or both.
        Malformed tool-call JSON raises :class:`LLMError` so the caller
        can fall back rather than silently drop the request.
        """
        chosen_model = model or self.default_completion_model
        client = self._get_client()

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            # Canonical OpenAI tool spec — wrap the platform-agnostic
            # specs into the SDK's `tools` envelope.
            kwargs["tools"] = [{"type": "function", "function": spec} for spec in tools]

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            self._reraise_as_llm_error(exc, op="complete", model=chosen_model)

        choice = response.choices[0]
        message = choice.message
        text = message.content or ""

        # Extract tool calls. message.tool_calls is None when the model
        # produced plain text.
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        parsed_tool_calls: list[ToolCall] = []
        for raw in raw_tool_calls:
            fn = raw.function
            try:
                args = json.loads(fn.arguments) if fn.arguments else {}
            except json.JSONDecodeError as exc:
                raise LLMError(
                    f"openai.complete: malformed tool_call JSON args (name={fn.name!r}): {exc}"
                ) from exc
            parsed_tool_calls.append(ToolCall(id=raw.id, name=fn.name, arguments=args))

        usage = getattr(response, "usage", None)
        return CompletionResult(
            text=text,
            tool_calls=parsed_tool_calls,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=response.model,
            provider=self.name,
            finish_reason=(choice.finish_reason or "").lower(),
        )

    # ------------------------------------------------------------------
    # SDK plumbing
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazy SDK client with optional HTTP proxy."""
        if self._client is not None:
            return self._client

        # Lazy import — the openai SDK is heavy and tests that mock the
        # provider entirely shouldn't pay the import cost.
        from openai import AsyncOpenAI  # type: ignore[import-not-found]

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._proxy:
            import httpx

            # AsyncOpenAI accepts an http_client override so we can pin
            # the proxy without leaking it into env globally.
            kwargs["http_client"] = httpx.AsyncClient(proxy=self._proxy)
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _reraise_as_llm_error(self, exc: Exception, *, op: str, model: str) -> None:
        """Map OpenAI SDK exceptions onto the L1 LLM* hierarchy.

        We classify by SDK class names (string-based to avoid pinning
        a hard dependency on the openai package internals; class names
        are stable across versions). Anything we don't recognise becomes
        a generic :class:`LLMError`.
        """
        exc_name = exc.__class__.__name__
        logger.warning(
            "llm.openai.%s_failed model=%s exc=%s msg=%s",
            op,
            model,
            exc_name,
            str(exc)[:200],
        )
        if exc_name in {"APIConnectionError", "APITimeoutError", "InternalServerError"}:
            raise LLMTransportError(f"openai.{op}: {exc_name}: {exc}") from exc
        if exc_name in {"RateLimitError"}:
            raise LLMQuotaError(f"openai.{op}: rate-limited: {exc}") from exc
        # Catch-all — unknown SDK error class.
        raise LLMError(f"openai.{op}: {exc_name}: {exc}") from exc
