"""Consent-gated access to Ayla declared prefs (personal-context API).

The frozen contract v1.0 (``PERSONAL_CONTEXT_INTERNAL_API_CONTRACT.md``)
states: *«Consent-гейт (memory_green) enforce'ится на боте ДО вызова»*.
This module is that enforcement point — every function checks
:func:`apps.consent.services.has_memory_consent` (zone ``green``,
MEMORY_CONSENT_SPEC §8.1: global per ``ayla_user_id``, cross-tenant)
BEFORE the wire is touched, and short-circuits with a
:attr:`GateResult.BLOCKED_CONSENT` result otherwise.

Note the two consent bases in play (deliberate, flagged to the
orchestrator 2026-07-18): local ``MemoryEntry`` writes gate on the
PERSONAL_DATA welcome consent (ADR-0011 §11), while Ayla declared-prefs
calls gate on the ``memory_green`` consent (MEMORY_CONSENT_SPEC). The
two surfaces follow their own frozen specs; unifying them is a
Decision-Log-level call, not a code accident.

All functions return :class:`GatedResult` — callers (W5 concierge) get a
single, exception-free seam: ``BLOCKED_CONSENT`` (gate closed or user
unlinked), ``ERROR`` (upstream failure, logged), ``OK`` (payload set).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from apps.consent.services import has_memory_consent
from apps.integrations.ayla.personal_context_client import (
    AskEligibility,
    DeclaredContext,
    PersonalContextError,
    PersonalContextHttpClient,
)

logger = logging.getLogger(__name__)


class GateStatus(str, Enum):
    OK = "ok"
    BLOCKED_CONSENT = "blocked_consent"  # gate closed or no ayla_user_id
    ERROR = "error"  # upstream/transport failure (logged)


@dataclass(frozen=True)
class GatedResult:
    """Outcome of one gated call. ``context``/``eligibility``/``skip_count``
    are populated per call type on OK."""

    status: GateStatus
    context: DeclaredContext | None = None
    eligibility: AskEligibility | None = None
    skip_count: int | None = None


def _resolve_ayla_user_id(bot_user) -> uuid.UUID | None:
    raw = getattr(bot_user, "ayla_user_id", None)
    if not raw:
        return None
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


def _gate(bot_user) -> uuid.UUID | None:
    """The memory_green gate. Returns the ayla_user_id when open, else None."""
    ayla_user_id = _resolve_ayla_user_id(bot_user)
    if ayla_user_id is None:
        return None
    if not has_memory_consent(ayla_user_id, "green"):
        logger.info(
            "identity.personal_context.gate_closed bot_user=%s",
            getattr(bot_user, "id", "?"),
        )
        return None
    return ayla_user_id


def get_declared_prefs(
    bot_user,
    *,
    client: PersonalContextHttpClient | None = None,
) -> GatedResult:
    """Gated ``GET personal-context/`` — full declared-prefs catalogue."""
    ayla_user_id = _gate(bot_user)
    if ayla_user_id is None:
        return GatedResult(status=GateStatus.BLOCKED_CONSENT)
    owns = client is None
    client = client or PersonalContextHttpClient()
    try:
        return GatedResult(
            status=GateStatus.OK,
            context=client.get_context(ayla_user_id=str(ayla_user_id)),
        )
    except PersonalContextError:
        logger.exception("identity.personal_context.get_failed")
        return GatedResult(status=GateStatus.ERROR)
    finally:
        if owns:
            client.close()


def patch_declared_prefs(
    bot_user,
    updates: list[dict[str, Any]],
    *,
    client: PersonalContextHttpClient | None = None,
) -> GatedResult:
    """Gated ``PATCH personal-context/`` — batch LWW update (≤10 entries)."""
    ayla_user_id = _gate(bot_user)
    if ayla_user_id is None:
        return GatedResult(status=GateStatus.BLOCKED_CONSENT)
    owns = client is None
    client = client or PersonalContextHttpClient()
    try:
        return GatedResult(
            status=GateStatus.OK,
            context=client.patch_context(ayla_user_id=str(ayla_user_id), updates=updates),
        )
    except PersonalContextError:
        logger.exception("identity.personal_context.patch_failed")
        return GatedResult(status=GateStatus.ERROR)
    finally:
        if owns:
            client.close()


def get_ask_eligibility(
    bot_user,
    *,
    client: PersonalContextHttpClient | None = None,
) -> GatedResult:
    """Gated ``GET ask-eligibility/`` — the ONE field Ayla allows asking."""
    ayla_user_id = _gate(bot_user)
    if ayla_user_id is None:
        return GatedResult(status=GateStatus.BLOCKED_CONSENT)
    owns = client is None
    client = client or PersonalContextHttpClient()
    try:
        return GatedResult(
            status=GateStatus.OK,
            eligibility=client.get_ask_eligibility(ayla_user_id=str(ayla_user_id)),
        )
    except PersonalContextError:
        logger.exception("identity.personal_context.ask_eligibility_failed")
        return GatedResult(status=GateStatus.ERROR)
    finally:
        if owns:
            client.close()


def mark_asked(
    bot_user,
    field: str,
    *,
    client: PersonalContextHttpClient | None = None,
) -> GatedResult:
    """Gated ``POST mark-asked/`` — stamps the 24h cooldown (non-idempotent)."""
    ayla_user_id = _gate(bot_user)
    if ayla_user_id is None:
        return GatedResult(status=GateStatus.BLOCKED_CONSENT)
    owns = client is None
    client = client or PersonalContextHttpClient()
    try:
        client.mark_asked(ayla_user_id=str(ayla_user_id), field=field)
        return GatedResult(status=GateStatus.OK)
    except PersonalContextError:
        logger.exception("identity.personal_context.mark_asked_failed")
        return GatedResult(status=GateStatus.ERROR)
    finally:
        if owns:
            client.close()


def skip(
    bot_user,
    field: str,
    *,
    client: PersonalContextHttpClient | None = None,
) -> GatedResult:
    """Gated ``POST skip/`` — increments the skip counter (non-idempotent)."""
    ayla_user_id = _gate(bot_user)
    if ayla_user_id is None:
        return GatedResult(status=GateStatus.BLOCKED_CONSENT)
    owns = client is None
    client = client or PersonalContextHttpClient()
    try:
        return GatedResult(
            status=GateStatus.OK,
            skip_count=client.skip(ayla_user_id=str(ayla_user_id), field=field),
        )
    except PersonalContextError:
        logger.exception("identity.personal_context.skip_failed")
        return GatedResult(status=GateStatus.ERROR)
    finally:
        if owns:
            client.close()
