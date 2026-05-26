"""FAQ skill — KB-driven, two-call tool-use flow (DRF-589 / Sprint 7 / F2).

Replaces the Sprint 6 canned-reply stub with a real grounded-answer
implementation:

1. **Build initial messages** via :func:`apps.skills.faq.prompts.build_faq_prompt`
   with ``retrieved_chunks=None``. The system prompt nudges the LLM
   to invoke ``search_knowledge_base`` for fact-bearing questions.
2. **First LLM call** through the L5 router with the
   :data:`apps.skills.faq.tools.SEARCH_KB_TOOL_SPEC` tool exposed.
   Model either answers directly (no tool call → use that text) OR
   emits a tool call.
3. **Tool execution** — when tool_calls non-empty, invoke
   :func:`apps.skills.faq.tools.search_knowledge_base` and collect
   the retrieved chunks.
4. **Second LLM call** with ``retrieved_chunks=tool_result``. The
   prompt now anchors on the chunks; the model produces the grounded
   answer.
5. **Confidence + handoff** — compute confidence from chunk scores
   (avg of top-3). When ``< 0.5`` OR ``retrieved_chunks`` is empty,
   set :attr:`SkillResult.should_handoff = True`. The Sprint 7 / O2
   pipeline step 10.5 catches this and routes to an admin.

### Why one tool call, not a loop

LLM frameworks support arbitrary tool-call recursion. Sprint 7 caps
at one round — two LLM calls, one tool call between. This is enough
to ground 95% of FAQ questions (single retrieval suffices) and bounds
worst-case latency. Sprint 8 may add a second hop if telemetry shows
multi-step questions are common.

### Brand voice adapter (Decision 15)

F3 ships its own :class:`BrandVoiceConfig` dataclass (decoupled from
ayla's schema). This skill bridges from the per-tenant brand voice
source (Sprint 8's PromptRegistry) to that dataclass. Sprint 7 ships
a hardcoded default voice — the salon "Алина" persona — until the
PromptRegistry lands per-tenant voices.

### Async-from-sync bridge

:class:`apps.skills.base.SkillResult` is returned by a sync ``handle``;
the LLM provider methods are async. We bridge with ``asyncio.run``
per call (two calls per handle ≈ 200ms event-loop spin-up overhead
on a 200-2000ms total turn — negligible).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar

from apps.audit.services import write_audit
from apps.events.services import emit
from apps.events.vocabulary import SKILL_DISPATCHED
from apps.llm.protocol import LLMError
from apps.skills.base import SkillContext, SkillResult
from apps.skills.faq.prompts import BrandVoiceConfig, build_faq_prompt
from apps.skills.faq.tools import (
    SEARCH_KB_TOOL_SPEC,
    KbToolResult,
    search_knowledge_base,
)
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# Sprint 7 hardcoded brand voice. Sprint 8 PromptRegistry lifts this
# into a per-tenant config. The persona+tone are non-controversial —
# "warm admin of the salon" — and forbidden phrases cover the
# generic-marketing-speak we want to avoid.
_DEFAULT_BRAND_VOICE = BrandVoiceConfig(
    persona="Алина, администратор салона «Формула тела»",
    tone="дружелюбный, краткий, без канцелярита",
    forbidden=(
        "гарантирую",
        "100%",
        "лучшие в городе",
        "уникальная технология",
    ),
)


# Decision 5 confidence threshold. Below this → post-skill handoff.
_CONFIDENCE_HANDOFF_THRESHOLD = 0.5

# Top-N chunk scores used for confidence calculation.
_CONFIDENCE_TOP_N = 3


# Fallback texts.
_FALLBACK_HANDOFF_TEXT = "Не уверена в точном ответе — переключаю на менеджера, он уточнит."


# Audit / event slugs.
EVENT_FAQ_ANSWERED = "kb.faq_answered"
EVENT_FAQ_HANDOFF = "kb.faq_handoff"


@register
class FAQSkill:
    """KB-driven FAQ skill (Sprint 7 / F2).

    Replaces the Sprint 6 stub. Wires:

    * F3 prompts (``build_faq_prompt``)
    * F1 tool (``search_knowledge_base``)
    * L5 router (``get_router``)
    * O1 SkillResult fields (``should_handoff``, ``tool_calls_made``,
      ``confidence``)
    * O2 pipeline step 10.5 (consumes ``should_handoff``)
    """

    name: ClassVar[str] = "faq"

    # Sprint 6 stub kept its keyword matcher. Sprint 7 plan calls for
    # gating on ``ctx.intent.label == "faq"`` (set by the orchestrator
    # step-6 intent classifier). The orchestrator runs the intent
    # classifier BEFORE dispatch, so when intent is faq we always
    # match; otherwise we don't.
    def matches(self, context: SkillContext) -> bool:
        intent = context.intent
        if intent is not None:
            return intent.intent == "faq"
        # No intent attached (Sprint 3 callers, tests) — fall back to
        # the keyword heuristic so we don't regress dispatch behaviour.
        return _legacy_keyword_match(context.message_text)

    def handle(self, context: SkillContext) -> SkillResult:
        """Two-call FAQ flow with sync/async separation.

        Sprint 7 design constraint: F1 ``search_knowledge_base`` does
        sync Django ORM writes (audit + event) and its embedding step
        runs ``asyncio.run`` internally. Calling it from inside an
        outer event loop trips Django's "no sync ORM in async context"
        guard. We avoid the conflict by splitting the flow into three
        SEPARATE sync phases, each with its own short-lived event loop
        for the LLM call:

        1. Sync: build first prompt → asyncio.run(provider.complete).
        2. Sync: tool execution (search_knowledge_base writes ORM here).
        3. Sync: build second prompt → asyncio.run(provider.complete).
        4. Sync: audit + return.

        Each ``asyncio.run`` opens + closes a fresh loop. No outer
        coroutine carries through, so Django sees normal sync ORM at
        every write site.
        """
        from apps.llm.router import get_router

        emit(
            SKILL_DISPATCHED,
            distinct_id=str(context.bot_user.id),
            dialog_id=context.conversation.id,
            properties={"skill": self.name},
        )

        tenant = context.conversation.tenant
        tenant_id = str(tenant.id)

        # #473 LLM Y3 envelope expansion: the entire LLM-touching flow
        # (provider lookup at line ↓, two ``provider.complete`` calls,
        # and the embedding-provider lookup + ``provider.embed`` inside
        # ``_execute_search_tool``) is wrapped here so ANY LLMError
        # variant — UnknownTenantError, LLMProviderUnavailable,
        # LLMTransportError, LLMQuotaError, LLMProviderQuotaExceeded —
        # converts to a friendly handoff instead of 500-ing the customer.
        #
        # SCOPE NOTE: envelope intentionally covers ``_audit_faq`` +
        # ``_build_skill_result`` too (they sit at the bottom of the
        # try). Neither raises ``LLMError`` today (one is an ORM write,
        # the other a dataclass build). Non-LLM exceptions (DB outage,
        # serialisation crash) bubble past ``except LLMError`` and
        # surface as 500 — that's pre-existing F2 behaviour, untouched
        # by #473. Tracked as future hardening (outer ``except
        # Exception`` with ``reason='unexpected_error'``) if pilot
        # telemetry shows the gap matters.
        try:
            provider = get_router().get_provider(tenant, skill=self.name, op="complete")
            model = getattr(provider, "default_completion_model", None) or ""

            # Phase 1 — first LLM call (decide on tool use).
            first_messages = build_faq_prompt(
                brand_voice=_DEFAULT_BRAND_VOICE,
                retrieved_chunks=None,
                query=context.message_text,
            )
            first = asyncio.run(
                provider.complete(
                    first_messages,
                    model=model,
                    tools=[SEARCH_KB_TOOL_SPEC],
                )
            )

            if not first.tool_calls:
                # No retrieval needed (small-talk / direct answer).
                return _build_skill_result(
                    text=first.text,
                    tool_calls_made=[],
                    confidence=None,
                )

            first_call = first.tool_calls[0]
            if first_call.name != SEARCH_KB_TOOL_SPEC["name"]:
                return _handoff(
                    tool_calls_made=first.tool_calls,
                    reason="faq_unknown_tool",
                    text=_FALLBACK_HANDOFF_TEXT,
                )

            # Phase 2 — tool execution (sync, writes ORM audit rows).
            # NOTE: _execute_search_tool ALSO does an LLM call internally
            # (embedding for retrieval) — its LLMError is caught here.
            tool_result = _execute_search_tool(tenant, first_call.arguments)

            if not tool_result.hits:
                _audit_faq(
                    tenant_id=tenant_id,
                    hits_count=0,
                    confidence=None,
                    handoff=True,
                )
                return _handoff(
                    tool_calls_made=first.tool_calls,
                    reason="faq_low_confidence",
                    text=_FALLBACK_HANDOFF_TEXT,
                )

            confidence = _compute_confidence(tool_result)
            retrieved_chunks = [
                {
                    "text": hit.text,
                    "score": hit.score,
                    "doc_type": hit.doc_type,
                    "source_uri": hit.source_uri,
                }
                for hit in tool_result.hits
            ]

            # Phase 3 — second LLM call (grounded answer).
            second_messages = build_faq_prompt(
                brand_voice=_DEFAULT_BRAND_VOICE,
                retrieved_chunks=retrieved_chunks,
                query=context.message_text,
            )
            second = asyncio.run(provider.complete(second_messages, model=model))

            # Phase 4 — audit + return.
            handoff = confidence < _CONFIDENCE_HANDOFF_THRESHOLD
            _audit_faq(
                tenant_id=tenant_id,
                hits_count=len(tool_result.hits),
                confidence=confidence,
                handoff=handoff,
            )
            if handoff:
                return _handoff(
                    tool_calls_made=first.tool_calls,
                    reason="faq_low_confidence",
                    text=_FALLBACK_HANDOFF_TEXT,
                    confidence=confidence,
                )
            return _build_skill_result(
                text=second.text,
                tool_calls_made=first.tool_calls,
                confidence=confidence,
            )
        except LLMError as exc:
            logger.warning(
                "faq.llm.failed tenant=%s err_type=%s err=%s",
                tenant_id,
                type(exc).__name__,
                exc,
            )
            return _handoff(
                tool_calls_made=[],
                reason="llm_error",
                text=_FALLBACK_HANDOFF_TEXT,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _execute_search_tool(tenant: Any, arguments: dict[str, Any]) -> KbToolResult:
    """Invoke the F1 tool with the LLM-supplied args.

    Coerces ``k`` and ``doc_types`` to safe types. The tool itself
    enforces the Decision-6 ``k`` clamp.
    """
    from apps.llm.router import get_router

    # F1 needs an LLMProvider for embedding. Use the embedding-op
    # router resolution (handles Anthropic→OpenAI embedding fallback).
    embedding_provider = get_router().get_provider(tenant, skill="faq", op="embedding")

    query = str(arguments.get("query", ""))
    k_value = arguments.get("k", 3)
    try:
        k_int = int(k_value)
    except (TypeError, ValueError):
        k_int = 3
    doc_types = arguments.get("doc_types")
    if doc_types is not None and not isinstance(doc_types, list):
        doc_types = None

    return search_knowledge_base(
        tenant,
        query,
        provider=embedding_provider,
        doc_types=doc_types,
        k=k_int,
    )


def _compute_confidence(result: KbToolResult) -> float:
    """Top-N average of hit scores; 0.0 when no hits."""
    if not result.hits:
        return 0.0
    top = sorted((h.score for h in result.hits), reverse=True)[:_CONFIDENCE_TOP_N]
    return sum(top) / len(top)


def _build_skill_result(
    *,
    text: str,
    tool_calls_made: list[Any],
    confidence: float | None,
) -> SkillResult:
    return SkillResult(
        reply_text=text,
        action_type="faq",
        tool_calls_made=tool_calls_made,
        confidence=confidence,
        meta={"skill": "faq"},
    )


def _handoff(
    *,
    tool_calls_made: list[Any],
    reason: str,
    text: str,
    confidence: float | None = None,
) -> SkillResult:
    return SkillResult(
        reply_text=text,
        action_type="faq",
        should_handoff=True,
        handoff_reason=reason,
        tool_calls_made=tool_calls_made,
        confidence=confidence,
        meta={"skill": "faq"},
    )


def _audit_faq(
    *,
    tenant_id: str,
    hits_count: int,
    confidence: float | None,
    handoff: bool,
) -> None:
    write_audit(
        EVENT_FAQ_HANDOFF if handoff else EVENT_FAQ_ANSWERED,
        target="FAQSkill",
        payload={
            "tenant_id": tenant_id,
            "hits_count": hits_count,
            "confidence": confidence,
            "handoff": handoff,
        },
    )


# ---------------------------------------------------------------------------
# Sprint 6 keyword matcher — kept for backward-compat dispatch
# ---------------------------------------------------------------------------


_QUESTION_SIGNALS: tuple[str, ...] = (
    # Generic question markers
    "?",
    "сколько",
    "когда",
    "где",
    "почему",
    "как ",
    "какой",
    "какая",
    "какие",
    "что такое",
    "что входит",
    "есть ли",
    "можно ли",
    # Imperative info-requests. Cutover smoke 2026-05-19 surfaced
    # «Расскажи про RF-лифтинг» falling through to echo because no
    # interrogative signal matched. These are common Russian phrasings
    # of "give me info about X" and rarely appear in greetings.
    "расскажи",
    "опиши",
    "подскажи",
    "интересует",
    "поведай",
    # English fallbacks
    "what is",
    "how much",
    "when",
    "where",
    "why",
    "tell me",
)


def _legacy_keyword_match(text: str) -> bool:
    """Fallback matcher when ctx.intent is absent (Sprint 3 callers + tests).

    Production turns always have intent from the step-6 classifier;
    this branch keeps direct dispatch via apps.skills.registry.dispatch
    working without retrofitting every test to populate intent.
    """
    lower = text.lower()
    return any(signal in lower for signal in _QUESTION_SIGNALS)
