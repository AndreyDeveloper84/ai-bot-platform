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

Provenance policy (P0-3, ``OD_C04_GROUNDED_WHY.md`` §1) — SEPARATE from
confidence, and do not conflate the two. Confidence answers «насколько
уверены» and only picks the «кажется» hedge; provenance answers «кто это
сказал». They are not derived from each other, and the pair the adapter
used to produce was the wrong way round in BOTH directions:

* backend-derived ``busy_days`` / ``favorite_masters`` (nightly inference,
  ``users/personal_context_inference.py``) arrived через declared prefs and
  were asserted flatly, as if the person had said them;
* a locally EXTRACTED user statement («не ем морепродукты», written with
  ``source='explicit'``) was hedged «кажется», because the adapter judged
  by STORE, not by origin.

So both sides now emit a per-field origin and pass it to ai-core:

* declared prefs → backend ``data_sources`` (``explicit`` = the person typed
  it, anything else = derived). Absent from the payload → the old, all-stated
  behaviour, byte for byte: a bot deploy must not turn every declared fact
  into a «догадка» just because the backend has not shipped the field yet.
* local ``MemoryEntry`` → :attr:`MemoryEntry.source` (``explicit`` = stated,
  ``inferred``/``signal`` = derived).

One border, two values. Not a trust model — that is a separate owner
decision (audit §24 P0-3, «confidence is an ephemeral display parameter»).

Fail-closed on every error: memory surfacing must never break the turn.

Concierge Mode rollback (runbook §7, W5): ``CONCIERGE_MEMORY_ENABLED=false``
in env disables this whole surface without a deploy — see
:func:`concierge_memory_enabled`.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from ayla_ai_core import SOURCE_INFERRED, SOURCE_STATED, build_memory_block

from apps.identity.models import MemoryEntry
from apps.identity.services.memory_key_policy import CARDINALITY_MULTI, key_cardinality
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

# Values allowed to reach the block as a diet_type (Ayla contract vocabulary).
_DIET_TYPE_VOCAB = frozenset(
    {"omnivore", "vegetarian", "vegan", "keto", "halal", "kosher", "other"}
)

_DECLARED_CONFIDENCE = 1.0
_INFERRED_CONFIDENCE = 0.6

# Backend `UserPersonalContext.data_sources` value that means «the person
# typed this». Everything else the backend can stamp — `inferred` (nightly
# booking-history inference), `behavioral`, `transactional`, `conversational`
# — is a derivation, and so is any value we do not recognise.
_BACKEND_STATED_SOURCE = "explicit"


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
    sources: dict[str, str] = {}
    declared_origins = _declared_origins(declared.context)
    for key, value in (declared.context.context or {}).items():
        if key == "preferred_time_slots" and isinstance(value, list):
            value = [_SLOT_DISPLAY.get(s, s) for s in value]
        facts[key] = value
        confidences[key] = _DECLARED_CONFIDENCE
        if declared_origins is not None:
            sources[key] = (
                SOURCE_STATED
                if declared_origins.get(key, _BACKEND_STATED_SOURCE) == _BACKEND_STATED_SOURCE
                else SOURCE_INFERRED
            )

    _merge_inferred(bot_user, facts, confidences, sources)

    try:
        return build_memory_block(facts, confidences=confidences, sources=sources)
    except Exception:  # noqa: BLE001 — surfacing must never break the turn
        logger.exception("orchestrator.memory_block.build_failed")
        return ""


