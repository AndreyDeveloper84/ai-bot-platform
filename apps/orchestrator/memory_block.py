"""Consent-gated assembly of the ayla-ai-core memory block (W5 task 2).

Renders the GREEN-zone personal-memory block for the concierge system
prompt via :func:`ayla_ai_core.build_memory_block`:

- **Declared prefs** (Ayla personal-context API) arrive through the W3
  gated service :func:`apps.identity.services.personal_context.get_declared_prefs`,
  which enforces the ``memory_green`` consent BEFORE the wire (frozen
  contract v1.0). БЕЗ memory_green the block is "" and NOT A SINGLE FACT
  reaches the prompt — this is the hard pilot requirement (acceptance #7).
- **Inferred green facts** (bot-side ``MemoryEntry``) merge in only when
  THEIR OWN consent basis (PERSONAL_DATA welcome consent, ADR-0011 §11)
  is open — the two surfaces deliberately keep separate bases, see the
  W3 module docstring.

Confidence policy (W5 decision, documented): declared prefs are
user-stated → 1.0 (asserted); inferred facts → 0.6 (softened «кажется» —
Constitution Art. VII probabilistic phrasing for hypotheses). Both honour
the ai-core thresholds (>=0.8 assert, <0.4 clarify).

Fail-closed on every error: memory surfacing must never break the turn.

Concierge Mode rollback (runbook §7, W5): ``CONCIERGE_MEMORY_ENABLED=false``
in env disables this whole surface without a deploy — see
:func:`concierge_memory_enabled`.
"""

from __future__ import annotations

import logging
from typing import Any

from ayla_ai_core import build_memory_block

from apps.identity.services.personal_context import GateStatus, get_declared_prefs

logger = logging.getLogger(__name__)

# Ayla contract slot values (PERSONAL_CONTEXT contract §catalog:
# early_morning/morning/afternoon/evening/late_evening) → ayla-ai-core
# label keys (morning/day/evening/night). Display-only translation —
# values written back to Ayla always use the contract vocabulary.
_SLOT_DISPLAY = {
    "early_morning": "morning",
    "morning": "morning",
    "afternoon": "day",
    "evening": "evening",
    "late_evening": "night",
}

# Bot-side inferred green keys → ai-core memory-block keys. The pilot's
# deterministic extractor writes ``diet``; the block expects ``diet_type``.
_INFERRED_KEY_MAP = {"diet": "diet_type"}

_DECLARED_CONFIDENCE = 1.0
_INFERRED_CONFIDENCE = 0.6


def concierge_memory_enabled() -> bool:
    """Concierge Mode rollback switch (runbook §7, W5).

    Default ON; ``CONCIERGE_MEMORY_ENABLED=false`` in env disables the
    whole concierge memory surface (prompt block + memory-ask) without a
    deploy. Read via Django settings so tests flip it with the
    ``settings`` fixture.
    """
    from django.conf import settings

    return bool(getattr(settings, "CONCIERGE_MEMORY_ENABLED", True))


def build_concierge_memory_block(bot_user: Any) -> str:
    """Return the system-prompt memory block, or "" when nothing may surface.

    "" covers every gated/failure case: consent closed, user unlinked,
    upstream error, or simply no facts — the caller injects nothing and
    the happy-path prompt is byte-identical to the no-memory one.
    """
    if not concierge_memory_enabled():
        return ""
    declared = get_declared_prefs(bot_user)
    if declared.status is not GateStatus.OK or declared.context is None:
        return ""

    facts: dict[str, Any] = {}
    confidences: dict[str, float] = {}
    for key, value in (declared.context.context or {}).items():
        if key == "preferred_time_slots" and isinstance(value, list):
            value = [_SLOT_DISPLAY.get(s, s) for s in value]
        facts[key] = value
        confidences[key] = _DECLARED_CONFIDENCE

    _merge_inferred(bot_user, facts, confidences)

    try:
        return build_memory_block(facts, confidences=confidences)
    except Exception:  # noqa: BLE001 — surfacing must never break the turn
        logger.exception("orchestrator.memory_block.build_failed")
        return ""


def _merge_inferred(bot_user: Any, facts: dict, confidences: dict) -> None:
    """Merge bot-side inferred green MemoryEntry facts (own consent basis).

    Declared values win on key conflicts (user-stated beats inferred).
    Any failure degrades to declared-only — never raises.
    """
    try:
        from apps.consent.memory import can_store_green_memory

        if not can_store_green_memory(bot_user):
            return
        ayla_user_id = getattr(bot_user, "ayla_user_id", None)
        if not ayla_user_id:
            return
        from apps.identity.services.memory_reader import read_personal_context

        view = read_personal_context(ayla_user_id)
    except Exception:  # noqa: BLE001
        logger.exception("orchestrator.memory_block.inferred_failed")
        return

    for fact in view.green_facts:
        content = fact.content if isinstance(fact.content, dict) else {}
        raw_key = content.get("key")
        key = _INFERRED_KEY_MAP.get(raw_key, raw_key)
        value = content.get("value")
        if isinstance(key, str) and key not in facts and value not in (None, "", []):
            facts[key] = value
            confidences[key] = _INFERRED_CONFIDENCE
