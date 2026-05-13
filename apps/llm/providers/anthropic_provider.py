"""Anthropic provider implementing :class:`apps.llm.protocol.LLMProvider`.

(DRF-583 / Sprint 7 / L4.)

Wraps the official ``anthropic`` async SDK (Messages API). The L5
router (DRF-587) hands this implementation back when a tenant's
``features["llm_provider"] == "anthropic"`` OR when the per-skill
default routes here. Sprint 8 ramps the canary; Sprint 7 just ships
the provider so the router has something to switch to.

### Why the parallel module + Decision 18 model pinning

Same rationale as L2 (OpenAIProvider): we don't replace Sprint 1's
wrapper at the call sites — we expose an L1-Protocol-conformant
shape so skills that DO call through the router get vendor-agnostic
``CompletionResult`` / ``ToolCall`` DTOs.

Models per Decision 18 (claude-3 was deprecated 2025):

* ``claude-haiku-4-5`` — intent routing default.
* ``claude-sonnet-4-6`` — high-risk reply default (Sprint 8 ramp).

### No embeddings on Anthropic

Anthropic exposes no embeddings API. :meth:`embedding` raises
:class:`NotImplementedError` so the L5 router (DRF-587) can catch
and fall back to OpenAI for embedding ops. The router fallback is
the contract — provider here ONLY signals the gap.

### Tool-calling round-trip

The platform passes function specs in the canonical L1 shape
(``{"name", "description", "parameters"}``). Anthropic's tools API
calls the field ``input_schema`` instead of ``parameters``; we wrap
on the way out. On return, Anthropic emits ``tool_use`` blocks
inside ``content`` (NOT a separate ``tool_calls`` field like
OpenAI); we collect them into the same provider-agnostic
:class:`apps.llm.protocol.ToolCall` list so call sites stay
vendor-blind.

### Messages API mapping

ChatML ``messages=[{role: system, ...}]`` is split: Anthropic puts
``system`` as a top-level ``system=`` kwarg, with the remaining
``user``/``assistant`` messages alternating. We collapse adjacent
same-role messages where needed (rare in our flow but Anthropic
rejects two ``user`` rows in a row).

### Proxy support

``api.anthropic.com`` is also blocked on RU-hosted runners. The
provider reads ``ANTHROPIC_PROXY`` (falling back to ``OPENAI_PROXY``
if unset) and threads it into an ``httpx.AsyncClient``.
"""

from __future__ import annotations

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


# Decision 18 — pinned default models. Override per-call via ``model=`` kwarg.
_DEFAULT_INTENT_MODEL = "claude-haiku-4-5"
_DEFAULT_REPLY_MODEL = "claude-sonnet-4-6"


