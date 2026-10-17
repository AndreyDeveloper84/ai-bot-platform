"""Inferred green-memory persistence (S3-B, pilot 2026-08-15).

.. deprecated:: Migration Plan Step 3.5 audit (2026-08-20)
   This writer has ZERO production callers (only tests exercise it) and is
   kept dormant: per the canonical contract (MDC §3.1/§3.2, AYLA-DEC-0024)
   inference may become persistent memory ONLY through the proposal flow
   (MemoryProposal, Step 4+). Its semantics are intentionally NOT changed
   here — in particular, rows it writes keep provenance=NULL, since
   user_confirmed_inference requires explicit user confirmation.

The sanctioned write path for **inferred** 🟢 green facts — facts the
platform derives (behavioral / conversational signals) rather than the
user stating them verbatim. Complements the explicit path
(``apps.orchestrator.memory.personal_context.record_explicit_green_facts``,
W5-owned) with the same guarantees, kept inside the W3 zone
(``apps.identity``) so the concierge (W5) can call a stable seam:

  - **Consent-gated.** Writes only when
    :func:`apps.consent.memory.can_store_green_memory` passes (green's
    152-ФЗ basis = PERSONAL_DATA welcome consent, ADR-0011 §11), the
    voluntary preference-inference consent is active
    (:mod:`apps.consent.preference_inference`, DRF-2779, owner 05.10), and
    the user has a canonical ``ayla_user_id``.
  - **Deduped.** A live fact with the same ``(kind, key, value)`` is not
    re-written — re-inferring the same value never churns the store. A
    *changed* value lands as a new entry (history preserved; readers
    order by ``created_at``).
  - **Erased-by-request respected (DRF-2837).** A ``(kind, key, value)`` the
    person asked to forget — said or proposed, deleted in chat or on the
    memory screen — is not written again while its tombstone lies
    (:mod:`apps.identity.services.erased_by_request`). The rule sits HERE, on
    the only door inferred rows are written through, so a future pipeline
    cannot forget it. An inference that merely expired (``ttl_purge``) is not
    blocked: a term is not the person's will.
  - **Forget-all respected.** No new memory accretes onto a tombstoned
    UPC (152-ФЗ right-to-be-forgotten).
  - **Never breaks the caller.** All work is best-effort; failures are
    logged and swallowed.

Per the schema (``docs/specs/memory-entry-schema.md`` CHECK 1), inferred
rows carry ``source='inferred'`` + ``last_inferred_at=now``. Zone stays
🟢 green — inferred yellow/red is out of pilot scope (the writer
fail-closes those anyway per ADR-0011 §10.2).

Layering note: this module deliberately imports the consent gate
(``apps.consent.memory``) — a one-directional edge identity → consent
(consent imports identity lazily, so no import cycle). The gate must
live ON the write path here because the pilot brief forbids relying on
W5-side callers to enforce it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from apps.consent.memory import can_store_green_memory
from apps.identity.models import MemoryEntry
from apps.identity.services.erased_by_request import keys_erased_by_request
from apps.identity.services.memory_reader import (
    get_or_create_personal_context,
    read_personal_context,
)
from apps.identity.services.memory_writer import write_entry

logger = logging.getLogger(__name__)

# Purpose tag stamped on the write path (audit / provenance).
_WRITE_PURPOSE = "discovery:inferred_green_fact"


@dataclass(frozen=True)
class InferredGreenFact:
    """One inferred green fact to persist.

    ``content`` MUST carry ``key``/``value`` entries — the dedup
    convention shared with the explicit path is
    ``(kind, content["key"], content["value"])``.
    """

    kind: str
    content: dict[str, Any]
    ttl_days: int | None = None


def record_inferred_green_facts(
    bot_user,
    facts: list[InferredGreenFact],
    *,
    source_tenant_id: uuid.UUID | None = None,
) -> int:
    """Persist inferred green facts for ``bot_user``. Returns count written.

    No-op (returns 0) when: no ``ayla_user_id``, consent gate closed,
    empty input, every fact already live, or the UPC is forgotten.
    """
    if not facts:
        return 0
    ayla_user_id = getattr(bot_user, "ayla_user_id", None)
    if not ayla_user_id:
        return 0
    if not can_store_green_memory(bot_user):
        logger.info(
            "identity.memory.inferred_gate_closed bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        return 0
    # DRF-2779 (умная память Ф4): выводимое пишется только под отдельным
    # добровольным согласием на предположения. Основание зелёной зоны
    # (personal_data) разрешает ХРАНИТЬ сказанное человеком; анализировать
    # его обращения и действия ради предложений — другой предмет, и решение
    # владельца 05.10 вынесло его в своё согласие. Fail-closed: сбой чтения
    # согласия — не запись.
    try:
        from apps.consent.preference_inference import is_granted as preference_inference_granted

        inference_allowed = preference_inference_granted(bot_user)
    except Exception:  # noqa: BLE001 — a failed consent read must not open the gate
        logger.exception(
            "identity.memory.inferred_consent_read_failed bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        inference_allowed = False
    if not inference_allowed:
        logger.info(
            "identity.memory.inferred_gate_closed bot_user=%s reason=preference_inference",
            getattr(bot_user, "id", "?"),
        )
        return 0

    try:
        user_id = (
            ayla_user_id if isinstance(ayla_user_id, uuid.UUID) else uuid.UUID(str(ayla_user_id))
        )

        existing = read_personal_context(user_id)
        seen = {
            (f.kind, f.content.get("key"), f.content.get("value")) for f in existing.green_facts
        }

        upc = get_or_create_personal_context(user_id)
        if upc.soft_deleted_at is not None or upc.forget_all_requested_at is not None:
            return 0

        # DRF-2837: дедуп выше видит только живые строки. Стёртое по просьбе
        # лежит надгробием, и без этого чтения следующий прогон предложил бы
        # его снова — без единого нового слова человека.
        erased = keys_erased_by_request(user_id)

        now = timezone.now()
        written = 0
        blocked = 0
        for fact in facts:
            dedup_key = (fact.kind, fact.content.get("key"), fact.content.get("value"))
            if dedup_key in seen:
                continue
            if dedup_key in erased:
                blocked += 1
                continue
            entry = write_entry(
                user_id=user_id,
                personal_context=upc,
                sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
                source=MemoryEntry.SOURCE_INFERRED,
                kind=fact.kind,
                content=fact.content,
                request_id=uuid.uuid4(),
                purpose=_WRITE_PURPOSE,
                consent_at=None,  # green: service-contract basis
                source_tenant_id=source_tenant_id,
                last_inferred_at=now,  # CHECK 1: required for inferred rows
                ttl_days=fact.ttl_days,
            )
            if entry is not None:
                written += 1
                seen.add(dedup_key)

        if blocked:
            # Число, без рода и значения: что именно человек стёр — не для журнала.
            logger.info(
                "identity.memory.inferred_blocked_erased bot_user=%s count=%d",
                bot_user.id,
                blocked,
            )
        if written:
            # Count + kinds only — never the inferred values.
            logger.info(
                "identity.memory.inferred_written bot_user=%s count=%d kinds=%s",
                bot_user.id,
                written,
                sorted({f.kind for f in facts}),
            )
        return written
    except Exception:  # noqa: BLE001 — memory write must never break the caller
        logger.exception(
            "identity.memory.inferred_write_failed bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        return 0
