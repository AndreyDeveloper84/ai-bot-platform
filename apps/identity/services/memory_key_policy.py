"""Key-aware conflict policy for green-memory READ paths (read-side supersession).

# Why this exists

The write paths deliberately keep history: a changed fact (vegan → keto)
lands as a NEW live row and the old row stays live until the targeted
supersession lifecycle ships. Without a read-side rule both consumers
leaked contradictions into the prompt:

  - ``orchestrator.memory_block`` took the FIRST occurrence of a key —
    i.e. the STALEST value (rows arrive ``created_at`` ASC);
  - ``persona.memory_surface`` rendered ALL live rows — mutually
    exclusive facts (vegan + keto) reached the model together.

This module is the deterministic fix: a key→cardinality registry plus a
resolver that collapses live rows to the current fact set BEFORE
surfacing. No schema change, no write-path change — old rows stay live,
readers just stop surfacing superseded ones.

# Policy

  - **single** (default — conservative, unknown keys never contradict):
    exactly ONE value per key reaches the prompt. Winner selection:
    1. source priority — an explicit (user-stated / confirmed) row is
       NEVER displaced by a fresher inferred/signal row;
    2. then freshest ``created_at``;
    3. then the larger entry id — a stable, arbitrary tiebreak for
       legacy rows with identical ``created_at`` (determinism is the
       requirement, not which of them wins).
  - **multi**: all values coexist (declared per key in the registry).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from apps.identity.models import MemoryEntry
from apps.identity.services.memory_reader import (
    GreenFact,
    PersonalContextView,
    get_personal_context,
    read_green_entries,
)

CARDINALITY_SINGLE = "single"
CARDINALITY_MULTI = "multi"

# memory key → cardinality. Pilot keys (owner ruling 2026-08-23, DRF-1260):
# `diet` and `price_range` are single (one current diet / one current budget);
# `preferred_districts`, `preferred_time_slots`, `favorite_masters` are multi
# (several districts / slots / masters legitimately coexist). Unknown keys
# default to single: conservative — no contradictions by default.
_KEY_CARDINALITY: dict[str, str] = {
    "diet": CARDINALITY_SINGLE,
    "price_range": CARDINALITY_SINGLE,
    "preferred_districts": CARDINALITY_MULTI,
    "preferred_time_slots": CARDINALITY_MULTI,
    "favorite_masters": CARDINALITY_MULTI,
}

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def key_cardinality(key: Any) -> str:
    """Cardinality for a memory key; unknown/non-str keys are single."""

    if not isinstance(key, str):
        return CARDINALITY_SINGLE
    return _KEY_CARDINALITY.get(key, CARDINALITY_SINGLE)


def _fact_key(entry: MemoryEntry) -> str | None:
    """The fact's memory key, or None when content carries no usable key."""

    content = entry.content if isinstance(entry.content, dict) else {}
    key = content.get("key")
    return key if isinstance(key, str) and key else None


def _currency(entry: MemoryEntry) -> tuple:
    """Total-order «how current is this row» key (higher wins).

    Explicit source outranks inferred/signal regardless of freshness; then
    freshest created_at; then the larger id as a stable tiebreak.
    """

    explicit = 1 if entry.source == MemoryEntry.SOURCE_EXPLICIT else 0
    return (explicit, entry.created_at or _EPOCH, entry.id.int)


def select_current_facts(entries: Sequence[MemoryEntry]) -> list[MemoryEntry]:
    """Collapse live green rows to the current fact set per the key policy.

    Single-value keys keep exactly one winning row (see module docstring);
    multi-value keys and keyless rows pass through untouched. Output keeps
    the input order (``created_at`` ASC from the reader). Pure function —
    same input rows always resolve to the same output.
    """

    winners: dict[str, MemoryEntry] = {}
    for entry in entries:
        key = _fact_key(entry)
        if key is None or key_cardinality(key) == CARDINALITY_MULTI:
            continue
        current = winners.get(key)
        if current is None or _currency(entry) > _currency(current):
            winners[key] = entry

    return [
        entry
        for entry in entries
        if (key := _fact_key(entry)) is None
        or key_cardinality(key) == CARDINALITY_MULTI
        or winners[key] is entry
    ]


def read_current_view(user_id: uuid.UUID) -> PersonalContextView:
    """Like ``memory_reader.read_personal_context``, but conflict-resolved.

    Same read gate (green-only, forgotten users invisible) and the same
    view shape; ``green_facts`` are collapsed by :func:`select_current_facts`
    so consumers never surface mutually exclusive values of one key, and each
    carries its :attr:`MemoryEntry.source` so the prompt can tell a quote from
    a guess (P0-3).
    """

    upc = get_personal_context(user_id)
    if upc is None:
        return PersonalContextView()

    facts = [
        GreenFact(
            kind=entry.kind,
            content=entry.content if isinstance(entry.content, dict) else {},
            # The resolver already reads `source` to pick a winner (`_currency`)
            # and used to throw it away right here — the single point where the
            # bot lost «who said this» (P0-3). Carry it to the consumer.
            source=entry.source,
        )
        for entry in select_current_facts(read_green_entries(user_id))
    ]
    summary = (upc.summary or "").strip() or None
    return PersonalContextView(summary=summary, green_facts=facts)
