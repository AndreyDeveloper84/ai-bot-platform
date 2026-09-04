"""Per-user proactive-nutrition preferences + the time arithmetic (DRF-1285).

Everything a bot-initiated nutrition message needs to decide *whether* and
*when* to fire lives here, so both beat tasks, the opt-out skill and the
dry-run command read one implementation instead of three.

### Where the preferences live

``BotUser.context["nutrition_proactive"]`` -- a JSON sub-dict, not new
columns. Two reasons:

1. ``BotUser.context`` is already the established home for exactly this
   shape of per-user scheduler state: ``apps.bookings.followups`` keeps its
   once-a-day idempotency key there (``last_followup_sent_at``).
2. A migration is not free here. ``makemigrations`` pins new migrations onto
   the *leaf* of ``identity``, and tests that roll ``identity`` back through
   ``MigrationExecutor`` drop every dependent table without restoring it
   (the DRF-1277 class of failure). Two beat tasks do not justify that risk.

Schema (every key optional; a missing key means the conservative default)::

    {
      "daily_report_time": "off" | "HH:MM",   # default "off" -> nothing sent
      "water_reminders": false,               # default False -> nothing sent
      "last_report_date": "YYYY-MM-DD",       # local-day idempotency key
      "water": {
          "date": "YYYY-MM-DD",               # local day these counters cover
          "sent": 0,                          # reminders sent that day
          "last_total_ml": 0,                 # intake observed at last send
          "ignored_streak": 0                 # consecutive unheeded reminders
      },
      "opted_out_at": "<iso8601>",            # set by the opt-out skill
      "outbox": [                             # shared send journal (DRF-1468)
          {"surface": "report", "sent_at": "<iso8601 utc>"},
      ],                                      # pruned by age, capped in length
    }

### Both defaults are OFF

A bot that writes first needs a reason to write, and "the code shipped" is
not one. ``daily_report_time`` defaults to ``"off"`` and ``water_reminders``
to ``False``, so a deploy plus a flag flip still sends nobody anything until
a person asks for it. This is deliberately *stricter* than the legacy mysite
bot, which defaulted the daily report to 21:00 for every onboarded user.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

#: Namespace inside ``BotUser.context``.
CONTEXT_KEY = "nutrition_proactive"

#: The value of ``daily_report_time`` that means "send nothing".
REPORT_OFF = "off"

#: Quiet hours, local to the recipient: no proactive nutrition message is
#: composed when the local hour is >= START or < END. 22:00-08:59 inclusive,
#: matching the legacy mysite window (``nutrition_settings_helpers.py:38``).
QUIET_START_HOUR = 22
QUIET_END_HOUR = 9

#: The proportional-norm day: intake is expected to spread over 16 hours
#: starting at this local hour. See :func:`proportional_norm_ml`.
WAKEUP_HOUR = 9
DAY_SPAN_HOURS = 16

#: Remind only when intake is under this share of the *proportional* norm.
WATER_DEFICIT_RATIO = 0.5

#: Hard ceiling on water reminders per person per local day.
MAX_WATER_REMINDERS_PER_DAY = 3

#: Consecutive reminders that moved the intake needle by nothing before the
#: feature turns itself off for that person. The one idea worth keeping from
#: the legacy ``nudges`` package, which never ran (see tasks.py).
IGNORED_STREAK_LIMIT = 3

# -- the shared anti-nag mechanism (DRF-1468) --------------------------------

#: Key of the outbound journal inside the prefs sub-dict: a bounded list of
#: ``{"surface": ..., "sent_at": <iso utc>}``, oldest first. One journal for
#: every proactive surface, so the weekly ceiling and the ignore streak read
#: what was actually sent rather than per-feature counters that can drift.
OUTBOX_KEY = "outbox"

#: Hard length cap. The busiest permissible week is 28 sends (1 report +
#: 3 water per day); 64 covers it twice over. The age prune below is what
#: keeps the list small in practice.
OUTBOX_CAP = 64

#: Entries older than this many days are dropped on append. The weekly
#: ceiling looks 7 days back and the ignore streak only at the trailing
#: edge, so a fortnight keeps both fully informed.
OUTBOX_KEEP_DAYS = 14

#: Sliding 7-day ceiling on ALL proactive nutrition outbound, summed across
#: surfaces: about two unsolicited touches per day on average. This is the
#: anti-nag budget (policy R2/R6) -- per-surface quotas may be lower, never
#: higher in effect.
MAX_WEEKLY_OUTBOUND_TOTAL = 14

#: Per-surface weekly ceilings inside the total. The two live surfaces are
#: pinned at what their per-day quotas already allow (report 1/day, water
#: 3/day), so for them the ceiling documents the budget instead of changing
#: it. Any surface not listed -- the future hint and the weekly report --
#: gets the anti-nag default: one touch a week.
WEEKLY_SURFACE_CAPS = {"report": 7, "water": 21}
DEFAULT_WEEKLY_SURFACE_CAP = 1

#: Consecutive sends on one surface that no user message followed before
#: that surface pauses itself -- silently (policy R2: never «ты не ответила»).
SURFACE_IGNORE_LIMIT = 2

#: What ``BotUser.timezone`` holds when nobody ever set it -- the column
#: default from ``apps.identity.models.BotUser``. Not "empty", which is why a
#: naive "is it filled?" check on the pilot reports 100% and means 0%.
UNSET_TZ_SENTINEL = "Europe/Moscow"

FALLBACK_TZ = "Europe/Moscow"

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# -- preference access ------------------------------------------------------


def get_prefs(bot_user: Any) -> dict[str, Any]:
    """Return the ``nutrition_proactive`` sub-dict (a copy, never None)."""
    context = bot_user.context if isinstance(bot_user.context, dict) else {}
    raw = context.get(CONTEXT_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def merge_prefs(bot_user: Any, updates: dict[str, Any]) -> dict[str, Any]:
    """Return the full new ``context`` dict with ``updates`` merged in.

    Pure -- the caller decides how to persist. The tasks use a manager-level
    ``.update()`` so a cross-tenant beat is not refused by the tenant-scoped
    default manager.
    """
    context = dict(bot_user.context) if isinstance(bot_user.context, dict) else {}
    prefs = dict(context.get(CONTEXT_KEY) or {})
    prefs.update(updates)
    context[CONTEXT_KEY] = prefs
    return context


def report_time(prefs: dict[str, Any]) -> str:
    """Normalised ``daily_report_time``. Anything unparseable reads as off.

    A corrupt value must not be repaired into a *sending* state: the legacy
    task fell back to 21:00 on a malformed setting, which turns a data bug
    into an unrequested message.
    """
    raw = prefs.get("daily_report_time", REPORT_OFF)
    if not isinstance(raw, str):
        return REPORT_OFF
    raw = raw.strip()
    if raw == REPORT_OFF or not _HHMM_RE.match(raw):
        return REPORT_OFF
    return raw


def report_hour(prefs: dict[str, Any]) -> int | None:
    """The local hour the daily report is due, or None when off."""
    value = report_time(prefs)
    if value == REPORT_OFF:
        return None
    return int(value.split(":", 1)[0])


def water_enabled(prefs: dict[str, Any]) -> bool:
    return prefs.get("water_reminders") is True


# -- timezone ---------------------------------------------------------------


def resolve_timezone(bot_user: Any) -> tuple[ZoneInfo, str]:
    """Return ``(tzinfo, source)`` for a recipient.

    Precedence:

    1. ``BotUser.timezone`` when it is set to something other than the column
       default -- the only case where we know a human (or an import) actually
       chose it.
    2. ``Tenant.timezone`` -- the salon the person books with. For a
       single-city pilot this beats a global constant, and it is at least
       *someone's* deliberate configuration.
    3. ``Europe/Moscow``.

    ``source`` is returned rather than swallowed so the dry-run can show the
    operator how many recipients ride on an unverified guess. On the pilot as
    of 2026-08-23 that is all of them: 14/14 BotUsers carry the untouched
    column default.
    """
    raw = (getattr(bot_user, "timezone", "") or "").strip()
    if raw and raw != UNSET_TZ_SENTINEL:
        tz = _safe_zoneinfo(raw)
        if tz is not None:
            return tz, "botuser"

    tenant = getattr(bot_user, "tenant", None)
    tenant_tz = (getattr(tenant, "timezone", "") or "").strip()
    if tenant_tz:
        tz = _safe_zoneinfo(tenant_tz)
        if tz is not None:
            return tz, "tenant"

    return ZoneInfo(FALLBACK_TZ), "fallback"


def _safe_zoneinfo(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("nutrition_proactive.bad_timezone value=%s", name[:64])
        return None


def local_now(bot_user: Any, now_utc: datetime) -> tuple[datetime, str]:
    """``now_utc`` rendered in the recipient's timezone, plus the tz source."""
    tz, source = resolve_timezone(bot_user)
    return now_utc.astimezone(tz), source


