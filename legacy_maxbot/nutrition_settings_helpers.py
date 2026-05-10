"""Phase 3.1 Part 2D.2: helpers для BotUser.nutrition_settings JSON.

Schema (per BotUser model docstring):
    {
        "daily_report_time": "18:00" | "21:00" | "23:00" | "off",
        "daily_report_enabled": bool,  # legacy, не используется в Part 2D.2 (off=disable)
        "water_reminders_enabled": bool,
        "last_water_dismissed_at": ISO timestamp | None,
        # ... другие keys из ранних phases
    }

Все функции — sync ORM. Async caller'ы оборачивают в `sync_to_async`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


def get_setting(bot_user, key: str, *, default: Any = None) -> Any:
    """Return value из nutrition_settings или default."""
    settings = bot_user.nutrition_settings or {}
    return settings.get(key, default)


def set_setting(bot_user, key: str, value: Any) -> None:
    """Set value в nutrition_settings + save (только это поле).

    Other keys (food_disclaimer_shown_at, etc.) preserved.
    """
    settings = dict(bot_user.nutrition_settings or {})
    settings[key] = value
    bot_user.nutrition_settings = settings
    bot_user.save(update_fields=["nutrition_settings"])


def is_quiet_hours_for_user(bot_user, *, now_utc: datetime | None = None) -> bool:
    """Phase 3.1 Part 2D.2: quiet hours 22:00–09:00 local time (Design §10.4).

    Args:
        bot_user: BotUser с .timezone (IANA string).
        now_utc: Override для тестов. Default = real now UTC.

    Returns:
        True если local hour ∈ [22, 23] OR [0, 8] (i.e. 22:00..08:59).
    """
    if now_utc is None:
        now_utc = datetime.now(ZoneInfo("UTC"))
    try:
        user_tz = ZoneInfo(bot_user.timezone or "Europe/Moscow")
    except Exception:  # noqa: BLE001
        user_tz = ZoneInfo("Europe/Moscow")
    local_hour = now_utc.astimezone(user_tz).hour
    return local_hour >= 22 or local_hour < 9


def calc_proportional_norm(
    norm_ml: int,
    *,
    current_local_hour: int,
    wakeup_hour: int = 9,
) -> int:
    """Phase 3.1 Part 2D.2 (Design §7.7): proportional norm by elapsed wakeup time.

    proportional = min(1.0, elapsed/16) × norm_ml

    Day stretches wakeup..wakeup+16 (default 09:00..01:00 next day). Hours
    before wakeup (00..wakeup-1) but в next-day band (wraparound, e.g. 0-1)
    treated as full day (capped at norm). Hours [wakeup..23] are normal.
    Hours strictly before wakeup в same day (e.g. 08:00 before 09:00 wakeup)
    treated as elapsed=0.

    Logic:
        - if current >= wakeup: elapsed = current - wakeup, can be 0..14
        - if current < wakeup AND current < 8: elapsed = (24 - wakeup) + current
          (wraparound — например 1:00 = 16h after 09:00 предыдущего)
        - else (current ∈ [wakeup-1..wakeup-1] but не wrap'ом): elapsed = 0
    """
    if current_local_hour >= wakeup_hour:
        elapsed = current_local_hour - wakeup_hour
    elif current_local_hour < wakeup_hour - 1:  # wraparound
        elapsed = (24 - wakeup_hour) + current_local_hour
    else:
        elapsed = 0
    factor = min(1.0, elapsed / 16.0)
    return int(round(norm_ml * factor))
