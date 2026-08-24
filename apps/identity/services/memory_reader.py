"""Green/summary memory reader — the surfacing read path (M-C1 / #1101).

The concierge injects a user's remembered context into its system prompt so
Ayla can say «помню, что ты…». That read is served here.

# Scope — GREEN + summary only

This reader returns ONLY:
  - `UserPersonalContext.summary` (plaintext by design — see the model
    docstring; intended for LLM context on every turn), and
  - 🟢 **green** `MemoryEntry` rows (innocuous preferences).

It NEVER touches yellow or red. Yellow is voice-filtered from provider strings
(ADR-0011 §9) and red goes exclusively through the audited
`red_zone_reader.RedZoneReader` accessor (ADR-0011 §7). Surfacing green needs no
access log and no minor gate (green is allowed for minors — ADR-0011 §10).

# Read gate (ADR-0011 §11.3)

Withdrawn / deleted entries are invisible: the query filters
`soft_deleted_at IS NULL AND delete_requested_at IS NULL`. A user who invoked
forget-all (UPC `soft_deleted_at` set) gets an empty view — memory is silent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from apps.identity.models import MemoryEntry, UserPersonalContext


@dataclass(frozen=True)
class GreenFact:
    """One surfaced green memory fact (decrypted content).

    ``source`` mirrors :attr:`MemoryEntry.source` — ``explicit`` (the person
    said it), ``inferred`` (Ayla derived it from conversation) or ``signal``
    (derived from observable events). It rides along because consumers that
    render facts into the prompt MUST be able to tell a quote from a guess
    (P0-3, ``OD_C04_GROUNDED_WHY.md`` §1); collapsing it here is exactly how
    the origin used to get lost.

    The default is deliberately NOT ``explicit``: when the origin is unknown
    the safe reading is «we cannot claim the person said this», because the
    failure mode of the other default is presenting a guess as a quote.
    """

    kind: str
    content: dict[str, Any]
    source: str = MemoryEntry.SOURCE_INFERRED


@dataclass(frozen=True)
class PersonalContextView:
    """What the concierge may surface about a user this turn."""

    summary: str | None = None
    green_facts: list[GreenFact] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.summary and not self.green_facts


def get_personal_context(user_id: uuid.UUID) -> UserPersonalContext | None:
    """Return the user's live UPC, or None if absent or forgotten.

    «Forgotten» covers BOTH the completed forget-all (``soft_deleted_at`` set by
    the async sweep) AND the interim window where the user has requested
    forget-all but the sweep has not yet run (``forget_all_requested_at`` set,
    ``soft_deleted_at`` still NULL). Gating on both means memory stops surfacing
    the instant the user asks to be forgotten (152-ФЗ right-to-be-forgotten),
    not only after the async sweep completes.
    """

    return UserPersonalContext.objects.filter(
        user_id=user_id,
        soft_deleted_at__isnull=True,
        forget_all_requested_at__isnull=True,
    ).first()


def get_or_create_personal_context(user_id: uuid.UUID) -> UserPersonalContext:
    """Return the user's UPC, creating an empty one if it does not exist.

    Used by the write path (memory needs a parent UPC). A resurrected UPC is
    NOT auto-created here for a forgotten user — `get_or_create` on the PK
    returns the existing (possibly soft-deleted) row; callers that must respect
    forget-all use :func:`get_personal_context` for the read gate.
    """

    upc, _ = UserPersonalContext.objects.get_or_create(user_id=user_id)
    return upc


def read_green_entries(user_id: uuid.UUID) -> list[MemoryEntry]:
    """Return the user's live 🟢 green MemoryEntry rows (model instances).

    Same read-gate as :func:`read_personal_context` (not soft-deleted, no
    pending delete request) but returns the ORM rows — the management path
    (152-ФЗ «forget {X}») needs each row's ``id`` to soft-delete it. Returns
    ``[]`` when the UPC is absent or forgotten. Never yellow/red.

    Iterates model instances (NOT ``.values()``) so the EncryptedJSONField
    ``content`` decrypts via the field descriptor.
    """

    if get_personal_context(user_id) is None:
        return []

    return list(
        MemoryEntry.objects.filter(
            user_id=user_id,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            soft_deleted_at__isnull=True,
            delete_requested_at__isnull=True,
        ).order_by("created_at")
    )


def read_personal_context(user_id: uuid.UUID) -> PersonalContextView:
    """Read the surfaceable (summary + green) context for `user_id`.

    Returns an empty view (never raises) when there is no live UPC — the
    happy-path caller treats an empty view as «no memory to surface».
    """

    upc = get_personal_context(user_id)
    if upc is None:
        return PersonalContextView()

    facts = [
        GreenFact(kind=entry.kind, content=entry.content if isinstance(entry.content, dict) else {})
        for entry in read_green_entries(user_id)
    ]
    summary = (upc.summary or "").strip() or None
    return PersonalContextView(summary=summary, green_facts=facts)
