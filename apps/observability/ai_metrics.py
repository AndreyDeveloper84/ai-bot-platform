"""AI request metric recorder — sanctioned write path for `AIRequestMetric`.

Per AI observability epic #769 / Веха 1 / task #770.

# What this module provides

`record_ai_request(...)` — single function the AI request hot path
calls once per inbound user message after the orchestrator + skill +
LLM finish (success / fallback / error / escalation). One row per
call. Synchronous INSERT — target <5ms overhead on the parent request
which already takes 1-3s end-to-end.

# Caller pattern (W2 wires call sites in parallel PR)

```python
from apps.observability.ai_metrics import record_ai_request

# In apps/orchestrator/pipeline.py / apps/skills/<each>/skill.py:
record_ai_request(
    tenant=tenant,
    bot_user=bot_user,
    conversation=conversation,
    request_id=trace_id,
    message_text_length=len(user_message_text),
    intent_classified=intent_label or "",
    intent_confidence=confidence_or_none,
    skill_selected=skill_name or "",
    fallback_triggered=did_fallback,
    latency_total_ms=elapsed_ms,
    latency_llm_ms=llm_ms,
    latency_skill_ms=skill_ms,
    llm_provider="openai",
    llm_tokens_input=usage.input_tokens,
    llm_tokens_output=usage.output_tokens,
    llm_cost_usd=Decimal("0.001234"),
    outcome="success",
)
```

# Caller contract

- Tenant MUST be in scope when called — uses `.all_tenants.create(...)`
  internally but the recorder's contract is «scoped from caller side»
  to surface accidental cross-tenant emissions to ops attention.
- `record_ai_request` swallows no exceptions — caller chooses retry /
  log strategy. Sync DB failures bubble up as `IntegrityError` /
  `OperationalError`.
- Veha 1 deliberately keeps this simple. If observability emission
  becomes a hot-path concern (P99 spike), Веха 2+ may move to an
  async outbox pattern.

# What this module does NOT do

- Aggregation / thresholds → Веха 2 (#771)
- Dashboard rendering → Веха 3 (#772)
- Booking-event correlation → Веха 2 aggregation job (fills
  `booking_event_id` + `success_correlated_at` retroactively)
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional
from uuid import UUID

from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.observability.models import AIRequestMetric
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


def record_ai_request(
    *,
    tenant: Tenant,
    bot_user: Optional[BotUser],
    conversation: Optional[Conversation],
    request_id: UUID,
    message_text_length: int,
    latency_total_ms: int,
    outcome: str,
    intent_classified: str = "",
    intent_confidence: Optional[float] = None,
    skill_selected: str = "",
    fallback_triggered: bool = False,
    latency_llm_ms: Optional[int] = None,
    latency_skill_ms: Optional[int] = None,
    llm_provider: str = "",
    llm_tokens_input: Optional[int] = None,
    llm_tokens_output: Optional[int] = None,
    llm_cost_usd: Optional[Decimal] = None,
) -> AIRequestMetric:
    """Record one `AIRequestMetric` row for the just-completed AI request.

    All kwargs are keyword-only — caller must name every argument so the
    instrumentation reads self-documenting at call sites.

    Args:
        tenant: Owning tenant (required). PROTECT FK in the model.
        bot_user: The user whose message triggered the request. NULL allowed
            for system-triggered AI calls (proactive nudges).
        conversation: Owning conversation. NULL allowed for ad-hoc / out-of-band.
        request_id: Trace correlation UUID — match `trace_id_scope()` value.
        message_text_length: Inbound text length in chars (not the body).
        latency_total_ms: End-to-end wall-clock duration in ms.
        outcome: One of `AIRequestMetric.OUTCOME_*` — `success` / `error` /
            `fallback` / `escalated`.
        intent_classified: Intent label produced by the classifier. Empty
            when no classifier ran (early fallback / error before dispatch).
        intent_confidence: 0..1 classifier confidence. NULL when no classifier.
        skill_selected: Skill registry name (e.g. `"booking"`). Empty on early fallback.
        fallback_triggered: True when dispatcher chose clarification over execution.
        latency_llm_ms: Subset of total — LLM call duration. NULL when no LLM.
        latency_skill_ms: Subset of total — skill execute() duration. NULL on early exit.
        llm_provider: Provider slug — `"openai"` / `"anthropic"` / `"yandex"`. Empty when no LLM.
        llm_tokens_input: Input tokens billed. NULL when no LLM.
        llm_tokens_output: Output tokens billed. NULL when no LLM.
        llm_cost_usd: USD cost for the LLM call (6 dp precision). NULL when no LLM.

    Returns:
        The persisted `AIRequestMetric` row.

    Raises:
        ValueError: `outcome` not in `OUTCOME_CHOICES`.
        django.db.IntegrityError: schema-level conflict (FK violation, etc.)
            — bubbles up to caller.
    """
    valid_outcomes = {choice[0] for choice in AIRequestMetric.OUTCOME_CHOICES}
    if outcome not in valid_outcomes:
        raise ValueError(
            f"record_ai_request: outcome={outcome!r} not in "
            f"AIRequestMetric.OUTCOME_CHOICES ({sorted(valid_outcomes)})."
        )

    return AIRequestMetric.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        conversation=conversation,
        request_id=request_id,
        message_text_length=message_text_length,
        intent_classified=intent_classified,
        intent_confidence=intent_confidence,
        skill_selected=skill_selected,
        fallback_triggered=fallback_triggered,
        latency_total_ms=latency_total_ms,
        latency_llm_ms=latency_llm_ms,
        latency_skill_ms=latency_skill_ms,
        llm_provider=llm_provider,
        llm_tokens_input=llm_tokens_input,
        llm_tokens_output=llm_tokens_output,
        llm_cost_usd=llm_cost_usd,
        outcome=outcome,
    )
