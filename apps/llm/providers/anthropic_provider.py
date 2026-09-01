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

Forced tool use (DRF-1286) is likewise renamed, not missing:
OpenAI's ``tool_choice="required"`` is Anthropic's
``tool_choice={"type": "any"}`` — «use one of the supplied tools,
your choice which». :data:`_TOOL_CHOICE_MAP` holds the translation
so a caller passing the canonical protocol string gets forced tool
use on BOTH providers. This matters because the provider is an
operator switch (``SKILL_LLM_PROVIDER``): a feature that silently
no-ops after a flip is the defect class this mapping prevents.

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
from datetime import datetime, timezone
from typing import Any

from django.conf import settings
from django.core.cache import cache

from apps.audit.services import write_audit
from apps.events.services import emit
from apps.llm.protocol import (
    CompletionResult,
    LLMError,
    LLMProviderQuotaExceeded,
    LLMQuotaError,
    LLMTransportError,
    LLMVendorCreditsExhausted,
    ToolCall,
)
from apps.llm.retry import (
    RetriableLLMError,
    RetryPolicy,
    is_retriable_anthropic,
    is_vendor_quota_exhausted,
    policy_from_settings,
    run_with_retry,
    write_retry_attempt_audit,
)
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)


# Decision 18 — pinned default models. Override per-call via ``model=`` kwarg.
_DEFAULT_INTENT_MODEL = "claude-haiku-4-5"
_DEFAULT_REPLY_MODEL = "claude-sonnet-4-6"


# DRF-1286 — canonical protocol ``tool_choice`` string → Anthropic's
# object form. ``"required"`` → ``{"type": "any"}`` is the forced-tool-use
# equivalent: the model must emit a tool_use block, but picks which tool.
# (``{"type": "tool", "name": ...}`` would pin a specific tool — not what
# the canonical string means, so it is deliberately unmapped.)
_TOOL_CHOICE_MAP: dict[str, dict[str, str]] = {
    "auto": {"type": "auto"},
    "required": {"type": "any"},
    "none": {"type": "none"},
}


# Sprint 7 / L7 (DRF-585) — Redis key + TTL for daily token counter.
# 24-hour TTL with UTC midnight ISO date in the key gives a natural
# rollover: the new day's key doesn't exist until the first call of
# the new UTC day, so a brief race where the previous key is still
# alive doesn't matter — they never overlap by ISO date.
_TOKEN_COUNTER_KEY_PREFIX = "anthropic_tokens:"
_TOKEN_COUNTER_TTL_SECONDS = 24 * 60 * 60