# -- quiet hours ------------------------------------------------------------


def is_quiet_hour(local_hour: int) -> bool:
    """True inside 22:00-08:59 local.

    Expressed on the hour, not the timestamp, because every caller has
    already localised and both tasks fire on the hour. Keeping it a pure
    int -> bool makes the boundary cases (21 / 22 / 8 / 9) trivially testable.
    """
    return local_hour >= QUIET_START_HOUR or local_hour < QUIET_END_HOUR


# -- proportional norm ------------------------------------------------------


def proportional_norm_ml(norm_ml: int, *, local_hour: int) -> int:
    """``min(1, elapsed/16) * norm_ml`` -- the norm owed *by now*.

    Ported from ``legacy_maxbot/nutrition_settings_helpers.py:58``. Elapsed
    hours are counted from :data:`WAKEUP_HOUR` over a
    :data:`DAY_SPAN_HOURS`-hour day (09:00 -> 01:00 next day).

    This is the whole difference between a reminder and a reproach. At noon
    three hours have elapsed, so the expectation is ``3/16 = 19%`` of the
    day's water, and the reminder fires under half of *that* -- not under
    half of the full daily norm. Drop the proportionality and the bot tells
    everyone they are behind, all day, every day, and they turn it off.

    Hours before the wake-up hour on the same day yield ``elapsed = 0`` (the
    norm owed is zero, so no reminder can fire); the small-hours wraparound
    branch is kept for parity with the legacy helper even though quiet hours
    make it unreachable from the water task.
    """
    if local_hour >= WAKEUP_HOUR:
        elapsed = local_hour - WAKEUP_HOUR
    elif local_hour < WAKEUP_HOUR - 1:
        elapsed = (24 - WAKEUP_HOUR) + local_hour
    else:
        elapsed = 0
    factor = min(1.0, elapsed / float(DAY_SPAN_HOURS))
    return int(round(norm_ml * factor))


