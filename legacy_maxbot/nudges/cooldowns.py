"""Phase 3.2B T04: per-kind cooldown engine.

Reads NUDGE_REGISTRY[kind]['cooldown_days']. Last sent NudgeEvent for kind.
mode=less_often mute → cooldown × 2 (Design §10.6).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from services_app.models import NudgeEvent, NudgeMute
from maxbot.nudges.registry import get_kind_meta


def is_in_cooldown(
    bot_user, *, kind: str, now: datetime,
) -> tuple[bool, str | None]:
    """Returns (blocked, reason). reason='cooldown_{days}d'."""
    meta = get_kind_meta(kind)
    if meta is None:
        return False, None

    cooldown_days = meta.get("cooldown_days", 0)
    if cooldown_days <= 0:
        return False, None

    # Apply «less often» multiplier from active mute
    active_mute = NudgeMute.objects.filter(
        bot_user=bot_user, kind=kind, mode="less_often",
    ).order_by("-created_at").first()
    if active_mute:
        # Honor expires_at if set
        if active_mute.expires_at is None or active_mute.expires_at > now:
            cooldown_days *= 2

    # Last sent
    last = NudgeEvent.objects.filter(
        bot_user=bot_user, kind=kind, sent_at__isnull=False,
    ).order_by("-sent_at").first()
    if last is None:
        return False, None

    if (now - last.sent_at) < timedelta(days=cooldown_days):
        return True, f"cooldown_{cooldown_days}d"
    return False, None
