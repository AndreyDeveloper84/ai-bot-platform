"""Intent router (DRF-536 / Sprint 6 / O2).

Step 6 of the orchestrator pipeline. Takes the user's text + memory
snapshot + brand voice config, asks gpt-4o-mini to emit a structured
:class:`IntentDecision`, returns it. The dispatcher (O1 pipeline.turn)
uses the decision to pick the right skill.

### Why gpt-4o-mini with structured JSON

- Latency budget per PHASE0_DESIGN §5.2: 1500ms p95 per turn. gpt-4o-mini
  comfortably fits with a 200-token cap.
- Structured ``response_format={"type": "json_object"}`` forces the model
  to emit valid JSON; we still validate shape on the Python side.
- Sprint 7 may swap providers (Anthropic) — the function returns an
  :class:`IntentDecision` dataclass, signature stable across providers.

### Breaker integration

OpenAIProvider (Sprint 1 / D1) wraps every call in the circuit breaker.
When the breaker is open we get a fallback LLMResponse — the router
detects it and returns a safe ``IntentDecision`` (intent='unknown',
risk_level='low', confidence=0) so the pipeline can route to a generic
clarify reply instead of crashing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from apps.orchestrator.llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntentDecision:
    """Output of :func:`classify`. Consumed by O1 pipeline + skills.

    Fields per PHASE0_DESIGN §5.1 step 6.
    """

    intent: str  # short slug — 'faq' | 'booking' | 'handoff' | 'privacy' | 'small_talk' | 'unknown'
    skill: str  # registry-resolved skill name (lowercase identifier)
    confidence: float  # [0.0, 1.0]
    risk_level: str  # 'low' | 'medium' | 'high'
    missing_slots: list[str] = field(default_factory=list)
    reply_mode: str = "text"  # 'text' | 'keyboard' | 'silent'
    needs_rag: bool = False
    needs_tool: bool = False
    raw: dict[str, Any] = field(default_factory=dict)  # raw LLM payload for forensic


# Pinned schema description — LLM sees this in the system prompt.
_SCHEMA_PROMPT = """\
You are an intent classifier for a salon booking assistant. Return ONE
JSON object with EXACTLY these keys:

  intent (string): one of "faq" | "booking" | "handoff" | "privacy" | "small_talk" | "unknown"
  skill (string): lowercase skill slug — same value as intent for Phase 0
  confidence (number): 0.0..1.0
  risk_level (string): "low" | "medium" | "high"
  missing_slots (array of strings): empty if no booking flow active
  reply_mode (string): "text" | "keyboard" | "silent"
  needs_rag (boolean): true if FAQ-style answer requires knowledge base lookup
  needs_tool (boolean): true if the skill will need to invoke an external tool

