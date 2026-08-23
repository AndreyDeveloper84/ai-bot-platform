"""Two beat tasks that write to people first (DRF-1285).

``send_daily_reports`` -- the nutrition day summary, at the hour the person
chose, in the person's own timezone. Beat cadence: hourly on the hour; each
tick keeps only the recipients whose chosen hour is the current *local* one.

``send_water_reminders`` -- a water nudge every four hours, suppressed
during quiet hours and unless intake is behind the *proportional* norm.

### What is deliberately not here

The legacy ``mysite`` bot shipped a nudge engine -- ``legacy_maxbot/
nudges/``, 481 lines across ten modules: a rule registry with priorities
and per-kind cooldowns, daily caps, race guards, mute handlers,
auto-disable after repeated ignores. The policy design is sound.

It is also dead. ``evaluate_nudge`` (``nudges/dispatcher.py:29``) has
**zero** callers anywhere in this repository -- not one production call
site, and not a test either. Porting it would move a decorative engine,
not a working one.

(The ticket brief also reported that ``PatternRule.detector_function``
points at a missing ``detectors`` module. Neither ``PatternRule`` nor
``detector_function`` exists in the vendored copy at all -- that symbol
lives in the mysite Django models, which are not in this repo. The claim
is therefore unverifiable from here, and unnecessary: "no callers" is
already the stronger finding.)

So three of its ideas are re-implemented inline, at the size the two tasks
actually need, and the rest is left behind:

* **quota** -- :data:`~apps.nutrition_proactive.prefs.MAX_WATER_REMINDERS_PER_DAY`
  and a once-per-local-day key for the report.
* **cooldown** -- the four-hour beat cadence *is* the cooldown; there is no
  second timer to drift out of sync with it.
* **auto-disable after repeated ignores** --
  :data:`~apps.nutrition_proactive.prefs.IGNORED_STREAK_LIMIT` consecutive
  reminders that changed the logged intake by nothing turn water reminders
  off for that person and say so.

### Two switches, both closed

* ``NUTRITION_PROACTIVE_ENABLED`` (default ``False``) -- the task returns
  immediately. A deploy alone changes nothing.
* ``NUTRITION_PROACTIVE_DRY_RUN`` (default ``True``) -- the task does the
  full selection, the full Ayla read and the full threshold arithmetic, logs
  exactly who it *would* have written to and why, and sends nothing.

A real message therefore needs two deliberate flips, in that order. That is
the ratchet the pilot deserves: everything downstream of these tasks lands
in a real person's messenger.

### Idempotency and failure

Send-state is written only after the outbound call returns. A send that
raises leaves the counters untouched, so the next tick retries -- bounded by
the daily quota for water, and by the one-hour match window for the report.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from celery import shared_task  # type: ignore[import-untyped]
from django.conf import settings
from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.channels.max.outbound import MaxAPIError, send_message
from apps.integrations.ayla import (
    NutritionAPIError,
    NutritionUnavailableError,
    ProfileResponse,
    SummaryResponse,
    WaterTodayResponse,
    external_user_id_for,
    get_nutrition_client,
)
from apps.nutrition_proactive import prefs, render, selection

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(getattr(settings, "NUTRITION_PROACTIVE_ENABLED", False))


def dry_run() -> bool:
    """True unless an operator has explicitly turned the safety off."""
    return bool(getattr(settings, "NUTRITION_PROACTIVE_DRY_RUN", True))


#: What one daily-report evaluation needs from Ayla. The profile is last
#: and nullable because it is the only optional part: without it the report
#: still renders (calories against the summary's own goal, macros without
#: targets) and simply makes no remark.
DailyPayload = tuple[SummaryResponse, WaterTodayResponse | None, ProfileResponse | None]


@dataclass
class Decision:
    """One evaluated recipient. ``send`` False always carries a ``reason``."""

    bot_user_id: Any
    external_user_id: str
    send: bool
    reason: str
    local_hour: int | None = None
    tz_source: str = ""
    text: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    #: Preference updates to persist after a successful send.
    pref_updates: dict[str, Any] = field(default_factory=dict)

    def as_log(self) -> dict[str, Any]:
        """PII-free projection for logs and the dry-run report.

        ``text`` is excluded on purpose: a rendered daily report carries
        somebody's calorie and macro numbers.
        """
        return {
            "bot_user_id": str(self.bot_user_id),
            "external_user_id": self.external_user_id,
            "send": self.send,
            "reason": self.reason,
            "local_hour": self.local_hour,
            "tz_source": self.tz_source,
            **self.detail,
        }


# -- daily report -----------------------------------------------------------


def plan_daily_reports(
    *,
    now_utc: datetime | None = None,
    fetch: Callable[[str], DailyPayload] | None = None,
) -> list[Decision]:
    """Evaluate every candidate for the daily report at ``now_utc``.

    Pure with respect to the outside world apart from ``fetch``, which is
    injectable so tests and the dry-run exercise the same arithmetic without
    a live Ayla.
    """
    now_utc = now_utc or dj_timezone.now()
    fetch = fetch or _fetch_daily
    decisions: list[Decision] = []

    for bot_user in selection.base_queryset():
        ext = external_user_id_for(bot_user)
        blocked = selection.check_common(bot_user)
        if blocked:
            decisions.append(Decision(bot_user.pk, ext, False, blocked))
            continue

        user_prefs = prefs.get_prefs(bot_user)
        hour = prefs.report_hour(user_prefs)
        if hour is None:
            decisions.append(Decision(bot_user.pk, ext, False, "report_off"))
            continue

        local, tz_source = prefs.local_now(bot_user, now_utc)

        def decide(reason: str, *, send: bool = False, **kwargs: Any) -> Decision:
            """Bind the row-invariant fields so no branch can forget one."""
            return Decision(
                bot_user_id=bot_user.pk,
                external_user_id=ext,
                send=send,
                reason=reason,
                local_hour=local.hour,
                tz_source=tz_source,
                **kwargs,
            )

        # Quiet hours are checked before the chosen hour, not after: a person
        # who picked 23:00 in the Mini App has picked an hour we will not
        # honour, and the honest outcome is silence plus a log line -- not a
        # 23:00 message because "they asked for it".
        if prefs.is_quiet_hour(local.hour):
            decisions.append(decide("quiet_hours"))
            continue
        if local.hour != hour:
            decisions.append(decide("not_report_hour", detail={"wanted_hour": hour}))
            continue
        today_iso = local.date().isoformat()
        if user_prefs.get("last_report_date") == today_iso:
            decisions.append(decide("already_sent_today"))
            continue

        try:
            summary, water, profile = fetch(ext)
        except (NutritionUnavailableError, NutritionAPIError) as exc:
            logger.warning("nutrition_proactive.report.fetch_failed ext=%s err=%s", ext, exc)
            decisions.append(decide("ayla_unavailable"))
            continue

        decisions.append(
            decide(
                "due",
                send=True,
                text=render.render_daily_report(summary, water, profile),
                pref_updates={"last_report_date": today_iso},
            )
        )

    return decisions


def _fetch_daily(ext: str) -> DailyPayload:
    """Summary is required; water and profile degrade to ``None``.

    Three calls once a day per recipient. The profile is fetched rather than
    cached because its norms are what every printed target and every remark
    is measured against -- a stale copy would put a number in a message that
    no longer matches what the person sees in the Mini App.
    """
    client = get_nutrition_client()

    async def _run() -> DailyPayload:
        summary = await client.daily_summary(external_user_id=ext, with_comment=True)
        try:
            water = await client.get_water_today(external_user_id=ext)
        except (NutritionUnavailableError, NutritionAPIError):
            water = None
        try:
            profile = await client.get_profile(external_user_id=ext)
        except (NutritionUnavailableError, NutritionAPIError):
            profile = None
        return summary, water, profile

    return asyncio.run(_run())


@shared_task(name="nutrition_proactive.send_daily_reports")
def send_daily_reports() -> dict[str, int]:
    """Hourly beat. No-op unless ``NUTRITION_PROACTIVE_ENABLED``."""
    return _run_task("report", plan_daily_reports)


# -- water reminders --------------------------------------------------------


def plan_water_reminders(
    *,
    now_utc: datetime | None = None,
    fetch: Callable[[str], WaterTodayResponse] | None = None,
) -> list[Decision]:
    """Evaluate every candidate for a water reminder at ``now_utc``."""
    now_utc = now_utc or dj_timezone.now()
    fetch = fetch or _fetch_water
    decisions: list[Decision] = []

    for bot_user in selection.base_queryset():
        ext = external_user_id_for(bot_user)
        blocked = selection.check_common(bot_user)
        if blocked:
            decisions.append(Decision(bot_user.pk, ext, False, blocked))
            continue

        user_prefs = prefs.get_prefs(bot_user)
        if not prefs.water_enabled(user_prefs):
            decisions.append(Decision(bot_user.pk, ext, False, "water_off"))
            continue

        local, tz_source = prefs.local_now(bot_user, now_utc)

        def decide(reason: str, *, send: bool = False, **kwargs: Any) -> Decision:
            """Bind the row-invariant fields so no branch can forget one."""
            return Decision(
                bot_user_id=bot_user.pk,
                external_user_id=ext,
                send=send,
                reason=reason,
                local_hour=local.hour,
                tz_source=tz_source,
                **kwargs,
            )

        if prefs.is_quiet_hour(local.hour):
            decisions.append(decide("quiet_hours"))
            continue

        counters = prefs.water_counters(user_prefs, local.date())
        if counters["ignored_streak"] >= prefs.IGNORED_STREAK_LIMIT:
            decisions.append(decide("auto_disabled"))
            continue
        if counters["sent"] >= prefs.MAX_WATER_REMINDERS_PER_DAY:
            decisions.append(decide("daily_quota"))
            continue

        try:
            water = fetch(ext)
        except (NutritionUnavailableError, NutritionAPIError) as exc:
            logger.warning("nutrition_proactive.water.fetch_failed ext=%s err=%s", ext, exc)
            decisions.append(decide("ayla_unavailable"))
            continue

        if not water.norm_ml:
            # No profile, no norm, no basis to say anyone is behind.
            decisions.append(decide("no_norm"))
            continue

        proportional = prefs.proportional_norm_ml(water.norm_ml, local_hour=local.hour)
        threshold = proportional * prefs.WATER_DEFICIT_RATIO
        detail = {
            "total_ml": water.total_ml,
            "norm_ml": water.norm_ml,
            "proportional_ml": proportional,
            "threshold_ml": round(threshold, 1),
            "sent_today": counters["sent"],
            "ignored_streak": counters["ignored_streak"],
        }

        # Did the previous reminder change anything? Compared before the
        # threshold test so a person who is drinking never accrues a streak
        # merely by being ahead of the norm.
        streak = counters["ignored_streak"]
        if counters["sent"] > 0:
            streak = 0 if water.total_ml > counters["last_total_ml"] else streak + 1
        if streak >= prefs.IGNORED_STREAK_LIMIT:
            decisions.append(
                decide(
                    "auto_disabled",
                    detail=detail,
                    pref_updates={
                        "water_reminders": False,
                        "water": {**counters, "ignored_streak": streak},
                    },
                )
            )
            continue

        if water.total_ml >= threshold:
            decisions.append(decide("on_track", detail=detail))
            continue

        decisions.append(
            decide(
                "behind_proportional_norm",
                send=True,
                text=render.render_water_reminder(water, proportional_ml=proportional),
                detail=detail,
                pref_updates={
                    "water": {
                        "date": local.date().isoformat(),
                        "sent": counters["sent"] + 1,
                        "last_total_ml": water.total_ml,
                        "ignored_streak": streak,
                    }
                },
            )
        )

    return decisions


def _fetch_water(ext: str) -> WaterTodayResponse:
    return asyncio.run(get_nutrition_client().get_water_today(external_user_id=ext))


@shared_task(name="nutrition_proactive.send_water_reminders")
def send_water_reminders() -> dict[str, int]:
    """Every four hours. No-op unless ``NUTRITION_PROACTIVE_ENABLED``."""
    return _run_task("water", plan_water_reminders)


# -- shared execution -------------------------------------------------------


def _run_task(kind: str, planner: Callable[..., list[Decision]]) -> dict[str, int]:
    if not enabled():
        logger.info("nutrition_proactive.%s.disabled", kind)
        return {"planned": 0, "sent": 0, "skipped": 0, "failed": 0, "dry_run": 1}

    decisions = planner()
    is_dry = dry_run()
    sent = failed = 0
    to_send = [d for d in decisions if d.send]
    skipped = len(decisions) - len(to_send)

    for decision in decisions:
        if not decision.send and decision.pref_updates:
            # Auto-disable is a state change even when nothing is sent.
            _persist(decision)

    for decision in to_send:
        if is_dry:
            logger.info("nutrition_proactive.%s.dry_run would_send=%s", kind, decision.as_log())
            continue
        try:
            _deliver(decision)
        except Exception as exc:  # noqa: BLE001 -- one bad row must not stop the batch
            logger.exception(
                "nutrition_proactive.%s.send_failed bot_user=%s err=%s",
                kind,
                decision.bot_user_id,
                type(exc).__name__,
            )
            failed += 1
            continue
        _persist(decision)
        _audit(kind, decision)
        sent += 1

    logger.info(
        "nutrition_proactive.%s.summary planned=%d would_send=%d sent=%d "
        "skipped=%d failed=%d dry_run=%s",
        kind,
        len(decisions),
        len(to_send),
        sent,
        skipped,
        failed,
        is_dry,
    )
    return {
        "planned": len(decisions),
        "would_send": len(to_send),
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "dry_run": int(is_dry),
    }


def _deliver(decision: Decision) -> None:
    from apps.identity.models import BotUser

    chat_id = (
        BotUser.all_tenants.filter(pk=decision.bot_user_id)
        .values_list("chat_id", flat=True)
        .first()
        or ""
    ).strip()
    if not chat_id:
        raise MaxAPIError(0, "chat_id vanished between planning and delivery")
    send_message(chat_id=chat_id, text=decision.text, attachments=None)


def _persist(decision: Decision) -> None:
    from apps.identity.models import BotUser

    bot_user = BotUser.all_tenants.filter(pk=decision.bot_user_id).first()
    if bot_user is None:
        return
    context = prefs.merge_prefs(bot_user, decision.pref_updates)
    BotUser.all_tenants.filter(pk=decision.bot_user_id).update(context=context)


def _audit(kind: str, decision: Decision) -> None:
    try:
        write_audit(
            action=f"nutrition_proactive.{kind}.sent",
            target="BotUser",
            target_id=decision.bot_user_id,
            payload=decision.as_log(),
        )
    except Exception:  # noqa: BLE001
        logger.exception("nutrition_proactive.%s.audit_failed", kind)
