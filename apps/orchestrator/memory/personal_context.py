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
from apps.identity.services.memory_reader import (
    get_or_create_personal_context,
    read_personal_context,
)
from apps.identity.services.memory_writer import write_entry
from apps.persona.memory_extract import extract_green_facts

logger = logging.getLogger(__name__)

# Purpose tag stamped on the write path (audit / provenance).
_WRITE_PURPOSE = "discovery:explicit_green_fact"


def record_explicit_green_facts(bot_user, text: str) -> int:
    """Extract + persist explicit green facts from a user turn. Returns count written.

    No-op (returns 0) when: no ``ayla_user_id``, no active PERSONAL_DATA consent,
    nothing extracted, or every extracted fact already exists live.
    """

    ayla_user_id = getattr(bot_user, "ayla_user_id", None)
    if not ayla_user_id:
        return 0
    if not can_store_green_memory(bot_user):
        return 0

    try:
        candidates = extract_green_facts(text)
        if not candidates:
            return 0

        user_id = (
            ayla_user_id if isinstance(ayla_user_id, uuid.UUID) else uuid.UUID(str(ayla_user_id))
        )

        # Dedup against facts already stored live (read gate applied).
        existing = read_personal_context(user_id)
        seen = {
            (f.kind, f.content.get("key"), f.content.get("value")) for f in existing.green_facts
        }

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
        return written
    except Exception:  # noqa: BLE001 — memory write must never break the turn
        logger.exception(
            "orchestrator.memory.green_write_failed bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        return 0