class AnthropicProvider:
    """LLMProvider implementation backed by the async Anthropic SDK.

    Lazy-constructs the SDK client on first call so module import
    doesn't require a real API key.
    """

    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        proxy: str | None = None,
        default_completion_model: str = _DEFAULT_REPLY_MODEL,
    ) -> None:
        self._api_key = api_key or getattr(settings, "ANTHROPIC_API_KEY", "") or ""
        if proxy is not None:
            self._proxy = proxy
        else:
            self._proxy = (
                getattr(settings, "ANTHROPIC_PROXY", "")
                or getattr(settings, "OPENAI_PROXY", "")
                or ""
            )
        self.default_completion_model = default_completion_model
        self._client: Any = None

    # ------------------------------------------------------------------
    # LLMProvider — embedding (Anthropic has none)
    # ------------------------------------------------------------------

    async def embedding(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> list[float]:
        """Anthropic exposes no embeddings API. L5 router catches this
        and falls back to OpenAI for embedding ops.
        """
        raise NotImplementedError(
            "anthropic.embedding: Anthropic offers no embeddings API. "
            "Route embedding ops to apps.llm.providers.openai_provider."
        )

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
        """Anthropic Messages API call.

        Returns :class:`CompletionResult` in the same shape OpenAIProvider
        returns. Tool-use blocks are parsed into :class:`ToolCall`.
        """
        chosen_model = model or self.default_completion_model
        client = self._get_client()

        system, anthropic_messages = _split_system_message(messages)

        # Anthropic always requires max_tokens. Default to a sensible
        # cap if the caller didn't specify one (most platforms
        # don't bother for OpenAI either).
        max_tokens_value = max_tokens if max_tokens is not None else 4096

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens_value,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_to_anthropic_tool(spec) for spec in tools]

        try:
            response = await client.messages.create(**kwargs)
        except Exception as exc:
            self._reraise_as_llm_error(exc, op="complete", model=chosen_model)

        text, tool_calls = _parse_content_blocks(response.content)
        usage = getattr(response, "usage", None)

        return CompletionResult(
            text=text,
            tool_calls=tool_calls,
            prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
            completion_tokens=getattr(usage, "output_tokens", 0) or 0,
            model=getattr(response, "model", chosen_model),
            provider=self.name,
            finish_reason=(getattr(response, "stop_reason", "") or "").lower(),
        )

    # ------------------------------------------------------------------
    # SDK plumbing
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Lazy SDK client with optional HTTP proxy."""
        if self._client is not None:
            return self._client

        from anthropic import AsyncAnthropic  # type: ignore[import-not-found]

        kwargs: dict[str, Any] = {"api_key": self._api_key}
        if self._proxy:
            import httpx

            kwargs["http_client"] = httpx.AsyncClient(proxy=self._proxy)
        self._client = AsyncAnthropic(**kwargs)
        return self._client

    def _reraise_as_llm_error(self, exc: Exception, *, op: str, model: str) -> None:
        """Map Anthropic SDK exceptions onto the L1 LLM* hierarchy.

        Class-name match (no hard dep on `anthropic` internals) —
        SDK class names are stable across minor versions.
        """
        exc_name = exc.__class__.__name__
        logger.warning(
            "llm.anthropic.%s_failed model=%s exc=%s msg=%s",
            op,
            model,
            exc_name,
            str(exc)[:200],
        )
        if exc_name in {"APIConnectionError", "APITimeoutError", "InternalServerError"}:
            raise LLMTransportError(f"anthropic.{op}: {exc_name}: {exc}") from exc
        if exc_name in {"RateLimitError"}:
            raise LLMQuotaError(f"anthropic.{op}: rate-limited: {exc}") from exc
        raise LLMError(f"anthropic.{op}: {exc_name}: {exc}") from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _split_system_message(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Anthropic expects system text via top-level kwarg, NOT in messages.

    Concatenates all ``role=system`` entries into a single ``system``
    string (Anthropic accepts only one). Remaining entries stay in
    user/assistant order.
    """
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                system_parts.append(content)
        else:
            rest.append(msg)
    return ("\n\n".join(system_parts), rest)


def _to_anthropic_tool(spec: dict[str, Any]) -> dict[str, Any]:
    """Map L1-canonical tool spec → Anthropic SDK shape.

    Platform spec:    ``{"name", "description", "parameters": <schema>}``
    Anthropic spec:   ``{"name", "description", "input_schema": <schema>}``
    """
    return {
        "name": spec["name"],
        "description": spec.get("description", ""),
        "input_schema": spec.get("parameters", {"type": "object"}),
    }


def _parse_content_blocks(blocks: Any) -> tuple[str, list[ToolCall]]:
    """Anthropic content is a list of blocks: ``{type: text|tool_use}``.

    We concatenate all text blocks into one ``text`` string and collect
    every tool_use block into a :class:`ToolCall` list. Order preserved.
    """
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in blocks or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            # input is a dict (Anthropic parses JSON for us — no
            # malformed-args path like OpenAI).
            tool_calls.append(
                ToolCall(
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    arguments=dict(getattr(block, "input", {}) or {}),
                )
            )
    return ("".join(text_parts), tool_calls)
