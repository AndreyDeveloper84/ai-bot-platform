"""LLMProvider Protocol + provider-agnostic DTOs (DRF-580 / Sprint 7 / L1).

The contract every concrete LLM provider implements. Two operations:

* ``complete(messages, *, model, ...)`` — chat completion. Optional
  ``tools=[...]`` enables function-calling; the provider parses the
  vendor-specific tool-use shape back into provider-agnostic
  :class:`ToolCall` objects.
* ``embedding(text, *, model)`` — produce a vector. OpenAI has it;
  Anthropic does NOT, and raises :class:`NotImplementedError`. The
  L5 router (DRF-587) catches that and falls back to OpenAI for
  embedding ops regardless of which provider would otherwise be chosen.

### Why a Protocol, not an ABC

Same reason :class:`apps.skills.base.Skill` is a Protocol — providers
live in different modules (and Sprint 7 also wraps the existing
``OpenAIProvider`` without forcing it to inherit). Duck-typing through
:mod:`typing.Protocol` keeps imports flat and avoids a Sprint 1 → 7
inheritance retrofit.

### Exception hierarchy

* :class:`LLMError` — base. Catch this for "something LLM-side went wrong".
* :class:`LLMTransportError` — network / 5xx / timeout. Retryable.
* :class:`LLMQuotaError` — vendor rate-limit / 429. Often retry-after.
* :class:`LLMProviderQuotaExceeded` — OUR daily budget cap hit
  (Sprint 7 / L7 wraps Anthropic with a Redis counter). Router catches
  and falls back to the next-tier provider.
* :class:`LLMVendorCreditsExhausted` — subclass of the above: the
  VENDOR's balance is drained ("you have no credits remaining").
  Terminal, never retryable, triggers the same fallback.

### Tool spec

The :class:`LLMProvider.complete` ``tools`` parameter expects the
OpenAI-shaped spec (canonical because it's the lowest common denominator):

    {
        "name": "search_knowledge_base",
        "description": "Search the tenant KB for matching chunks.",
        "parameters": {  # JSON Schema
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    }

Anthropic provider converts to its native ``input_schema`` shape under
the hood. Both providers return :class:`ToolCall.arguments` as a parsed
``dict[str, Any]`` (NOT the raw JSON string), so call sites never have
to think about which provider answered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    """A parsed function-call request emitted by the LLM.

    Fields:
      id: vendor-supplied identifier. Used as ``tool_call_id`` when
          sending the tool result back in the next ``complete()`` turn.
      name: tool name (matches the registered tool spec ``"name"``).
      arguments: parsed JSON arguments. Always a ``dict[str, Any]`` —
                 providers parse the raw JSON string for us so call
                 sites never have to.
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompletionResult:
    """Return value of :meth:`LLMProvider.complete`.

    Fields:
      text: assistant message text. Empty when the model emitted only
            tool_calls (no natural-language reply).
      tool_calls: parsed function-call list. Empty when the model
                  produced a plain text reply. Order preserved when
                  the model emits multiple in one turn.
      prompt_tokens / completion_tokens: usage counters. Both 0 when
                                         the vendor doesn't expose them.
      model: actual model id used (provider may resolve aliases).
      provider: ``"openai"`` | ``"anthropic"`` — for cost attribution
                + routing telemetry.
      finish_reason: vendor finish code, normalised lowercase string.
                     Common values: ``"stop"``, ``"length"``,
                     ``"tool_calls"``, ``"content_filter"``.
    """

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    provider: str = ""
    finish_reason: str = ""


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """Base class for any LLM-side failure."""


class LLMTransportError(LLMError):
    """Network failure, timeout, or vendor 5xx. Generally retryable."""


class LLMQuotaError(LLMError):
    """Vendor rate-limit (HTTP 429) or vendor-side quota exhaustion.

    Distinct from :class:`LLMProviderQuotaExceeded` — that one is OUR
    daily cap, not the vendor's.
    """


class LLMProviderQuotaExceeded(LLMError):
    """Our internal daily-token cap hit (Sprint 7 / L7 / DRF-585).

    Raised BEFORE making the upstream call once the counter crosses the
    configured budget. The L5 router (DRF-587) catches this and falls
    back to the next-tier provider one hop.
    """


