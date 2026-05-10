"""Phase 3.2B T03: per-class quota enforcement.

Design §10.3 caps:
- SERVICE: no day cap, 5/week
- CARE: 1/day, 2/week
- MARKETING: 0/day, 1 per 60 days

Все запросы фильтруют `sent_at__isnull=False` — blocked events не считаются.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from services_app.models import NudgeEvent


_DAY_CAPS: dict[str, int] = {
    "care": 1,
    # marketing: 60-day rolling cap supersedes day cap (handled separately below)
    # service: no day cap (key absent)
}

_WEEK_CAPS: dict[str, int] = {
    "service": 5,
    "care": 2,
    # marketing: special — uses MARKETING_60_DAY_CAP instead
}

_MARKETING_PERIOD_DAYS = 60
_MARKETING_PERIOD_CAP = 1


def _count_sent(bot_user, nudge_class: str, *, since: datetime, now: datetime) -> int:
    # Strict `gt` on `since` — events at exactly the window boundary fall outside
    # (e.g. care event 24h ago should not count toward 1/day cap).
    return NudgeEvent.objects.filter(
        bot_user=bot_user,
        nudge_class=nudge_class,
        sent_at__gt=since,
        sent_at__lte=now,
    ).count()


def is_capped(
    bot_user, *, nudge_class: str, now: datetime,
) -> tuple[bool, str | None]:
    """Returns (blocked, reason). Reason — telemetry slug."""
    # Day cap
    day_cap = _DAY_CAPS.get(nudge_class)
    if day_cap is not None:
        day_start = now - timedelta(hours=24)
        count = _count_sent(bot_user, nudge_class, since=day_start, now=now)
        if count >= day_cap:
            return True, f"cap_day_{nudge_class}"

    # Marketing 60-day rolling
    if nudge_class == "marketing":
        period_start = now - timedelta(days=_MARKETING_PERIOD_DAYS)
        count = _count_sent(bot_user, nudge_class, since=period_start, now=now)
        if count >= _MARKETING_PERIOD_CAP:
            return True, "cap_60d_marketing"
        return False, None

    # Week cap
    week_cap = _WEEK_CAPS.get(nudge_class)
    if week_cap is not None:
        week_start = now - timedelta(days=7)
        count = _count_sent(bot_user, nudge_class, since=week_start, now=now)
        if count >= week_cap:
            return True, f"cap_week_{nudge_class}"

    return False, None