def _declared_origins(context: Any) -> dict[str, str] | None:
    """Backend per-field ``data_sources``, or None when it is not on the wire.

    None is load-bearing, not «empty»: it means the backend has not shipped
    the field yet, and the declared side must then render EXACTLY as before
    (everything unmarked). Marking every declared fact «догадка» because a
    deploy landed out of order would be a worse lie than the one we are
    fixing — the bot PR and the backend PR are independently deployable and
    this is the seam that makes that safe.

    Read off ``DeclaredContext.raw`` (the verbatim payload the client keeps
    for forward-compat) rather than a new DTO field: the HTTP client is not
    this task's territory and ``raw`` is exactly the documented escape hatch.
    """
    raw = getattr(context, "raw", None)
    if not isinstance(raw, dict):
        return None
    origins = raw.get("data_sources")
    if not isinstance(origins, dict):
        return None
    return {k: v for k, v in origins.items() if isinstance(k, str) and isinstance(v, str)}


def _merge_inferred(bot_user: Any, facts: dict, confidences: dict, sources: dict) -> None:
    """Merge bot-side green MemoryEntry facts (own consent basis).

    Despite the name this store holds BOTH origins — the deterministic
    extractor writes ``source='explicit'`` for what the person said, the
    inferred writer writes ``source='inferred'`` — and the two must not
    reach the model looking alike (P0-3). Hence ``sources``.

    Declared values win on key conflicts (user-stated beats inferred).
    Live rows are pre-resolved by the key policy (single-value keys
    surface ONE current value — an explicit correction beats a fresher
    inferred row), so a superseded fact never reaches the block.
    Any failure degrades to declared-only — never raises.
    """
    try:
        from apps.consent.memory import can_store_green_memory

        if not can_store_green_memory(bot_user):
            return
        ayla_user_id = getattr(bot_user, "ayla_user_id", None)
        if not ayla_user_id:
            return
        from apps.identity.services.memory_key_policy import read_current_view

        view = read_current_view(ayla_user_id)
    except Exception:  # noqa: BLE001
        logger.exception("orchestrator.memory_block.inferred_failed")
        return

    for fact in view.green_facts:
        content = fact.content if isinstance(fact.content, dict) else {}
        raw_key = cast(str, content.get("key"))
        # DRF-1261 keys whose local value shape is NOT the declared one:
        # `price_range` carries a compact "min:…,max:…" scalar and
        # `favorite_masters` carries a NAME (the block would render it as a
        # bogus «id=Анна»). Both reach the prompt through the DECLARED side
        # post-bridge; the local row is for the show/forget loop.
        if raw_key in ("price_range", "favorite_masters"):
            continue
        key = _INFERRED_KEY_MAP.get(raw_key, raw_key)
        value = content.get("value")
        if raw_key == "diet":
            # Only a named diet type is block-safe. New rows carry an
            # explicit `diet_type`; legacy rows carry only `value` (already
            # a diet-type word); «none» (retraction) and exclusion values
            # are not diet_type vocabulary and never reach the block.
            dt = content.get("diet_type")
            if isinstance(dt, str) and dt:
                value = dt
            elif value not in _DIET_TYPE_VOCAB:
                value = None
        elif raw_key == "preferred_time_slots" and isinstance(value, str):
            # Same display translation the declared side applies (contract
            # slot vocabulary → ai-core label keys).
            value = _SLOT_DISPLAY.get(value, value)
        if not (isinstance(key, str) and value not in (None, "", [])):
            continue
        origin = SOURCE_STATED if fact.source == MemoryEntry.SOURCE_EXPLICIT else SOURCE_INFERRED
        if key_cardinality(raw_key) == CARDINALITY_MULTI:
            # Multi-value keys accrete into a list — but never onto a
            # declared value (declared wins on key conflicts).
            if key not in facts:
                facts[key] = [value]
                confidences[key] = _INFERRED_CONFIDENCE
                sources[key] = origin
            elif confidences.get(key) == _INFERRED_CONFIDENCE and isinstance(facts[key], list):
                facts[key].append(value)
                # One rendered line, one origin: a list that mixes a quote
                # with a guess cannot honestly be labelled a quote.
                if origin == SOURCE_INFERRED:
                    sources[key] = SOURCE_INFERRED
        elif key not in facts:
            facts[key] = value
            confidences[key] = _INFERRED_CONFIDENCE
            sources[key] = origin