class LLMVendorCreditsExhausted(LLMProviderQuotaExceeded):
    """The VENDOR refused the call because our account has no credits /
    quota left (DRF-1437).

    Distinct from its parent in one dimension only — whose budget ran
    out. The parent is OUR Redis-counter cap; this one is the vendor
    saying "you have no credits remaining". Both are terminal for the
    provider that raised them and both must trigger the router's
    quota fallback, which is exactly why this subclasses the parent
    rather than sitting beside it: every existing ``except
    LLMProviderQuotaExceeded`` handler picks it up unchanged.

    Why it is NOT :class:`LLMQuotaError`: that class means "vendor
    rate-limit, slow down and retry". A drained balance does not heal
    with time, so retrying it burns the retry budget and delays the
    turn by the full backoff before failing anyway. The pilot incident
    of 2026-08-31 (98 consecutive refusals, zero provider hops) is the
    reference case — see ``apps.llm.retry.is_vendor_quota_exhausted``.

    Shape of the upstream error this maps from (both vendors 429 or
    400 with a billing discriminator in the body):

      * OpenAI — ``type="insufficient_quota"``,
        ``code="insufficient_quota"`` / ``"billing_hard_limit_reached"``
      * Anthropic — ``code="credit_balance_exhausted"``, message
        "Your credit balance is too low…" / "You have no credits
        remaining"
    """


class LLMProviderUnavailable(LLMError):
    """A provider could not be constructed (missing API key, SDK init
    failure, malformed settings).

    LLM retro B1: prior to this exception, ``LLMRouter._load_provider``
    surfaced raw ``Exception`` from the provider constructor. Callers
    (notably ``apps/skills/booking/skill.py``) had to wrap the lookup
    in bare ``try/except Exception`` to avoid a 500 — which obscured
    the actual root cause (config) from observability. Catching this
    typed error keeps the customer-facing fallback path while letting
    Sentry pinpoint the misconfigured provider.
    """


class UnknownTenantError(LLMError):
    """A ``tenant_id`` reached ``apps.llm.cost_tracker`` that does not
    correspond to any row in :class:`apps.tenancy.models.Tenant`.

    LLM retro Y3 (PR #473): before this exception, ``_read_tenant_caps``
    / ``_read_tenant_alert_context`` soft-failed to «generous defaults»
    (1M tokens, $50/day) with an ERROR log. The typo'd / stale tenant
    id then ran against a fictitious budget — bounded by the daily cap
    but invisible to the caller. The Phase 0 bridge (PR #409 squash
    ``4961b8e``) accepted this on the basis that raising inside
    ``provider.complete()`` would 500 customers because skill envelopes
    only wrapped router lookup, not completion.

    Phase 1 (#473) replaced the soft-fail with this typed raise. The
    skill envelope in ``apps/skills/{booking,faq}/skill.py`` now catches
    :class:`LLMError` (covers this + ``LLMProviderUnavailable`` + the
    rest) and produces a friendly handoff with reason ``llm_error``.
    """


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """The two-method contract every provider implements.

    Implementations:

    * Sprint 7 / L2 (DRF-581): ``apps.llm.providers.openai_provider.OpenAIProvider``
    * Sprint 7 / L4 (DRF-583): ``apps.llm.providers.anthropic_provider.AnthropicProvider``

    Concrete classes are picked at call-time by
    ``apps.llm.router.LLMRouter`` (L5 / DRF-587) based on per-tenant
    feature flags, per-skill defaults, and the org-wide default.
    """

    name: str  # ``"openai"`` | ``"anthropic"`` — stable identifier

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        temperature: float = 0.0,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        tool_choice: str | None = None,
    ) -> CompletionResult:
        """Chat completion. Pass ``tools`` for function-calling.

        ``tool_choice`` (DRF-1286) takes the canonical OpenAI strings —
        ``"auto"`` / ``"required"`` / ``"none"`` — because the OpenAI spec
        is this Protocol's canonical shape (see module docstring). Ignored
        when ``tools`` is empty. Providers translate: OpenAI passes the
        string through, Anthropic maps it onto its object form
        (``{"type": "auto" | "any" | "none"}``) — ``"required"`` →
        ``{"type": "any"}``, i.e. «the model MUST emit a tool call».
        """
        ...

    async def embedding(self, text: str, *, model: str) -> list[float]:
        """Embed ``text``. Anthropic raises :class:`NotImplementedError`."""
        ...