EVENT_PROVIDER_QUOTA_EXCEEDED = "llm.provider_quota_exceeded"


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
        retry_policy: RetryPolicy | None = None,
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
        # Phase 1 / PI7 (DRF-858) — retry policy. Lazy-defaulted on
        # first use (same pattern as OpenAIProvider) so tests that
        # build a provider outside Django settings still work.
        self._retry_policy: RetryPolicy | None = retry_policy

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
        tool_choice: str | None = None,
    ) -> CompletionResult:
        """Anthropic Messages API call with L7 daily-token cap.

        ``tool_choice`` (DRF-1286) takes the canonical protocol string and
        is translated through :data:`_TOOL_CHOICE_MAP`. An unknown value is
        dropped with a WARNING rather than forwarded — Anthropic rejects a
        bare string with 400, and a hard failure here would turn a caller
        typo into a customer-visible LLM error.

        Returns :class:`CompletionResult`; raises
        :class:`apps.llm.protocol.LLMProviderQuotaExceeded` when our
        configured daily token cap has been crossed BEFORE this call
        would land. The L5 router catches that exception and falls
        back to OpenAI on the next ``get_provider(prefer_fallback_from=
        "anthropic")`` call.
        """
        chosen_model = model or self.default_completion_model

        # Phase 1 / PI9 (DRF-860) — per-tenant cost cap. Runs BEFORE
        # the existing L7 per-model org-wide cap so tenants whose own
        # budget is already exhausted see TenantQuotaExceeded (caught
        # by the orchestrator → graceful fallback) instead of the
        # router-fallback LLMProviderQuotaExceeded. Both caps coexist:
        # tenant cap protects per-customer billing, model cap protects
        # the org-wide Anthropic spend. LLM retro B2: reservation
        # threaded back to record_usage to refund the over-estimate.
        reservation = await _enforce_tenant_caps(
            model=chosen_model,
            max_completion_tokens=max_tokens,
        )

        # L7 — pre-call quota gate. Read the current counter; if at
        # or above cap, refuse the call rather than burning more
        # tokens. We choose pre-call (not post-call) so an in-flight
        # batch can't push us 50%+ over budget.
        await _enforce_daily_token_cap(chosen_model)

        client = self._get_client()
        system, anthropic_messages = _split_system_message(messages)

        # Anthropic always requires max_tokens. Default to a sensible
        # cap if the caller didn't specify one.
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
            # DRF-1286 — forced tool use. Only meaningful alongside
            # `tools`; Anthropic 400s on `tool_choice` without them.
            if tool_choice:
                mapped = _TOOL_CHOICE_MAP.get(tool_choice)
                if mapped is None:
                    logger.warning(
                        "anthropic.complete: unsupported tool_choice=%r — ignored (supported: %s)",
                        tool_choice,
                        sorted(_TOOL_CHOICE_MAP),
                    )
                else:
                    kwargs["tool_choice"] = mapped

        # Phase 1 / PI7 (DRF-858) — wrap the SDK call in exponential-
        # backoff retry. Symmetric with OpenAIProvider — same policy,
        # same audit shape, anthropic-specific predicate.
        async def _do_completion() -> Any:
            return await client.messages.create(**kwargs)

        try:
            response = await run_with_retry(
                _do_completion,
                policy=self._get_retry_policy(),
                is_retriable=is_retriable_anthropic,
                on_attempt_failed=self._make_on_attempt_failed(op="complete", model=chosen_model),
            )
        except RetriableLLMError:
            raise
        except Exception as exc:
            self._reraise_as_llm_error(exc, op="complete", model=chosen_model)

        text, tool_calls = _parse_content_blocks(response.content)
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        # L7 — post-call accounting. INCR by the actual tokens used so
        # the next caller sees the up-to-date counter.
        _record_token_usage(prompt_tokens + completion_tokens)

        # Phase 1 / PI9 (DRF-860) — per-tenant accounting. Cost
        # computed against the vendor-resolved model id. LLM retro B2
        # reservation threaded through for reserve-then-record
        # reconciliation.
        actual_model = getattr(response, "model", chosen_model) or chosen_model
        await _record_tenant_usage(
            model=actual_model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            reservation=reservation,
        )

        return CompletionResult(
            text=text,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=getattr(response, "model", chosen_model),
            provider=self.name,
            finish_reason=(getattr(response, "stop_reason", "") or "").lower(),
        )

    # ------------------------------------------------------------------
    # SDK plumbing
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Retry helpers — Phase 1 / PI7 (DRF-858)
    # ------------------------------------------------------------------

    def _get_retry_policy(self) -> RetryPolicy:
        """Lazy retry-policy resolution (mirror of OpenAIProvider)."""
        if self._retry_policy is not None:
            return self._retry_policy
        self._retry_policy = policy_from_settings()
        return self._retry_policy

    def _make_on_attempt_failed(self, *, op: str, model: str) -> Any:
        """Build the audit hook closure used by :func:`run_with_retry`."""
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

        from anthropic import AsyncAnthropic  # type: ignore[import-not-found]

        timeout = getattr(settings, "LLM_REQUEST_TIMEOUT_S", 30.0)
        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": timeout,
            # DRF-989: disable SDK-level retries symmetric with OpenAI.
            # apps.llm.retry owns the retry policy and budget.
            "max_retries": 0,
        }
        if self._proxy:
            import httpx

            kwargs["http_client"] = httpx.AsyncClient(proxy=self._proxy, timeout=timeout)
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
        # DRF-1437 — checked BEFORE the RateLimitError branch below,
        # because a drained account balance arrives AS a RateLimitError.
        # Mapping it to LLMQuotaError ("slow down, retry later") is what
        # kept the 2026-08-31 pilot silent: the error was neither
        # retryable-usefully nor recognisable as "hop to another vendor".
        if is_vendor_quota_exhausted(exc):
            raise LLMVendorCreditsExhausted(
                f"anthropic.{op}: vendor credits exhausted: {exc}"
            ) from exc
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


def _today_key() -> str:
    """Today's UTC ISO date as the Redis key suffix.

    Date in UTC because the TTL is also 24h — staying in UTC means
    the natural midnight rollover never collides with the previous
    day's still-alive key.
    """
    return _TOKEN_COUNTER_KEY_PREFIX + datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _current_token_count() -> int:
    """Read the current day's token count from Redis. 0 when absent."""
    value = cache.get(_today_key())
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


async def _enforce_daily_token_cap(model: str) -> None:
    """Raise LLMProviderQuotaExceeded when today's counter is at or
    above the configured cap.

    Pre-call gate: refuses BEFORE we would otherwise call the upstream
    API. The L5 router's fallback path catches the exception and
    re-resolves the provider for the next call.

    Async because we're called from inside ``provider.complete``
    (async); the audit + event writes are sync ORM, so we route
    them through ``sync_to_async`` to satisfy Django's no-sync-ORM-
    in-async-context guard.
    """
    cap = int(getattr(settings, "ANTHROPIC_DAILY_TOKEN_CAP", 1_000_000))
    current = _current_token_count()
    if current < cap:
        return

    from asgiref.sync import sync_to_async

    date_key = _today_key().removeprefix(_TOKEN_COUNTER_KEY_PREFIX)
    await sync_to_async(_write_quota_exceeded_telemetry, thread_sensitive=False)(
        model=model,
        date_key=date_key,
        current=current,
        cap=cap,
    )
    raise LLMProviderQuotaExceeded(
        f"anthropic.complete: daily token cap reached "
        f"({current}/{cap} on {date_key}). Router should fall back to OpenAI."
    )


