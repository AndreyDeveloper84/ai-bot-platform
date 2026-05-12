"""Memory snapshot coordinator (DRF-540 / Sprint 6 / O6).

Glues short-term Redis memory (Sprint 2 / C1) + long-term ClientProfile
context (Sprint 6 / P1) into a single :class:`MemorySnapshot` that the
intent router (O2), skill dispatcher, and composer consume.

### Why a separate coordinator instead of inlining recall()

Three layers feed memory:
1. **Short-term** — last N messages of the current Conversation
   (Redis LIST), already shipped in `apps.orchestrator.memory.short_term`.
2. **Long-term** — durable bot_user-level state (ClientProfile rfm_segment,
   loyalty_tier, lifecycle_stage, last visit) that survives Redis TTL.
3. **Per-turn slot state** — `Conversation.slot_state` JSONField for
   in-progress flows (booking wizard, food logging).

The orchestrator pipeline (`turn()`, O1) wants one call with one return
shape. Inlining recall() into the pipeline would force every consumer
to re-implement the long-term merge. A coordinator gives one stable
contract that the intent router + skills depend on.

### History cap

Short-term `recall()` already caps to `SHORT_TERM_MEMORY_DEPTH`. The
coordinator re-caps to `MEMORY_HISTORY_LIMIT` (default 20, same as
short-term depth) so a settings tweak that loosens short-term doesn't
silently flood the LLM prompt.

### Latency budget

Per PHASE0_DESIGN §5.2: <100ms p95. Short-term recall is a single
Redis LRANGE + JSON decode loop; ClientProfile is a single indexed
query. Both well under budget on warm cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.orchestrator.memory import short_term


@dataclass(frozen=True)
class MemorySnapshot:
    """Single immutable view of all memory layers for one turn.

    Fields:
      history: short-term messages (oldest → newest) capped to limit.
      long_term: derived state from ClientProfile (rfm_segment,
                 loyalty_tier, lifecycle_stage, recency_days, ...).
                 Empty dict when profile doesn't exist yet (pre-recompute).
      slot_state: per-conversation in-progress state for multi-turn flows.
    """

    history: list[dict[str, Any]] = field(default_factory=list)
    long_term: dict[str, Any] = field(default_factory=dict)
    slot_state: dict[str, Any] = field(default_factory=dict)


def _history_limit() -> int:
    return int(getattr(settings, "MEMORY_HISTORY_LIMIT", 20))


def load_snapshot(conversation) -> MemorySnapshot:
    """Build a unified :class:`MemorySnapshot` for one turn.

    Args:
      conversation: a `Conversation` instance (passed in by the
        pipeline; we read its `id`, `slot_state`, and `bot_user`).

    Returns:
      :class:`MemorySnapshot`. Recall failures (Redis down, malformed
      payloads) degrade to empty history — never raise. The pipeline
      treats empty memory as "first turn" which is a safe baseline.
    """

    # Short-term — already caps to SHORT_TERM_MEMORY_DEPTH; re-cap to
    # MEMORY_HISTORY_LIMIT in case operator loosened short-term and
    # didn't want the side effect on LLM prompt size.
    try:
        history = short_term.recall(conversation.id, n=_history_limit())
    except Exception:  # noqa: BLE001 — coordinator is the safety boundary
        history = []

    # Long-term — read ClientProfile via the bot_user relation.
    # Profile may not exist on the very first turn before P1 signal
    # has run; treat absence as empty dict.
    long_term: dict[str, Any] = {}
    try:
        profile = conversation.bot_user.client_profile
        long_term = {
            "rfm_segment": profile.rfm_segment or "",
            "lifecycle_stage": profile.lifecycle_stage or "",
            "loyalty_tier": profile.loyalty_tier or "",
            "recency_days": profile.recency_days,
            "frequency_visits": profile.frequency_visits,
            "churn_risk": profile.churn_risk,
            "favorite_service_id": profile.favorite_service_id or "",
        }
    except Exception:  # noqa: BLE001 — profile-attr access or DB unreachable
        long_term = {}

    slot_state: dict[str, Any] = {}
    try:
        slot_state = dict(conversation.slot_state or {})
    except Exception:  # noqa: BLE001
        slot_state = {}

    return MemorySnapshot(history=history, long_term=long_term, slot_state=slot_state)