def water_threshold_ml(norm_ml: int, *, local_hour: int) -> float:
    """The intake below which a water reminder is warranted."""
    return proportional_norm_ml(norm_ml, local_hour=local_hour) * WATER_DEFICIT_RATIO


# -- per-day water counters -------------------------------------------------


def water_counters(prefs: dict[str, Any], today_local: date) -> dict[str, Any]:
    """Today's water counters, reset when the stored local day rolled over.

    ``ignored_streak`` deliberately survives the day roll: someone who
    ignored three reminders across two days ignored three reminders.
    """
    raw = prefs.get("water")
    streak = _int(raw.get("ignored_streak")) if isinstance(raw, dict) else 0
    if not isinstance(raw, dict) or raw.get("date") != today_local.isoformat():
        return {
            "date": today_local.isoformat(),
            "sent": 0,
            "last_total_ml": 0,
            "ignored_streak": streak,
        }
    return {
        "date": raw.get("date"),
        "sent": _int(raw.get("sent")),
        "last_total_ml": _int(raw.get("last_total_ml")),
        "ignored_streak": streak,
    }


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


# -- outbound journal (DRF-1468) ----------------------------------------------


def outbox_entries(prefs: dict[str, Any]) -> list[dict[str, Any]]:
    """The journaled sends, oldest first. A corrupt value reads as empty."""
    raw = prefs.get(OUTBOX_KEY)
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, dict)]


def append_outbox(
    prefs: dict[str, Any],
    *,
    surface: str,
    sent_at: datetime,
) -> dict[str, Any]:
    """Return a copy of ``prefs`` with one send journaled.

    Prunes entries older than :data:`OUTBOX_KEEP_DAYS` and caps the list at
    :data:`OUTBOX_CAP`, so the journal stays bounded no matter how long the
    feature runs. Timestamps compare as ISO strings -- every writer here
    stamps aware-UTC ``datetime.isoformat()``, which sorts chronologically.
    """
    cutoff = (sent_at - timedelta(days=OUTBOX_KEEP_DAYS)).isoformat()
    entries = [e for e in outbox_entries(prefs) if str(e.get("sent_at", "")) >= cutoff]
    entries.append({"surface": surface, "sent_at": sent_at.isoformat()})
    updated = dict(prefs)
    updated[OUTBOX_KEY] = entries[-OUTBOX_CAP:]
    return updated


def weekly_sent_count(
    prefs: dict[str, Any],
    *,
    now_utc: datetime,
    surface: str | None = None,
) -> int:
    """Sends journaled in the sliding 7 days before ``now_utc``."""
    cutoff = (now_utc - timedelta(days=7)).isoformat()
    return sum(
        1
        for entry in outbox_entries(prefs)
        if str(entry.get("sent_at", "")) >= cutoff
        and (surface is None or entry.get("surface") == surface)
    )


def weekly_cap_for(surface: str) -> int:
    """The per-surface weekly ceiling; unlisted surfaces get the default."""
    return WEEKLY_SURFACE_CAPS.get(surface, DEFAULT_WEEKLY_SURFACE_CAP)


def weekly_cap_reason(
    prefs: dict[str, Any],
    *,
    surface: str,
    now_utc: datetime,
) -> str | None:
    """The weekly-ceiling block reason for the next send, or None.

    The surface's own ceiling is asked first (the more specific answer),
    then the cross-surface total. Both reasons are distinct slugs so a dry
    run can tell "this surface spent its budget" from "all surfaces together
    did".
    """
    if weekly_sent_count(prefs, now_utc=now_utc, surface=surface) >= weekly_cap_for(surface):
        return "weekly_cap_surface"
    if weekly_sent_count(prefs, now_utc=now_utc) >= MAX_WEEKLY_OUTBOUND_TOTAL:
        return "weekly_cap_total"
    return None
