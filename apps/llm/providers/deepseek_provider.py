"""DeepSeek provider implementing :class:`apps.llm.protocol.LLMProvider`.

(DRF-280 / Sprint 8 / EPIC-P T2.)

DeepSeek exposes an OpenAI-compatible HTTP API at
``https://api.deepseek.com/v1``, so we reuse the official ``openai``
Python SDK with a different ``base_url`` + ``DEEPSEEK_API_KEY``. The
provider class shape is therefore very close to
:class:`apps.llm.providers.openai_provider.OpenAIProvider`, minus the
embedding endpoint (DeepSeek does not publish one — :meth:`embedding`
raises :class:`NotImplementedError` so the L5 router can fall back to
OpenAI for embedding ops, same contract as Anthropic).

### Why a separate module instead of subclassing OpenAIProvider

Same rationale as L4 (AnthropicProvider) parallels L2 (OpenAIProvider):
the providers happen to share a transport (OpenAI SDK) but represent
different vendors with different pricing, tool aliases, model IDs, and
quota behaviour. Subclassing would couple their evolution; module-per-
provider keeps Sprint-N changes localised.

### Model IDs (per DRF-279 spike, 2026-05-18)

Primary humanizer recommendation: ``deepseek-v4-flash`` ($0.14 / $0.28
per 1M tokens). The legacy aliases ``deepseek-chat`` and
``deepseek-reasoner`` are deprecated on **2026-07-24** per the
vendor's pricing page — we target ``v4-flash`` / ``v4-pro`` IDs
directly so the migration window doesn't bite us. Both aliases are
kept in :mod:`apps.llm.pricing.MODEL_PRICES` as transitional safety
net (calls in flight when the alias dies will at least cost-account
correctly until the L5 router config is bumped).

### Proxy support

Unlike ``api.openai.com``, ``api.deepseek.com`` is reachable from
Russian-hosted runners without a proxy (per the DRF-279 spike: «available
in Russia without a VPN and works stably»). We still honour
``DEEPSEEK_PROXY`` for unusual network configurations (corporate egress,
testing), falling back to no proxy when the env var is unset. Note we
deliberately do NOT fall back to ``OPENAI_PROXY`` here — that proxy is
tuned for openai.com and routing DeepSeek through it would defeat the
whole "directly accessible from RU" advantage.

### Tool-calling round-trip

DeepSeek's tool spec is byte-identical to OpenAI's (``{"type":
"function", "function": {name, description, parameters}}``) and the
return shape matches: ``message.tool_calls[*].function.{name, arguments}``
with ``arguments`` as a JSON string. The parsing block below is the
same as :mod:`apps.llm.providers.openai_provider`. If a future DeepSeek
release diverges, this provider's parsing block becomes the migration
seam.

### 152-ФЗ caveat

DeepSeek data centres are in China. Prompts containing PII
(client name, phone, health screening answers) MUST NOT be routed
through this provider without the PII-scrubbing layer (per DRF-279
spike §5). The L5 router config splits humanizer skill into
``humanizer`` (DeepSeek/Qwen) and ``humanizer_pii_safe`` (Yandex /
GigaChat) chains; **enforcement of that split is the router's job**,
not this provider's.
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
from apps.llm.retry import (
    RetriableLLMError,
    RetryPolicy,
    is_retriable_openai,
    policy_from_settings,
    run_with_retry,
    write_retry_attempt_audit,
)
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


# Per DRF-279 spike §1: ``v4-flash`` is the primary humanizer recommendation.
# The legacy ``deepseek-chat`` / ``deepseek-reasoner`` aliases deprecate on
# 2026-07-24; targeting the v4 IDs directly avoids the forced migration.
_DEFAULT_COMPLETION_MODEL = "deepseek-v4-flash"

# Official OpenAI-compatible base URL — documented at
# https://api-docs.deepseek.com/quick_start. The ``/v1`` path is part of
# the URL (the SDK appends ``chat/completions`` etc.).
_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


class DeepSeekProvider:
    """LLMProvider implementation for DeepSeek via OpenAI-compatible API.

    Construction is cheap — the SDK client is lazily built on first
    call so module import doesn't fail on a missing API key in tests
    that mock the call entirely.
    """

    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        proxy: str | None = None,
        base_url: str = _DEEPSEEK_BASE_URL,
        default_completion_model: str = _DEFAULT_COMPLETION_MODEL,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._api_key = api_key or getattr(settings, "DEEPSEEK_API_KEY", "") or ""
        # No fallback to OPENAI_PROXY by design — see module docstring.
        self._proxy = proxy if proxy is not None else getattr(settings, "DEEPSEEK_PROXY", "") or ""
        self._base_url = base_url
        self.default_completion_model = default_completion_model
        # Cached SDK client — built on first call.
        self._client: Any = None
        self._retry_policy: RetryPolicy | None = retry_policy

    # ------------------------------------------------------------------
    # LLMProvider — embedding (NOT supported)
    # ------------------------------------------------------------------

    async def embedding(
        self,
        text: str,
        *,
        model: str | None = None,
    ) -> list[float]:
        """DeepSeek does not publish an embeddings endpoint.

        The L5 router (DRF-587) catches :class:`NotImplementedError` and
        falls back to OpenAI for embedding ops regardless of which
        provider would otherwise be chosen. Symmetric with
        :meth:`apps.llm.providers.anthropic_provider.AnthropicProvider.embedding`.
        """
        raise NotImplementedError(
            "DeepSeek does not expose an embeddings endpoint. The L5 router "
            "falls back to OpenAI for embedding operations."
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
        """Chat completion + optional function-calling via DeepSeek.

        Returns :class:`CompletionResult` regardless of whether the
        model emitted natural-language text, tool calls, or both.
        Malformed tool-call JSON raises :class:`LLMError` so the caller
        can fall back rather than silently drop the request.
        """
        chosen_model = model or self.default_completion_model

        # Phase 1 / PI9 (DRF-860) — per-tenant cost cap pre-call gate.
        await _enforce_tenant_caps()

        client = self._get_client()

        kwargs: dict[str, Any] = {
            "model": chosen_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools:
            # DeepSeek mirrors OpenAI's tool spec verbatim.
            kwargs["tools"] = [{"type": "function", "function": spec} for spec in tools]

        # Same retry semantics as OpenAI — the SDK exception classes
        # surface identically since DeepSeek is reached through the
        # ``openai`` Python SDK.
        async def _do_completion() -> Any:
            return await client.chat.completions.create(**kwargs)

        try:
            response = await run_with_retry(
                _do_completion,
                policy=self._get_retry_policy(),
                is_retriable=is_retriable_openai,
                on_attempt_failed=self._make_on_attempt_failed(op="complete", model=chosen_model),
            )
        except RetriableLLMError:
            raise
        except Exception as exc:
            self._reraise_as_llm_error(exc, op="complete", model=chosen_model)

        choice = response.choices[0]
        message = choice.message
        text = message.content or ""

        raw_tool_calls = getattr(message, "tool_calls", None) or []
        parsed_tool_calls: list[ToolCall] = []
        for raw in raw_tool_calls:
            fn = raw.function
            try:
                args = json.loads(fn.arguments) if fn.arguments else {}
            except json.JSONDecodeError as exc:
                raise LLMError(
                    f"deepseek.complete: malformed tool_call JSON args (name={fn.name!r}): {exc}"
                ) from exc
            parsed_tool_calls.append(ToolCall(id=raw.id, name=fn.name, arguments=args))

        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        actual_model = getattr(response, "model", chosen_model) or chosen_model

        await _record_tenant_usage(
            model=actual_model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
        )

        return CompletionResult(
            text=text,
            tool_calls=parsed_tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=actual_model,
            provider=self.name,
            finish_reason=(choice.finish_reason or "").lower(),
        )

    # ------------------------------------------------------------------
    # SDK plumbing
    # ------------------------------------------------------------------

    def _get_retry_policy(self) -> RetryPolicy:
        """Lazy retry-policy resolution. Same pattern as OpenAIProvider."""
        if self._retry_policy is not None:
            return self._retry_policy
        self._retry_policy = policy_from_settings()
        return self._retry_policy

    def _make_on_attempt_failed(self, *, op: str, model: str) -> Any:
        """Build the per-attempt audit hook closure for ``run_with_retry``."""
        provider_name = self.name

        async def _hook(attempt: int, exc: BaseException) -> None:
            await write_retry_attempt_audit(
                provider=provider_name,
                model=model,
                op=op,
                attempt=attempt,
                exc=exc,
            )

        return _hook

    def _get_client(self) -> Any:
        """Lazy SDK client pointed at the DeepSeek base URL."""
        if self._client is not None:
            return self._client

        # Lazy import — same rationale as openai_provider.
        from openai import AsyncOpenAI  # type: ignore[import-not-found]

        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "base_url": self._base_url,
        }
        if self._proxy:
            import httpx

            kwargs["http_client"] = httpx.AsyncClient(proxy=self._proxy)
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _reraise_as_llm_error(self, exc: Exception, *, op: str, model: str) -> None:
        """Map OpenAI SDK exceptions onto the L1 LLM* hierarchy.

        Same class-name classification as OpenAIProvider — DeepSeek goes
        through the same SDK so exception classes are identical.
        """
        exc_name = exc.__class__.__name__
        logger.warning(
            "llm.deepseek.%s_failed model=%s exc=%s msg=%s",
            op,
            model,
            exc_name,
            str(exc)[:200],
        )
        if exc_name in {"APIConnectionError", "APITimeoutError", "InternalServerError"}:
            raise LLMTransportError(f"deepseek.{op}: {exc_name}: {exc}") from exc
        if exc_name in {"RateLimitError"}:
            raise LLMQuotaError(f"deepseek.{op}: rate-limited: {exc}") from exc
        raise LLMError(f"deepseek.{op}: {exc_name}: {exc}") from exc


# ---------------------------------------------------------------------------
# Phase 1 / PI9 (DRF-860) — per-tenant cost-cap glue
#
# Duplicated from openai_provider.py / anthropic_provider.py per the
# established pattern (one set per provider module). A future refactor
# can dedupe into apps.llm._provider_helpers — out of scope for DRF-280.
# ---------------------------------------------------------------------------


async def _enforce_tenant_caps() -> None:
    """Forward to :func:`apps.llm.cost_tracker.enforce_caps` when a
    tenant is in scope. Skipped when ``current_tenant()`` is None
    (system context, tests).
    """
    tenant = current_tenant()
    if tenant is None:
        return
    from apps.llm.cost_tracker import enforce_caps

    await enforce_caps(str(tenant.id))


async def _record_tenant_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Compute cost via :mod:`apps.llm.pricing` and forward to
    :func:`apps.llm.cost_tracker.record_usage`.

    Unknown-model pricing failure → log + record with cost=0 so the
    surrounding LLM call never breaks on a missing price-table entry.
    """
    tenant = current_tenant()
    if tenant is None:
        return
    from apps.llm.cost_tracker import record_usage
    from apps.llm.pricing import UnknownModelError, compute_cost

    try:
        cost_usd = compute_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except UnknownModelError:
        logger.warning(
            "llm.deepseek.cost_unknown_model model=%s — usage recorded with cost=0",
            model,
        )
        cost_usd = _ZERO_DECIMAL

    await record_usage(
        str(tenant.id),
        tokens=max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0)),
        cost_usd=cost_usd,
    )


# Module-level zero so the cost-fallback path doesn't allocate a fresh
# Decimal on each call. Matches openai_provider / anthropic_provider style.
from decimal import Decimal as _Decimal  # noqa: E402 — bottom-of-file constant

_ZERO_DECIMAL: _Decimal = _Decimal(0)
