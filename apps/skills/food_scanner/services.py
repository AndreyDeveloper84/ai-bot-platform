"""Shared helpers for the food-scanner skill + miniapp_api endpoints.

Веха 2 (founder verdict 2026-06-02). Pure functions that own:

* ED-mode detection — OR of the two profile signals (server-applied
  ``goal_overridden_by`` override and user-declared ``health_flags``).
* Redaction of nutrition payloads when ED-mode is active. The
  backend strips numeric fields BEFORE the wire so a client bug or
  an older Mini App version cannot accidentally render counts.

The skill (apps/skills/food_scanner/skill.py) is gradually being
refactored to call these helpers; today they back the miniapp_api
endpoints. Keeping them here (not in apps/miniapp_api) means the
MAX-channel skill can reuse the exact same redaction logic without
the views layer becoming a dependency.

See ``docs/architecture/food-scanner-api-contract.md`` for the wire
contract these helpers exist to enforce.
"""

from __future__ import annotations

from typing import Any

from apps.integrations.ayla.nutrition_client import (
    FoodLogResponse,
    ProfileResponse,
    ScanResponse,
    SummaryResponse,
)

# Адверсариальный обзор PRE_PILOT #P2 — explicit allowlist for the
# nutrition payload. The Ayla schema may grow new numeric fields
# (deficit_surplus, score, kcal_from_beverages, …) which would
# otherwise ship to the wire without a contract-side review. Pinning
# the allowlist here means a schema drift is caught the moment Ayla
# starts returning it (the field is dropped, frontend doesn't render
# something we didn't promise). When the contract is extended, add
# the new key here and to the spec document in the same PR.
_NUTRITION_ALLOWLIST: frozenset[str] = frozenset({"calories", "protein_g", "fat_g", "carbs_g"})


def _filter_nutrition(raw: Any) -> dict[str, Any] | None:
    """Apply the nutrition-payload allowlist or return None.

    ``None`` / non-dict inputs pass through as ``None`` so callers can
    render «не распознано» when Ayla didn't compute nutrition.
    """

    if not isinstance(raw, dict):
        return None
    return {k: v for k, v in raw.items() if k in _NUTRITION_ALLOWLIST}


def is_ed_mode_active(profile: ProfileResponse | None) -> bool:
    """OR of the two ED-mode signals.

    1. ``ProfileResponse.goal_overridden_by == "eating_disorder"`` —
       Ayla applied a server-side goal override because the user's
       declared context warrants it.
    2. ``ProfileResponse.health_flags["eating_disorder"]`` — the user
       declared the condition directly in the nutrition anketa.

    Either signal alone activates ED-mode. Both being absent (or
    ``profile is None``) returns False — the food endpoints render
    full nutrition.

    Callers that cannot fetch the profile (Ayla down) should treat
    the return value as advisory for STYLING only; food endpoints
    still apply their own redaction inside ``redact_*`` based on the
    same source — defence-in-depth.
    """

    if profile is None:
        return False
    if profile.goal_overridden_by == "eating_disorder":
        return True
    flags = profile.health_flags or {}
    return bool(flags.get("eating_disorder"))


def redact_scan_for_ed(scan: ScanResponse, *, ed_mode: bool) -> dict[str, Any]:
    """Serialise a ``ScanResponse`` for the wire, honouring ED-mode.

    Always preserves ``dish_name`` / ``portion_g`` / ``confidence`` /
    ``provider`` — the «neutral meal log» the founder asked for. The
    ``nutrition`` dict is replaced with ``null`` when ED-mode is
    active so the Mini App's render layer has nothing to display.
    """

    return {
        "scan_id": scan.scan_id,
        "dish_name": scan.dish_name,
        "confidence": scan.confidence,
        "portion_g": scan.portion_g,
        "nutrition": None if ed_mode else _filter_nutrition(scan.nutrition),
        "provider": scan.provider,
        "ed_mode": ed_mode,
    }


def redact_log_for_ed(log: FoodLogResponse, *, ed_mode: bool) -> dict[str, Any]:
    """Serialise a ``FoodLogResponse`` for the wire, honouring ED-mode.

    ``calories`` is the only numeric field on the log row that exits
    the bot-platform boundary; ED-mode replaces it with ``null``.
    """

    return {
        "log_id": log.log_id,
        "dish_name": log.dish_name,
        "meal_type": log.meal_type,
        "calories": None if ed_mode else log.calories,
        "ed_mode": ed_mode,
    }


def redact_summary_for_ed(summary: SummaryResponse, *, ed_mode: bool) -> dict[str, Any]:
    """Serialise a ``SummaryResponse`` for the wire, honouring ED-mode.

    Strips daily totals + targets + per-entry nutrition while
    preserving entry timestamps + names + meal types. This is the
    «neutral diary» surface the F3-diary Mini App screen renders
    when the customer is in ED-mode.
    """

    entries: list[dict[str, Any]] = []
    for raw in summary.entries or []:
        entry = {
            "log_id": raw.get("log_id") or raw.get("id") or "",
            "dish_name": raw.get("dish_name") or raw.get("name") or "",
            "meal_type": raw.get("meal_type") or "other",
            "logged_at": raw.get("logged_at") or raw.get("created_at") or "",
            "nutrition": None if ed_mode else _filter_nutrition(raw.get("nutrition")),
        }
        entries.append(entry)

    payload: dict[str, Any] = {
        "date": summary.date,
        "calories_total": None if ed_mode else round(summary.calories_total),
        "calories_goal": None if ed_mode else int(summary.calories_goal),
        "protein_g": None if ed_mode else round(summary.protein_g),
        "fat_g": None if ed_mode else round(summary.fat_g),
        "carbs_g": None if ed_mode else round(summary.carbs_g),
        # Адверсариальный обзор PRE_PILOT #P1 — explicit ai_comment
        # redaction. Ayla returns a free-form Russian-language comment
        # alongside the daily summary; the prose can embed numeric
        # counts («сегодня 1450 ккал из 1800») which would bypass
        # every numeric redaction above. Until Ayla guarantees a
        # numeric-free comment, we drop ai_comment entirely in ED-mode
        # and pass it through verbatim otherwise.
        "ai_comment": None if ed_mode else summary.ai_comment,
        "entries": entries,
        "ed_mode": ed_mode,
    }
    return payload


def serialize_health_flags(profile: ProfileResponse | None) -> dict[str, Any]:
    """Build the ``/customer/health-flags`` payload from a profile.

    ``eating_disorder`` is the OR of both signals (see
    :func:`is_ed_mode_active`); ``pregnancy`` / ``breastfeeding`` come
    straight from the declared health flags. The ``ed_mode`` alias
    mirrors the field name the food endpoints expose so frontend
    consumers don't have to remember two names.
    """

    flags = (profile.health_flags or {}) if profile is not None else {}
    ed = is_ed_mode_active(profile)
    return {
        "eating_disorder": ed,
        "pregnancy": bool(flags.get("pregnancy")),
        "breastfeeding": bool(flags.get("breastfeeding")),
        "ed_mode": ed,
    }
