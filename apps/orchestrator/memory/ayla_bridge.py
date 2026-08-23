"""Bridge: bot-side user-stated memory facts → Ayla declared prefs (DRF-1261).

The missing writer between «человек сказал» in the bot and the fields of
``users_userpersonalcontext``. The transport (``PersonalContextHttpClient``,
PATCH batch LWW idempotent) and the consent-gated service
(``apps.identity.services.personal_context``) already existed — this module
is the mapping + orchestration that calls them.

# What maps where (frozen contract v1.0 catalogue)

  preferred_time_slots  -> ``preferred_time_slots`` (union merge, LWW)
  preferred_districts   -> ``preferred_districts``  (union merge, LWW)
  price_range           -> ``price_range_min`` / ``price_range_max``
  diet (named type)     -> ``diet_type``
  diet retraction       -> ``diet_type=""`` (explicit clear)

# What deliberately does NOT map (contract gaps — report, not code)

  - ``excluded_foods`` / ``user_note``: NO receiver field (owner ruling
    Ответ 3). The extractor already drops these loudly; nothing reaches
    here.
  - ``favorite_masters``: the contract wants ``SpecialistProfile`` UUIDs;
    an explicitly named master («мой мастер — Анна») is a NAME and cannot
    be resolved cross-tenant (the same limit ``memory_ask`` documents).
    Stored bot-side only; logged here on every attempt.
  - ``skin_sensitivities``: field exists in Ayla, but the owner ruling
    (DRF-1290) forbids activating it as green memory — never written.
  - Clearing ``price_range_min/max``: the contract's ``value`` JSONField
    rejects null and empty-string would break the Decimal column — a
    forget of the budget key CANNOT clear Ayla-side values. Logged as a
    contract gap; contract extension proposed in the report.

# Consent

Silent-remember ruling (owner, 2026-08-23): no per-write confirmation —
the write basis is the onboarding consent, enforced by the gated service
(``memory_green`` scope) before the wire is touched. What justifies the
silence is the show/forget loop — see ``apps.persona.memory_commands``.

Best-effort: every failure is logged and swallowed — the local MemoryEntry
write already happened and the bridge must never break the turn. PATCH is
idempotent LWW, so the next user statement heals a transient failure.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from apps.identity.services.personal_context import (
    GateStatus,
    get_declared_prefs,
    patch_declared_prefs,
)
from apps.persona.memory_extract import GreenFactCandidate

logger = logging.getLogger(__name__)

# Fields this bridge writes — and therefore the only ones «забудь» may clear.
# favorite_masters is Ayla-engine-owned (rebook>=3 semantics) and price has no
# clear encoding (see module docstring) — neither is touched on forget.
_CLEARABLE_FIELDS: dict[str, list[tuple[str, Any]]] = {
    "diet": [("diet_type", "")],
    "preferred_time_slots": [("preferred_time_slots", [])],
    "preferred_districts": [("preferred_districts", [])],
}

_ALL_BRIDGE_KEYS = frozenset(_CLEARABLE_FIELDS) | {"price_range", "favorite_masters"}


def _key(candidate: GreenFactCandidate) -> str | None:
    key = candidate.content.get("key")
    return key if isinstance(key, str) else None


def bridge_candidates_to_ayla(
    bot_user: Any,
    candidates: Iterable[GreenFactCandidate],
    *,
    client: Any = None,
) -> int:
    """PATCH the Ayla declared prefs for this turn's candidates. Fields patched.

    0 when nothing is bridgeable, the gate is closed, or upstream failed.
    Never raises — the local write already landed; the bridge heals on the
    next statement (PATCH is idempotent LWW).
    """

    candidates = list(candidates)
    if not candidates:
        return 0

    slots: list[str] = []
    districts: list[str] = []
    price: GreenFactCandidate | None = None
    diet: GreenFactCandidate | None = None
    for candidate in candidates:
        key = _key(candidate)
        value = candidate.content.get("value")
        if key == "preferred_time_slots" and isinstance(value, str):
            if value not in slots:
                slots.append(value)
        elif key == "preferred_districts" and isinstance(value, str):
            if value not in districts:
                districts.append(value)
        elif key == "price_range":
            price = candidate  # extractor emits at most one per turn
        elif key == "diet":
            diet = candidate
        elif key == "favorite_masters":
            logger.info(
                "orchestrator.memory_bridge.favorite_master_unbridgeable — "
                "contract wants SpecialistProfile UUIDs, name stays bot-side"
            )

    updates: list[dict[str, Any]] = []

    current: dict[str, Any] = {}
    if slots or districts:
        declared = get_declared_prefs(bot_user, client=client)
        if declared.status is not GateStatus.OK or declared.context is None:
            logger.info("orchestrator.memory_bridge.read_blocked")
            return 0
        current = declared.context.context or {}

    def _union(field: str, new_values: list[str]) -> None:
        existing = current.get(field)
        merged = [v for v in existing if isinstance(v, str)] if isinstance(existing, list) else []
        changed = False
        for value in new_values:
            if value not in merged:
                merged.append(value)
                changed = True
        if changed:
            updates.append({"field": field, "value": merged, "source": "explicit"})

    if slots:
        _union("preferred_time_slots", slots)
    if districts:
        _union("preferred_districts", districts)
    if price is not None:
        for bound in ("min", "max"):
            raw = price.content.get(bound)
            if isinstance(raw, str) and raw:
                updates.append(
                    {"field": f"price_range_{bound}", "value": raw, "source": "explicit"}
                )
    if diet is not None:
        diet_type = diet.content.get("diet_type")
        if isinstance(diet_type, str) and diet_type:
            updates.append({"field": "diet_type", "value": diet_type, "source": "explicit"})
        elif diet.content.get("value") == "none":
            # «я теперь снова ем мясо» — the correction must also reach Ayla.
            updates.append({"field": "diet_type", "value": "", "source": "explicit"})

    if not updates:
        return 0
    result = patch_declared_prefs(bot_user, updates, client=client)
    if result.status is not GateStatus.OK:
        logger.info(
            "orchestrator.memory_bridge.patch_blocked fields=%s",
            ",".join(u["field"] for u in updates),
        )
        return 0
    logger.info(
        "orchestrator.memory_bridge.patched fields=%s",
        ",".join(u["field"] for u in updates),
    )
    return len(updates)


def clear_declared_fields(
    bot_user: Any,
    memory_keys: Iterable[str],
    *,
    client: Any = None,
) -> int:
    """Clear Ayla declared fields for forgotten memory keys. Fields cleared.

    «Забыть» must be real, not a mark: when the user forgets a domain, the
    Ayla-side declared value goes back to its empty default in the same
    best-effort spirit. Only bridge-owned fields are cleared (see
    ``_CLEARABLE_FIELDS``); ``price_range`` has no clear encoding in the
    frozen contract — logged as a gap, never guessed.
    """

    updates: list[dict[str, Any]] = []
    for key in dict.fromkeys(memory_keys):
        if key in ("price_range", "favorite_masters"):
            # price: null is rejected by the contract serializer, "" breaks
            # the Decimal column — no honest clear encoding (contract gap).
            # favorite_masters: never bridge-written; Ayla-engine-owned.
            logger.warning(
                "orchestrator.memory_bridge.clear_skipped key=%s — no clear "
                "encoding in the frozen contract (contract gap)",
                key,
            )
            continue
        for field, empty in _CLEARABLE_FIELDS.get(key, []):
            updates.append({"field": field, "value": empty, "source": "explicit"})
    if not updates:
        return 0
    result = patch_declared_prefs(bot_user, updates, client=client)
    if result.status is not GateStatus.OK:
        logger.info("orchestrator.memory_bridge.clear_blocked")
        return 0
    return len(updates)
