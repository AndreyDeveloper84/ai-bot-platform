"""Phase 3.2B T02: nudge registry — single source of truth for kind metadata.

Mapping kind → {class, priority, cooldown_days}.

Source: Design Doc v2 §10.2 (priorities) + §10.5 (cooldowns) + §10.1 (classes).
Hard-coded для скорости и safety против runtime-нерфа. Изменения требуют
деплоя — это feature, не bug (нудж-policy = engineering decision, не content).
"""
from __future__ import annotations

from django.db import models


class NudgeClass(models.TextChoices):
    SERVICE = "service", "Service"
    CARE = "care", "Care"
    MARKETING = "marketing", "Marketing"


# Design §10.2 + §10.5 — full table.
NUDGE_REGISTRY: dict[str, dict] = {
    "booking_continuation": {
        "class": NudgeClass.SERVICE, "priority": 100, "cooldown_days": 0,
    },
    "health_concern_high": {
        "class": NudgeClass.CARE, "priority": 90, "cooldown_days": 7,
    },
    "health_concern_medium": {
        "class": NudgeClass.CARE, "priority": 80, "cooldown_days": 14,
    },
    "after_service_care": {
        "class": NudgeClass.SERVICE, "priority": 70, "cooldown_days": 0,
    },
    "weekly_unlock": {
        "class": NudgeClass.CARE, "priority": 60, "cooldown_days": 0,  # one-time
    },
    "returning_success": {
        "class": NudgeClass.CARE, "priority": 50, "cooldown_days": 30,
    },
    "pattern_detected": {
        "class": NudgeClass.CARE, "priority": 40, "cooldown_days": 21,
    },
    "pattern_followup": {
        "class": NudgeClass.CARE, "priority": 35, "cooldown_days": 0,  # one-time
    },
    "health_concern_low": {
        "class": NudgeClass.CARE, "priority": 30, "cooldown_days": 30,
    },
    "cross_promo": {
        "class": NudgeClass.MARKETING, "priority": 20, "cooldown_days": 60,
    },
    "reengagement": {
        "class": NudgeClass.CARE, "priority": 10, "cooldown_days": 30,
    },
}


def get_kind_meta(kind: str) -> dict | None:
    """Lookup nudge metadata. Returns None для неизвестных kind (defensive)."""
    return NUDGE_REGISTRY.get(kind)