Return JSON only — no markdown, no preamble. Be decisive: use
"unknown" + confidence=0.5 only when the message is genuinely ambiguous.
"""

_VALID_INTENTS = {"faq", "booking", "handoff", "privacy", "small_talk", "unknown"}
_VALID_RISK = {"low", "medium", "high"}
_VALID_REPLY_MODE = {"text", "keyboard", "silent"}


_SAFE_FALLBACK = IntentDecision(
    intent="unknown",
    skill="unknown",
    confidence=0.0,
    risk_level="low",
    reply_mode="text",
)


async def classify(
    text: str,
    *,
    memory_snapshot: dict[str, Any] | None = None,
    brand_voice: dict[str, Any] | None = None,
    provider: OpenAIProvider | None = None,
    tenant: Any = None,
    model: str = "gpt-4o-mini",
) -> IntentDecision:
    """Classify user text → :class:`IntentDecision`.

    Args:
      text: user's inbound message body.
      memory_snapshot: optional dict from :func:`apps.orchestrator.memory.coordinator.load_snapshot`.
        We inject `long_term.rfm_segment`, `long_term.lifecycle_stage`,
        recent message count into the system prompt so the classifier
        can use customer context.
      brand_voice: optional dict from BrandVoiceConfig (Sprint 4 / F0.8).
        We pass `forbidden_phrases` summary as a hint.
      provider: dependency-injected Sprint-1 ``OpenAIProvider``. When
        supplied, the legacy path is used (tactical PII tokenize +
        no audit row). Tests use this for in-process mocking.
      tenant: when supplied AND ``provider`` is None, the **production**
        ``LLMProvider`` protocol is resolved через
        ``apps.llm.router.get_router().get_provider(tenant, skill=
        "intent", op="complete")``. That provider is auto-wrapped в
        :class:`apps.llm.pii_protected_provider.PIITokenizingProvider`
        which (a) tokenizes user PII at the LLM-call boundary and
        (b) emits an ``llm.call_completed`` audit row (152-ФЗ §6
        transit pseudonymisation evidence per ADR-0011 §11.4).
        Closes the audit-trail gap для intent classification (#975).
      model: LLM model override. Default gpt-4o-mini per latency budget.

    Returns:
      :class:`IntentDecision`. Safe fallback on any failure
      (breaker open, JSON parse error, schema validation error).

    ### Two code paths (#975 production migration, 2026-06-02)

    1. **Production path** — when ``tenant is not None AND provider is None``.
       Goes through the LLM router, which auto-wraps the resolved
       concrete provider в ``PIITokenizingProvider``. The decorator
       handles tokenize-out / detokenize-in / audit-emit uniformly,
       same enforcement surface as other skills.

       The pipeline (``apps.orchestrator.pipeline._run_under_tenant``)
       supplies ``tenant`` so every customer-facing classify call
       lands here.

    2. **Legacy path** — when ``provider is not None`` OR ``tenant is None``.
       Uses Sprint-1 ``apps.orchestrator.llm.openai_provider.OpenAIProvider``
       directly. This path retains the **tactical** PII tokenize hack
       from #842 (decorator not in play). Audit row NOT emitted. Kept
       for backwards compatibility с (a) unit tests that pin a mock
       provider for deterministic intent JSON и (b) any future internal
       caller that hasn't been ported. Production migration ensures the
       pipeline always uses the production path.
    """
    if provider is not None or tenant is None:
        return await _classify_legacy_path(
            text,
            memory_snapshot=memory_snapshot,
            brand_voice=brand_voice,
            provider=provider,
            model=model,
        )

    return await _classify_production_path(
        text,
        tenant=tenant,
        memory_snapshot=memory_snapshot,
        brand_voice=brand_voice,
        model=model,
    )


async def _classify_production_path(
    text: str,
    *,
    tenant: Any,
    memory_snapshot: dict[str, Any] | None,
    brand_voice: dict[str, Any] | None,
    model: str,
) -> IntentDecision:
    """#975 production path — resolve provider through router, let the
    ``PIITokenizingProvider`` decorator handle tokenize + audit emit.

    The production ``LLMProvider`` protocol returns
    :class:`apps.llm.protocol.CompletionResult` (vs. Sprint-1's
    ``LLMResponse``). Field names match closely; we read ``text``
    instead of ``content`` and ``is_fallback`` is replaced by
    ``finish_reason`` semantics — the production path never reaches
    here on circuit-breaker-open (router раises ``BreakerOpenError``
    which we catch as a general exception below and return the safe
    fallback).
    """
    # Production protocol's `complete()` lacks the `response_format=
    # {"type": "json_object"}` kwarg the Sprint-1 path used. The
    # production OpenAI provider в `apps.llm.providers.openai_provider`
    # accepts arbitrary kwargs but doesn't pass response_format
    # downstream. JSON shape is enforced by the prompt's «only valid
    # JSON, no commentary» instruction — same contract as `_build_
    # messages`. If a future provider returns non-JSON, the catch
    # below routes к the SAFE fallback.
    from asgiref.sync import sync_to_async

    from apps.llm.protocol import LLMError
    from apps.llm.router import get_router

    # `get_provider` emits a sync `write_audit` row (router-level
    # «llm.provider_resolved»). When called from async context that
    # raises `SynchronousOnlyOperation`. Skills run sync in thread
    # via `sync_to_async(_dispatch_skill)` so they don't hit this.
    # Intent router IS async — wrap explicitly. `thread_sensitive=False`
    # because the router lookup + audit emit doesn't share state с
    # the surrounding async caller.
    def _resolve() -> Any:
        return get_router().get_provider(tenant, skill="intent", op="complete")

    try:
        provider = await sync_to_async(_resolve, thread_sensitive=False)()
    except Exception as exc:  # noqa: BLE001 — router lookup failures must not crash pipeline
        logger.warning(
            "intent_router.production_provider_lookup_failed text_len=%d err=%s",
            len(text or ""),
            exc,
        )
        return _SAFE_FALLBACK

    messages = _build_messages(text, memory_snapshot or {}, brand_voice or {})

    try:
        result = await provider.complete(
            messages,
            model=model,
            temperature=0.1,
            max_tokens=200,
        )
    except LLMError as exc:
        logger.warning(
            "intent_router.production_llm_error text_len=%d err_type=%s",
            len(text or ""),
            type(exc).__name__,
        )
        return _SAFE_FALLBACK
    except Exception as exc:  # noqa: BLE001 — safety boundary
        logger.exception(
            "intent_router.production_unhandled text_len=%d err=%s",
            len(text or ""),
            exc,
        )
        return _SAFE_FALLBACK

    try:
        raw = json.loads(result.text)
    except json.JSONDecodeError:
        # #842 W3 CRIT-2 — log shape only; LLM response may contain
        # echoed tokens but they're already pseudonymized via the
        # decorator. Length proxy is enough for ops triage.
        logger.warning(
            "intent_router.production_malformed_json text_len=%d response_len=%d",
            len(text or ""),
            len(result.text or ""),
        )
        return _SAFE_FALLBACK

    return _validate_and_build(raw)


async def _classify_legacy_path(
    text: str,
    *,
    memory_snapshot: dict[str, Any] | None,
    brand_voice: dict[str, Any] | None,
    provider: OpenAIProvider | None,
    model: str,
) -> IntentDecision:
    """Pre-#975 path — Sprint-1 OpenAIProvider + tactical PII tokenize.

    Retained for backwards compatibility. The production path is
    preferred when caller supplies ``tenant`` (the pipeline always
    does). When ``tenant`` is None (legacy callers / unit tests) OR
    ``provider`` is dependency-injected (mocked), this path runs.
    """
    provider = provider or OpenAIProvider()

    # #842 — PII tokenization at intent_router boundary (152-ФЗ §6).
    # This path doesn't go через the decorator (Sprint-1 provider
    # shape), so we tokenize here directly.
    from django.conf import settings as _settings

    from apps.llm.pii_tokenizer import current_conversation_id as _pii_cid
    from apps.llm.pii_tokenizer import tokenize as _pii_tokenize

    classifier_text = text
    if getattr(_settings, "PII_TOKENIZER_ENABLED", True):
        _conv_id = _pii_cid()
        if _conv_id is not None:
            classifier_text = _pii_tokenize(text, _conv_id)

    messages = _build_messages(classifier_text, memory_snapshot or {}, brand_voice or {})

    try:
        response = await provider.complete(
            messages,
            model=model,
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0.1,
        )
    except Exception as exc:  # noqa: BLE001 — router is safety boundary
        # #842 W3 CRIT-2 — length-only proxy; `text` is RAW user input.
        logger.exception("intent_router.llm_failed text_len=%d err=%s", len(text or ""), exc)
        return _SAFE_FALLBACK

    if response.is_fallback:
        # Breaker was open. Pipeline routes via SAFE fallback.
        logger.warning("intent_router.fallback_served reason=breaker_open")
        return _SAFE_FALLBACK

    try:
        raw = json.loads(response.content)
    except json.JSONDecodeError:
        logger.warning("intent_router.malformed_json response_len=%d", len(response.content or ""))
        return _SAFE_FALLBACK

    return _validate_and_build(raw)


def _build_messages(
    text: str,
    memory: dict[str, Any],
    brand_voice: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compose the chat-completion messages list (system + user)."""

    long_term = memory.get("long_term", {}) or {}
    history = memory.get("history", []) or []

    context_hints = []
    if long_term.get("rfm_segment"):
        context_hints.append(f"customer segment: {long_term['rfm_segment']}")
    if long_term.get("lifecycle_stage"):
        context_hints.append(f"lifecycle: {long_term['lifecycle_stage']}")
    if long_term.get("loyalty_tier"):
        context_hints.append(f"tier: {long_term['loyalty_tier']}")
    if history:
        context_hints.append(f"recent turns: {len(history)}")

    forbidden = brand_voice.get("forbidden_phrases") or []
    if forbidden:
        context_hints.append(f"avoid topics: {len(forbidden)} forbidden patterns active")

    system_lines = [_SCHEMA_PROMPT.strip()]
    if context_hints:
        system_lines.append("Context: " + "; ".join(context_hints))

    return [
        {"role": "system", "content": "\n\n".join(system_lines)},
        {"role": "user", "content": text},
    ]


def _validate_and_build(raw: dict[str, Any]) -> IntentDecision:
    """Coerce raw LLM dict into :class:`IntentDecision`. Soft on bad fields."""

    intent = str(raw.get("intent") or "unknown").lower()
    if intent not in _VALID_INTENTS:
        intent = "unknown"

    skill = str(raw.get("skill") or intent).lower()

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    risk_level = str(raw.get("risk_level") or "low").lower()
    if risk_level not in _VALID_RISK:
        risk_level = "low"

    reply_mode = str(raw.get("reply_mode") or "text").lower()
    if reply_mode not in _VALID_REPLY_MODE:
        reply_mode = "text"

    missing_slots = raw.get("missing_slots") or []
    if not isinstance(missing_slots, list):
        missing_slots = []
    missing_slots = [str(s) for s in missing_slots]

    return IntentDecision(
        intent=intent,
        skill=skill,
        confidence=confidence,
        risk_level=risk_level,
        missing_slots=missing_slots,
        reply_mode=reply_mode,
        needs_rag=bool(raw.get("needs_rag")),
        needs_tool=bool(raw.get("needs_tool")),
        raw=raw,
    )
