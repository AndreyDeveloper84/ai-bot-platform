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
      provider: dependency-injected for tests. Defaults to OpenAIProvider().
      model: LLM model override. Default gpt-4o-mini per latency budget.

    Returns:
      :class:`IntentDecision`. Safe fallback on any failure
      (breaker open, JSON parse error, schema validation error).
    """

    provider = provider or OpenAIProvider()

    # #842 — PII tokenization at intent_router boundary (152-ФЗ §6).
    # ``apps.orchestrator.llm.openai_provider.OpenAIProvider`` is a
    # Sprint-1 simpler provider returning ``LLMResponse``, NOT the
    # production ``LLMProvider`` protocol that ``router._load_provider``
    # auto-wraps in ``PIITokenizingProvider``. So the decorator never
    # fires for intent classification — raw user text would otherwise
    # cross the trust boundary к OpenAI verbatim.
    #
    # Tactical fix: tokenize ``text`` directly at this call site. The
    # classifier returns structured JSON intent decisions (no user
    # free-text echo), so detokenization on response is omitted — if
    # the LLM ever leaks a token into the JSON, it would surface as
    # the literal `<CAT_NONCE_INDEX>` string in `IntentDecision.intent`
    # which downstream code treats as «unknown» and falls back safely.
    #
    # Audit row for transit pseudonymisation is NOT emitted at this
    # call site — pipeline-level `record_ai_request` (W4 #816) covers
    # observability. Follow-up: harmonise intent_router к use the
    # production LLMProvider protocol so the decorator pattern applies
    # uniformly.
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
        logger.exception("intent_router.llm_failed text=%s err=%s", text[:80], exc)
        return _SAFE_FALLBACK

    if response.is_fallback:
        # Breaker was open. Pipeline routes via SAFE fallback.
        logger.warning("intent_router.fallback_served reason=breaker_open")
        return _SAFE_FALLBACK

    try:
        raw = json.loads(response.content)
    except json.JSONDecodeError:
        logger.warning("intent_router.malformed_json content=%s", response.content[:200])
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
