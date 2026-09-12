"""The coach_hint planner: who gets a proactive dietologist hint (DRF-1464, T5).

Same shape as :func:`apps.nutrition_proactive.tasks.plan_daily_reports` —
a pure evaluation over the candidate queryset with every outside read
injected, so the beat task, the dry-run command and the tests exercise
one arithmetic. What is said is :mod:`apps.nutrition_coach.copy`; when a
pattern exists is :mod:`apps.nutrition_coach.triggers`; this file is only
the gate ladder that decides whether the two may meet.

### The ladder, in order

Order is load-bearing: cheap and absolute gates first, fetches last, and
no gate behind a fetch it makes pointless.

1. **coach flags** — ``NUTRITION_COACH_ENABLED`` closed: no candidates
   at all. (Dry-run is the task's question, not the planner's.)
2. **the shared gate** — :func:`selection.check_common`: opt-out veto,
   erasure, 152-ФЗ baseline, ``chat_id``, food-scanner consent.
3. **HEALTH + PERSONAL_DATA** via ``has_global_consent`` — the diary is
   special-category data (152-ФЗ ст. 10); fail-closed: a consent read
   that raises reads as «нет основания».
4. **subscription pref** — ``coach_hints`` (default True; owner decision
   Q-09: the basis is the goal itself). The «Не присылать» button flips
   it through the generic DRF-1468 path, so it is honoured before a
   single Ayla call is spent.
5. **sensitive perimeter** — ``render.remarks_suppressed``: pregnancy /
   breastfeeding / eating disorder / bmi_floor gate the WHOLE surface,
   not just the remark (owner decision). An unreadable profile reads as
   «не знаем» — and «не знаем» is silence.
6. **explicit goal** — R7: no goal, no hint, however perfect the week.
7. **quiet hours** — recipient-local, same window as every surface.
8. **weekly budget** — ``prefs.weekly_cap_reason(surface="coach_hint")``;
   unlisted surfaces get the anti-nag default of one touch a week.
9. **ignore streak** — two unanswered hints pause the surface silently
   (pref flip, never a message).
10. **trigger** — one of the two (goal × pattern) detectors.
11. **outbound guard** — ``vet_outbound``; a hit is silence, no journal.

No frequency arithmetic of its own: every cadence question is delegated
to DRF-1468 (``prefs`` / ``antinag``). The architecture test
(``apps/nutrition_coach/tests/test_no_frequency_counters.py``) keeps it
that way.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from django.utils import timezone as dj_timezone

from apps.integrations.ayla import (
    NutritionAPIError,
    NutritionUnavailableError,
    ProfileResponse,
    external_user_id_for,
    get_nutrition_client,
)
from apps.nutrition_coach import copy as coach_copy
from apps.nutrition_coach import flags as coach_flags
from apps.nutrition_coach import goals, history, triggers
from apps.nutrition_coach.goals import Goal
from apps.nutrition_coach.history import WeekPicture
from apps.nutrition_proactive import antinag, prefs, render, selection
from apps.nutrition_proactive.tasks import Decision, vet_outbound
from apps.notifications.proactive import marketing_blocker

logger = logging.getLogger(__name__)

#: The surface name everywhere DRF-1468 asks for one: the outbox journal,
#: the weekly cap, the ignore streak, the stop button's callback payload.
SURFACE = "coach_hint"

#: Every slug this planner can emit. The shared gate's vocabulary is
#: included whole (``selection.BLOCK_REASONS``); the rest is this
#: surface's own ladder. Enumerated so the dry run and the tests assert
#: against a stable vocabulary instead of scattered literals.
BLOCK_REASONS = (
    *selection.BLOCK_REASONS,
    "no_health_consent",
    # DRF-1731: a coach hint is PROMO — a tip nobody asked for that
    # morning — so it also needs the advertising consent (38-ФЗ ст. 18).
    "no_marketing_consent",
    "hints_off",
    "sensitive_perimeter",
    "no_goal",
    "quiet_hours",
    "weekly_cap_surface",
    "weekly_cap_total",
    "surface_auto_paused",
    "no_trigger",
    "outbound_safety_hit",
    "due",
)


def plan_coach_hints(
    *,
    now_utc: datetime | None = None,
    fetch_goal: Callable[[Any], Goal | None] | None = None,
    fetch_history: Callable[[Any], WeekPicture] | None = None,
    fetch_profile: Callable[[Any], ProfileResponse | None] | None = None,
) -> list[Decision]:
    """Evaluate every candidate for a coach hint at ``now_utc``.

    Pure apart from the three injected reads — same contract as
    :func:`apps.nutrition_proactive.tasks.plan_daily_reports`.
    """
    if not coach_flags.enabled():
        # The whole surface stays silent; there are no per-user decisions
        # to make, so there is nothing to log per row either.
        return []

    now_utc = now_utc or dj_timezone.now()
    fetch_goal = fetch_goal or goals.active_goal
    fetch_history = fetch_history or history.week_picture
    fetch_profile = fetch_profile or _fetch_profile
    decisions: list[Decision] = []

    for bot_user in selection.base_queryset():
        ext = external_user_id_for(bot_user)
        blocked = selection.check_common(bot_user)
        if blocked:
            decisions.append(Decision(bot_user.pk, ext, False, blocked))
            continue

        if not _health_basis(bot_user):
            decisions.append(Decision(bot_user.pk, ext, False, "no_health_consent"))
            continue

        # DRF-1731: PROMO class (apps.notifications.proactive.PROACTIVE_SENDERS).
        # Asked after the health basis so the reason names the FIRST
        # missing ground in legal order: baseline → special category →
        # advertising. By record: a withdrawn toggle stops the hint the
        # same tick.
        marketing_blocked = marketing_blocker(bot_user)
        if marketing_blocked:
            decisions.append(Decision(bot_user.pk, ext, False, marketing_blocked))
            continue

        user_prefs = prefs.get_prefs(bot_user)
        if user_prefs.get("coach_hints", True) is False:
            decisions.append(Decision(bot_user.pk, ext, False, "hints_off"))
            continue

        tz, tz_source = prefs.resolve_timezone(bot_user)
        local = now_utc.astimezone(tz)

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

        if render.remarks_suppressed(fetch_profile(bot_user)):
            # The sensitive perimeter gates the WHOLE surface; an
            # unreadable profile is «не знаем», and «не знаем» is silence.
            decisions.append(decide("sensitive_perimeter"))
            continue

        goal = fetch_goal(bot_user)
        if goal is None:
            decisions.append(decide("no_goal"))
            continue

        if prefs.is_quiet_hour(local.hour):
            decisions.append(decide("quiet_hours"))
            continue

        capped = prefs.weekly_cap_reason(user_prefs, surface=SURFACE, now_utc=now_utc)
        if capped:
            decisions.append(decide(capped))
            continue

        streak = antinag.surface_ignored_streak(bot_user, user_prefs, surface=SURFACE)
        if streak >= prefs.SURFACE_IGNORE_LIMIT:
            # Silent pause: pref off, no message (policy R2/R6). State
            # change, persisted even when nothing is sent.
            decisions.append(
                decide(
                    "surface_auto_paused",
                    detail={"ignored_streak": streak},
                    pref_updates={"coach_hints": False},
                )
            )
            continue

        week = fetch_history(bot_user)
        trigger = triggers.any_trigger(goal.key, week, tz=tz)
        if trigger is None:
            decisions.append(decide("no_trigger", detail={"week_status": week.status.value}))
            continue

        first_ever = not any(
            entry.get("surface") == SURFACE and entry.get("solicited") is not True
            for entry in prefs.outbox_entries(user_prefs)
        )
        text, blocked_by = vet_outbound(coach_copy.render_hint(trigger.kind, first_ever=first_ever))
        if blocked_by:
            # Silence, and nothing journaled: the next tick evaluates
            # this person fresh rather than behind a spent budget.
            decisions.append(decide(blocked_by))
            continue

        decisions.append(
            decide(
                "due",
                send=True,
                text=text,
                detail={
                    "trigger": trigger.kind,
                    "trigger_days": trigger.days,
                    "first_ever": first_ever,
                },
            )
        )

    return decisions


def _health_basis(bot_user: Any) -> bool:
    """PERSONAL_DATA **and** HEALTH, proven by records — fail-closed.

    Same two-key standard :func:`apps.orchestrator.food_history.
    read_consent_open` applies to READING the diary: a hint that reasons
    about the diary is written on the same basis the diary is read on.
    ``has_global_consent`` because the beat runs tenant-less.
    """
    try:
        from apps.consent.models import ConsentRecord
        from apps.consent.services import has_global_consent

        return has_global_consent(
            bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value
        ) and has_global_consent(bot_user, ConsentRecord.ConsentType.HEALTH.value)
    except Exception:  # noqa: BLE001 — fail-closed: no proven basis, no hint
        logger.exception("nutrition_proactive.coach.consent_check_failed")
        return False


def _fetch_profile(bot_user: Any) -> ProfileResponse | None:
    """The anketa profile, best-effort. ``None`` on every failure.

    The sensitive perimeter lives in this response, so «could not read»
    must be distinguishable from «read, and clean» — ``None`` it is, and
    :func:`render.remarks_suppressed` already reads ``None`` as
    suppressed. Fetched per tick rather than cached: the override flags
    are exactly the values a stale copy must never keep saying «clean»
    about.
    """
    try:
        ext = external_user_id_for(bot_user)
        client = get_nutrition_client()
    except Exception as exc:  # noqa: BLE001 — unconfigured env is not an error
        logger.debug("nutrition_proactive.coach.disabled: %s", exc)
        return None
    try:
        return asyncio.run(client.get_profile(external_user_id=ext))
    except (NutritionUnavailableError, NutritionAPIError) as exc:
        logger.info("nutrition_proactive.coach.profile_unavailable reason=%s", exc)
        return None
    except Exception:  # noqa: BLE001 — never break the tick; reads as «не знаем»
        logger.exception("nutrition_proactive.coach.profile_fetch_failed")
        return None


__all__ = ["BLOCK_REASONS", "SURFACE", "plan_coach_hints"]
