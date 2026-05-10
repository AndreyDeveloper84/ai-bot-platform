"""Phase 3.2A T03: render «Учла важное» block from ProfileResponse.

Source of truth: Ayla returns `overrides_applied: list[str]` in profile.raw
(Design Doc §4.6, Ayla spec §1.2). Each item — already-formatted Russian
sentence. We just render with «• » bullet prefix.

Fallback for older Ayla без overrides_applied: derive 1 line from
ProfileResponse.goal_overridden_by slug.
"""
from __future__ import annotations


_GOAL_OVERRIDE_LABELS: dict[str, str] = {
    "pregnancy": "Беременность → цель «держать вес» + дополнительный белок и фолиевая",
    "breastfeeding": "Грудное вскармливание → +500 ккал, +20 г белка",
    "eating_disorder": "Бережный режим — без жёстких целей, поддерживаю заботливо",
    "bmi_floor": "Текущий вес уже ниже комфортной нормы — переключаю на «держать»",
}


def render_overrides_applied(profile) -> str | None:
    """Return «Учла важное:\\n• …» block or None if no overrides.

    Args:
        profile: ProfileResponse with .raw (dict) и .goal_overridden_by (str|None).
    """
    raw = getattr(profile, "raw", None) or {}
    items = raw.get("overrides_applied")
    lines: list[str] = []

    if isinstance(items, list) and items:
        for item in items:
            text = str(item).strip()
            if text:
                lines.append(f"• {text}")
    else:
        # Fallback: derive single line from goal_overridden_by slug
        slug = getattr(profile, "goal_overridden_by", None)
        if slug:
            label = _GOAL_OVERRIDE_LABELS.get(
                slug, "Учла особенности — советы будут аккуратнее"
            )
            lines.append(f"• {label}")

    if not lines:
        return None
    return "Учла важное:\n" + "\n".join(lines)
