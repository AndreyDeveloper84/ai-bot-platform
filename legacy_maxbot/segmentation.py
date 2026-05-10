"""DRF-292 (B-13) — Phase 3 50/50 A/B middleware.

Single rollout decision point. Caller passes a ``BotUser`` and gets back
True/False — should this user see Phase 3 surfaces (the 🍎 Дневник
питания button + everything behind it) on this turn.

Decision tree (top wins):

    1. ``settings.NUTRITION_ENABLED=True``     → True (full rollout, all users).
    2. ``bot_user.max_user_id`` ∈ ``PHASE3_INTERNAL_ACCOUNTS`` → True
       (internal cohort always opted in, regardless of A/B state — they
       are the test pilots, not the experimental sample).
    3. ``settings.PHASE3_AB_ENABLED=False``    → False (A/B not armed, no
       public exposure even with non-empty internal list).
    4. ``bot_user.id % 100 < 50``              → True for segment A,
       False for segment B (deterministic, stable across sessions; same
       bot_user.id always lands in the same bucket).

Why ``bot_user.id`` (Django PK) and not ``max_user_id``: PK assignment
is monotonic per registration order, evenly distributing across the
modulo 100 space. ``max_user_id`` comes from MAX and could be clustered
on a small range, biasing the split. The unit tests guard the ±10%
distribution invariant on a 1000-user sample so a future regression
breaking this gets caught immediately.

The ``bot_user=None`` path returns False for every branch — defensive
default for code paths that don't have a user object yet (e.g. early
``/start`` before BotUser persisted). Keeps the gate fail-closed so
nobody accidentally sees the feature without an identifiable user.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover
    from services_app.models import BotUser


__all__ = ["in_phase3_segment"]


def in_phase3_segment(bot_user: "BotUser | None") -> bool:
    """Should this user see Phase 3 features on this turn?

    See module docstring for the full decision tree. Returns False when
    ``bot_user`` is None — fail-closed so callers without a resolved
    user (e.g. anonymous webhooks) never accidentally expose the gate.
    """
    if getattr(settings, "NUTRITION_ENABLED", False):
        return True

    if bot_user is None:
        return False

    internal = getattr(settings, "PHASE3_INTERNAL_ACCOUNTS", []) or []
    if bot_user.max_user_id in internal:
        return True

    if not getattr(settings, "PHASE3_AB_ENABLED", False):
        return False

    # Stable 50/50 split keyed on Django PK. Segment A sees the feature.
    return bot_user.id % 100 < 50
