# mysite/maxbot/evening_inline.py
"""Phase 3.1 Part 2D.3 T04: evening inline daily report trigger logic.

Pure function — no I/O. Encapsulates the 5 gates from Design §6.1:
  1. local_hour ∈ [18, 22)  (after-dinner band, NOT in quiet hours)
  2. summary.entries count ≥ 3 (meaningful day)
  3. daily_report_time != "off"  (user opted out of pushes)
  4. evening_inline_shown_at != today_local_date_iso (idempotency)
"""
from __future__ import annotations

from datetime import datetime


def should_trigger_evening_inline(
    bot_user,
    *,
    summary,
    now_local: datetime,
    today_local_date: str,
) -> bool:
    """Return True iff all 4 gates hold; False otherwise.

    Args:
        bot_user: BotUser (uses .nutrition_settings only).
        summary: SummaryResponse — uses .entries length only.
        now_local: TZ-aware datetime в local TZ юзера (caller computes).
        today_local_date: ISO date string в local TZ юзера, e.g. "2026-05-05".
    """
    hour = now_local.hour
    if hour < 18 or hour >= 22:
        return False

    entries = getattr(summary, "entries", None) or []
    if len(entries) < 3:
        return False

    settings = bot_user.nutrition_settings or {}
    if settings.get("daily_report_time") == "off":
        return False

    if settings.get("evening_inline_shown_at") == today_local_date:
        return False

    return True
