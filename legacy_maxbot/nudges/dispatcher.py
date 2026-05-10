"""Phase 3.2B T08: dispatcher pipeline.

Composes all 5 predicates in order: mute → quiet → caps → cooldown → race.
First blocker wins; reason returned for telemetry. Pure sync — caller wraps
in sync_to_async if needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from maxbot.nudges.caps import is_capped
from maxbot.nudges.cooldowns import is_in_cooldown
from maxbot.nudges.mute import is_muted
from maxbot.nudges.race_guards import should_skip_due_to_recent_activity
from maxbot.nudges.registry import get_kind_meta
from maxbot.nutrition_settings_helpers import is_quiet_hours_for_user


@dataclass(frozen=True)
class Decision:
    send: bool
    kind: str = ""
    nudge_class: str = ""
    priority: int = 0
    blocked_reason: str | None = None


def evaluate_nudge(bot_user, *, kind: str, now: datetime) -> Decision:
    """Pipeline: mute → quiet → caps → cooldown → race. First block wins."""
    meta = get_kind_meta(kind)
    if meta is None:
        return Decision(send=False, kind=kind, blocked_reason="unknown_kind")
    nudge_class = str(meta["class"])
    priority = int(meta["priority"])

    # 1. Mute
    blocked, reason = is_muted(
        bot_user, kind=kind, nudge_class=nudge_class, now=now,
    )
    if blocked:
        return Decision(
            send=False, kind=kind, nudge_class=nudge_class,
            priority=priority, blocked_reason=reason,
        )

    # 2. Quiet hours
    if is_quiet_hours_for_user(bot_user, now_utc=now):
        return Decision(
            send=False, kind=kind, nudge_class=nudge_class,
            priority=priority, blocked_reason="quiet_hours",
        )

    # 3. Caps
    blocked, reason = is_capped(bot_user, nudge_class=nudge_class, now=now)
    if blocked:
        return Decision(
            send=False, kind=kind, nudge_class=nudge_class,
            priority=priority, blocked_reason=reason,
        )

    # 4. Cooldown
    blocked, reason = is_in_cooldown(bot_user, kind=kind, now=now)
    if blocked:
        return Decision(
            send=False, kind=kind, nudge_class=nudge_class,
            priority=priority, blocked_reason=reason,
        )

    # 5. Race-condition (recent user activity)
    blocked, reason = should_skip_due_to_recent_activity(bot_user, now=now)
    if blocked:
        return Decision(
            send=False, kind=kind, nudge_class=nudge_class,
            priority=priority, blocked_reason=reason,
        )

    return Decision(
        send=True, kind=kind, nudge_class=nudge_class, priority=priority,
    )
