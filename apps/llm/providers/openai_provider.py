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

import inspect
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
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._api_key = api_key or getattr(settings, "OPENAI_API_KEY", "") or ""
        self._proxy = proxy if proxy is not None else getattr(settings, "OPENAI_PROXY", "") or ""
        self.default_completion_model = default_completion_model
        self.default_embedding_model = default_embedding_model
        # Cached SDK client — built on first call.
        self._client: Any = None
        # Phase 1 / PI7 (DRF-858) — retry policy. Lazily defaulted on
        # first use so unit tests that construct a provider without a
        # Django settings module still work (the test suite has
        # settings, but the dataclass-default pattern matches the rest
        # of this provider's lazy initialisation).
        self._retry_policy: RetryPolicy | None = retry_policy

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

        Phase 1 / PI9 (DRF-860): embedding calls count toward the per-tenant
        daily token + cost cap too. Small per-call, but RAG hot paths can
        run hundreds of embeddings in a minute — without capturing them
        a runaway retrieval loop could outrun the cap entirely.
        """
        chosen_model = model or self.default_embedding_model
        await _enforce_tenant_caps(projected_tokens=_estimate_embedding_tokens(text))

        client = self._get_client()

        # Phase 1 / PI7 (DRF-858) — wrap the SDK call in exponential-
        # backoff retry. RAG bulk reindex (G2) can fire hundreds of
        # embedding calls in a minute; 429s on this hot path were a
        # major source of "не могу ответить" in incident reviews.
        async def _do_embedding() -> Any:
            return await client.embeddings.create(model=chosen_model, input=text)

        try:
            response = await run_with_retry(
                _do_embedding,
                policy=self._get_retry_policy(),
                is_retriable=is_retriable_openai,
                on_attempt_failed=self._make_on_attempt_failed(op="embedding", model=chosen_model),
            )
        except RetriableLLMError:
            # Retries exhausted — propagate up. The orchestrator's
            # outer boundary catches this and serves the static
            # fallback + writes audit + emits manager alert.
            raise
        except Exception as exc:
            self._reraise_as_llm_error(exc, op="embedding", model=chosen_model)

        # Embedding endpoint usage block — present in modern OpenAI SDK
        # responses. Total tokens = prompt_tokens (no completion side).
        usage = getattr(response, "usage", None)
        used_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if used_tokens == 0:
            used_tokens = _estimate_embedding_tokens(text)
        await _record_tenant_usage(
            model=chosen_model,
            input_tokens=used_tokens,
            output_tokens=0,
        )

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
        tool_choice: str | None = None,
    ) -> CompletionResult:
        """Chat completion + optional function-calling.

        Returns :class:`CompletionResult` regardless of whether the
        model emitted natural-language text, tool calls, or both.
        Malformed tool-call JSON raises :class:`LLMError` so the caller
        can fall back rather than silently drop the request.

        ``tool_choice`` (DRF-1286) is the canonical protocol string
        (``"auto"`` / ``"required"`` / ``"none"``) — OpenAI's Chat
        Completions API takes exactly these values, so it passes through
        verbatim. Ignored without ``tools`` (OpenAI rejects the pair).
        """
        chosen_model = model or self.default_completion_model

        # Phase 1 / PI9 (DRF-860) — per-tenant cost cap pre-call gate.
        # Same call shape on both providers via the shared cost_tracker
        # module. Raises TenantQuotaExceeded; orchestrator catches and
        # serves the static Russian fallback. LLM retro B2: gate
        # reserves a worst-case cost so concurrent calls can't all
        # read "under cap" and overshoot. Reservation threaded back
        # to record_usage below.
        reservation = await _enforce_tenant_caps(
            model=chosen_model,
            max_completion_tokens=max_tokens,
        )

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
            # DRF-1286 — forced tool use. Only meaningful alongside
            # `tools`; OpenAI 400s on `tool_choice` without them.
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

        # Phase 1 / PI7 (DRF-858) — wrap the SDK call in exponential-
        # backoff retry. ~95% of OpenAI 429 / 5xx are transient and
        # retry-successful within 1-2 attempts; absorbing them here
        # spares the user the static "не могу ответить" fallback.
        # Retries do NOT double-charge the tenant cap — record_usage
        # runs ONCE on the final successful response below.
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
            # Retries exhausted — propagate to orchestrator outer boundary.
            raise
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
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        actual_model = getattr(response, "model", chosen_model) or chosen_model

        # Phase 1 / PI9 (DRF-860) — post-call accounting. Use the
        # vendor-resolved model id (response.model echoes the actual
        # variant served, which may differ from the alias the caller
        # passed). compute_cost falls back gracefully to chosen_model
        # when the table doesn't know the resolved variant.
        await _record_tenant_usage(
            model=actual_model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            reservation=reservation,
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

    # ------------------------------------------------------------------
    # Retry helpers — Phase 1 / PI7 (DRF-858)
    # ------------------------------------------------------------------

    def _get_retry_policy(self) -> RetryPolicy:
        """Lazy retry-policy resolution.

        Constructor-injected policy wins; otherwise we read from
        Django settings on first use. Lazy because tests that
        construct a provider without entering settings context still
        work (rare but historically supported).
        """
        if self._retry_policy is not None:
            return self._retry_policy
        self._retry_policy = policy_from_settings()
        return self._retry_policy

    def _make_on_attempt_failed(self, *, op: str, model: str) -> Any:
        """Build the per-attempt audit hook closure for ``run_with_retry``.

        Closure pattern lets us bind ``op`` + ``model`` into the
        callback without changing :func:`run_with_retry`'s signature
        (which takes a fixed ``(attempt, exc)`` callable).
        """
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
        """Lazy SDK client with optional HTTP proxy."""
        if self._client is not None:
            return self._client

        # Lazy import — the openai SDK is heavy and tests that mock the
        # provider entirely shouldn't pay the import cost.
        from openai import AsyncOpenAI  # type: ignore[import-not-found]

        timeout = getattr(settings, "LLM_REQUEST_TIMEOUT_S", 30.0)
        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": timeout,
            # DRF-989: disable SDK-level retries. Our retry layer in
            # apps.llm.retry owns the policy, audit hooks, and budget.
            "max_retries": 0,
        }
        if self._proxy:
            import httpx

            # AsyncOpenAI accepts an http_client override so we can pin
            # the proxy without leaking it into env globally. The proxy
            # client must carry the same timeout so it can't hang either.
            kwargs["http_client"] = httpx.AsyncClient(proxy=self._proxy, timeout=timeout)
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def aclose(self) -> None:
        """Close the cached SDK client (and its httpx pool), if built.

        Long-lived callers (the web process, the consumer) keep one
        provider for the process lifetime and never need this. Short-lived
        callers that construct a provider per call — notably the DRF-1054
        availability probe, which deliberately builds a FRESH client each
        tick so it exercises a fresh proxy CONNECT — must close it, or
        every tick leaks an ``httpx.AsyncClient`` plus its connection pool
        inside the Celery worker.

        Best-effort and idempotent: never raises, safe on an unbuilt or
        already-closed client.
        """
        client = self._client
        if client is None:
            return
        self._client = None
        close = getattr(client, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 — teardown must never break the caller
            logger.warning("llm.openai.aclose_failed", exc_info=True)

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


# ---------------------------------------------------------------------------
# Phase 1 / PI9 (DRF-860) — per-tenant cost-cap glue
# ---------------------------------------------------------------------------


# LLM retro B2: imported from the cost_tracker module — shared with
# the Anthropic provider via the cost_tracker constant. See reviewer
# Y3 (extract-to-shared) note.


async def _enforce_tenant_caps(
    *,
    projected_tokens: int = 0,
    model: str | None = None,
    max_completion_tokens: int | None = None,
):
    """Call into the shared cost_tracker if a tenant is in scope.

    Skips the gate entirely when ``current_tenant()`` is None — that
    means we're outside any request/worker context (typically the
    test suite or an admin shell). The gate exists to protect
    operational tenants; system-context calls (migrations, management
    commands without tenant_scope) shouldn't be billed.

    LLM retro B2: when ``model`` is provided we compute a worst-case
    ``estimated_cost_usd`` (= price(model, projected_tokens,
    max_completion_tokens or default)) and pass it to
    :func:`enforce_caps` to enable the reserve-then-record path. The
    returned :class:`CostReservation` is threaded back to
    :func:`_record_tenant_usage` so the post-call accounting refunds
    the over-estimate.

    Returns the reservation when one is made, or ``None`` for the
    no-scope skip.
    """
    tenant = current_tenant()
    if tenant is None:
        return None
    from apps.llm.cost_tracker import enforce_caps
    from apps.llm.pricing import UnknownModelError, compute_cost

    estimated_cost_usd: _Decimal | None = None
    if model:
        from apps.llm.cost_tracker import RESERVATION_DEFAULT_MAX_TOKENS

        try:
            estimated_cost_usd = compute_cost(
                model,
                input_tokens=max(0, projected_tokens),
                output_tokens=max_completion_tokens or RESERVATION_DEFAULT_MAX_TOKENS,
            )
        except UnknownModelError:
            # Unknown model in pricing table → skip reservation; gate
            # still runs the token-cap check + legacy cost-cap soft-check.
            estimated_cost_usd = None

    return await enforce_caps(
        str(tenant.id),
        projected_tokens=projected_tokens,
        estimated_cost_usd=estimated_cost_usd,
    )


async def _record_tenant_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    reservation=None,
) -> None:
    """Compute cost via :mod:`apps.llm.pricing` and forward to
    :func:`apps.llm.cost_tracker.record_usage`.

    Unknown-model pricing failure → log + skip. We never want a missing
    price-table entry to break the surrounding LLM call — the cost cap
    will under-count for that one call and the cost-table audit (CI
    test for compute_cost) will surface the omission separately.

    ``reservation`` is the :class:`CostReservation` returned by the
    matching :func:`_enforce_tenant_caps` call. Threading it through
    enables the LLM retro B2 reserve-then-record reconciliation
    (refund the over-estimate / top up the under-estimate).
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
            "llm.openai.cost_unknown_model model=%s — usage recorded with cost=0",
            model,
        )
        cost_usd = _ZERO_DECIMAL

    await record_usage(
        str(tenant.id),
        tokens=max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0)),
        cost_usd=cost_usd,
        reservation=reservation,
    )


def _estimate_embedding_tokens(text: str) -> int:
    """Rough token estimate for embedding-call pre-gate budgeting.

    Embedding endpoints don't expose a "would-be" usage; we estimate
    1 token per 4 characters (the OpenAI rule-of-thumb for English /
    Cyrillic mixed text). Used ONLY for the pre-call ``projected_tokens``
    overshoot guard — the post-call ``record_usage`` always uses the
    actual ``response.usage.total_tokens`` so the counter is exact.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


# Module-level zero so the cost-fallback path doesn't allocate a fresh
# Decimal on each call.
from decimal import Decimal as _Decimal  # noqa: E402 — bottom-of-file constant

_ZERO_DECIMAL: _Decimal = _Decimal(0)
