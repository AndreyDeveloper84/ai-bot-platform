"""Explicit green-fact write orchestration (M-B2 / #1099, pilot-narrow).

Coordinates the one write path the pilot activates: a user's **spontaneous
explicit** green fact (e.g. «я веган») → a 🟢 green `MemoryEntry`. Ties together
the consent gate (``apps.consent``), the extractor (``apps.persona``), the UPC
resolver + writer (``apps.identity``) — the orchestrator layer owns this
cross-app coordination so ``apps.identity`` keeps no upward dependency on
consent/persona.

Guarantees:
  - **Consent-gated.** Writes only when the user holds active PERSONAL_DATA
    consent (green's 152-ФЗ basis, ADR-0011 §11) AND we have a canonical
    ``ayla_user_id``. Otherwise a no-op.
  - **Idempotent.** A fact already stored live (same kind/key/value) is not
    re-written — repeated «я веган» never appends duplicates.
  - **Never breaks the turn.** All work is best-effort; any failure is logged
    and swallowed so the conversation is unaffected (happy-path intact).
"""

from __future__ import annotations

import logging
import uuid

from apps.consent.memory import can_store_green_memory
from apps.identity.models import MemoryEntry
from apps.identity.services.ayla_link import ensure_ayla_link
from apps.identity.services.memory_key_policy import CARDINALITY_SINGLE, key_cardinality
from apps.identity.services.memory_reader import (
    get_or_create_personal_context,
    read_green_entries,
    read_personal_context,
)
from apps.identity.services.memory_writer import supersede_entries, write_entry
from apps.persona.memory_extract import extract_user_facts

logger = logging.getLogger(__name__)

# Purpose tag stamped on the write path (audit / provenance).
_WRITE_PURPOSE = "discovery:explicit_green_fact"


def record_explicit_green_facts(bot_user, text: str) -> int:
    """Extract + persist explicit green facts from a user turn. Returns count written.

    No-op (returns 0) when: no active PERSONAL_DATA consent, nothing extracted,
    identity could not be resolved, or every extracted fact already exists live.

    DRF-1035 — gate order is deliberate: consent, then extraction, then identity.
    Persisting memory needs a permanent Ayla subject, so this is an
    identity-dependent action; but a turn that ends up storing nothing must not
    mint one. Resolving last means «hello» never creates an identity, while the
    first turn that actually has a fact to keep does (owner ruling J-O3,
    identity-on-first-dependent-action).
    """

    if not can_store_green_memory(bot_user):
        return 0

    try:
        result = extract_user_facts(text)
        candidates = result.candidates
        if result.drops:
            # Explicit drops (allergy perimeter, contract gaps) — counted in
            # logs, never stored, never silent (DRF-1290 / owner ruling).
            logger.info(
                "orchestrator.memory.extraction_drops bot_user=%s reasons=%s",
                bot_user.id,
                sorted({d.reason for d in result.drops}),
            )
        if not candidates:
            return 0

        user_id = ensure_ayla_link(bot_user, trigger="memory_write")
        if user_id is None:
            # Ayla unreachable, or resolution failed. Dropping the fact is the
            # correct degradation: memory is keyed on this id, so there is no
            # valid key to write under. The next turn retries.
            return 0

        # Dedup against facts already stored live (read gate applied).
        existing = read_personal_context(user_id)
        seen = {
            (f.kind, f.content.get("key"), f.content.get("value")) for f in existing.green_facts
        }
        # Live rows for the supersession lifecycle (single-cardinality keys):
        # a new explicit value displaces the previous live rows of its key
        # with reason=changed (DRF-1261 «исправляю» path).
        live_rows = read_green_entries(user_id)

        # UPC parent must exist for the FK; create an empty one if needed.
        upc = get_or_create_personal_context(user_id)

        # Never accrete new memory onto a forgotten user. Erasure of memory
        # (forget-all) is independent of PERSONAL_DATA consent, so the consent
        # gate above does not cover this: a user who invoked forget-all but kept
        # consent must not have «я веган» re-written onto their tombstoned UPC
        # (152-ФЗ right-to-be-forgotten).
        if upc.soft_deleted_at is not None or upc.forget_all_requested_at is not None:
            return 0

        written = 0
        for candidate in candidates:
            if candidate.dedup_key in seen:
                continue
            entry = write_entry(
                user_id=user_id,
                personal_context=upc,
                sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
                source=MemoryEntry.SOURCE_EXPLICIT,
                kind=candidate.kind,
                content=candidate.content,
                request_id=uuid.uuid4(),
                purpose=_WRITE_PURPOSE,
                consent_at=None,  # green: service-contract basis, no per-entry consent
            )
            if entry is not None:
                written += 1
                seen.add(candidate.dedup_key)
                key = candidate.content.get("key")
                if key_cardinality(key) == CARDINALITY_SINGLE:
                    displaced = [
                        row
                        for row in live_rows
                        if row.id != entry.id
                        and isinstance(row.content, dict)
                        and row.content.get("key") == key
                        and (row.kind, key, row.content.get("value")) != candidate.dedup_key
                    ]
                    if displaced:
                        supersede_entries(replaced_by=entry, entries=displaced)

        if written:
            # Observe-only fill-rate signal (structured log — count + kinds only,
            # never the fact value). Fill-rate is also queryable directly off the
            # green MemoryEntry count per user.
            logger.info(
                "orchestrator.memory.green_written bot_user=%s count=%d kinds=%s",
                bot_user.id,
                written,
                sorted({c.kind for c in candidates}),
            )

        # Bridge (DRF-1261): mirror this turn's user-stated facts into the
        # Ayla declared prefs. ALL extracted candidates are offered (not only
        # newly written rows) — PATCH is idempotent LWW, so a repeated
        # statement heals a transient upstream failure. Best-effort inside.
        try:
            from apps.orchestrator.memory.ayla_bridge import bridge_candidates_to_ayla

            bridge_candidates_to_ayla(bot_user, candidates)
        except Exception:  # noqa: BLE001 — the bridge must never break the turn
            logger.exception(
                "orchestrator.memory.bridge_failed bot_user=%s",
                getattr(bot_user, "id", "?"),
            )
        return written
    except Exception:  # noqa: BLE001 — memory write must never break the turn
        logger.exception(
            "orchestrator.memory.green_write_failed bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        return 0