def _write_quota_exceeded_telemetry(
    *,
    model: str,
    date_key: str,
    current: int,
    cap: int,
) -> None:
    """Sync ORM writes for the quota-exceeded path."""
    write_audit(
        EVENT_PROVIDER_QUOTA_EXCEEDED,
        target="AnthropicProvider",
        payload={
            "provider": "anthropic",
            "date": date_key,
            "tokens": current,
            "cap": cap,
            "model": model,
        },
    )
    emit(
        EVENT_PROVIDER_QUOTA_EXCEEDED,
        properties={
            "provider": "anthropic",
            "date": date_key,
            "tokens": current,
            "cap": cap,
        },
    )


def _record_token_usage(tokens: int) -> None:
    """INCR today's counter by `tokens`. Sets TTL on first write of
    the day so the key auto-expires at the natural rollover.

    Django's cache backend doesn't expose atomic INCR on every backend
    (locmem doesn't, redis does). For locmem we fall back to a
    read-modify-write — fine for tests; the prod redis path uses
    `.incr()` atomicity.
    """
    if tokens <= 0:
        return

    key = _today_key()
    incr = getattr(cache, "incr", None)
    if callable(incr):
        try:
            incr(key, tokens)
            # `add` only sets TTL when the key was absent — on a fresh
            # day it primes the 24h timer; on subsequent writes it's
            # a no-op.
            cache.add(key, 0, timeout=_TOKEN_COUNTER_TTL_SECONDS)
            return
        except ValueError:
            # Some backends raise ValueError on INCR of missing keys.
            # Fall through to read-modify-write.
            pass

    # Read-modify-write fallback (locmem in tests). Not atomic across
    # workers — acceptable in single-tenant Sprint 7. Multi-worker
    # prod runs on django-redis which has native INCR above.
    current = _current_token_count()
    cache.set(key, current + tokens, timeout=_TOKEN_COUNTER_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Phase 1 / PI9 (DRF-860) — per-tenant cost-cap glue
# ---------------------------------------------------------------------------


# LLM retro B2: imported from the cost_tracker module so the worst-
# case output-token budget is single-sourced across providers (closes
# reviewer Y3 — pre-fix each provider carried its own 4096 constant
# with a "kept synced" comment, which is exactly the drift hazard).


async def _enforce_tenant_caps(
    *,
    model: str | None = None,
    max_completion_tokens: int | None = None,
):
    """Forward to :func:`apps.llm.cost_tracker.enforce_caps` when a
    tenant is in scope.

    No-op outside a tenant context (matches the OpenAI provider).

    LLM retro B2: when ``model`` is provided, computes a worst-case
    ``estimated_cost_usd`` and forwards it to ``enforce_caps`` so the
    cost-cap check uses the reserve-then-record path. Returns the
    :class:`CostReservation` to thread back into ``record_usage``.
    """
    tenant = current_tenant()
    if tenant is None:
        return None
    from decimal import Decimal

    from apps.llm.cost_tracker import enforce_caps
    from apps.llm.pricing import UnknownModelError, compute_cost

    estimated_cost_usd: Decimal | None = None
    if model:
        from apps.llm.cost_tracker import RESERVATION_DEFAULT_MAX_TOKENS

        try:
            estimated_cost_usd = compute_cost(
                model,
                input_tokens=0,
                output_tokens=max_completion_tokens or RESERVATION_DEFAULT_MAX_TOKENS,
            )
        except UnknownModelError:
            estimated_cost_usd = None

    return await enforce_caps(
        str(tenant.id),
        estimated_cost_usd=estimated_cost_usd,
    )


async def _record_tenant_usage(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    reservation=None,
) -> None:
    """Compute cost and forward to :func:`apps.llm.cost_tracker.record_usage`.

    Mirrors the OpenAI provider's helper — kept duplicated rather than
    refactored into a shared module because the providers are
    intentionally parallel modules (per L2/L4 docstrings) and a shared
    helper would re-introduce the import-cycle the cost_tracker module
    itself was designed to break.

    ``reservation`` is the :class:`CostReservation` returned by the
    matching ``_enforce_tenant_caps`` call (LLM retro B2 reserve-then-
    record reconciliation).
    """
    tenant = current_tenant()
    if tenant is None:
        return
    from decimal import Decimal

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
            "llm.anthropic.cost_unknown_model model=%s — usage recorded with cost=0",
            model,
        )
        cost_usd = Decimal(0)

    await record_usage(
        str(tenant.id),
        tokens=max(0, int(input_tokens or 0)) + max(0, int(output_tokens or 0)),
        cost_usd=cost_usd,
        reservation=reservation,
    )


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
