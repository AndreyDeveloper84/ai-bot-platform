"""Registry wrapper for the off switch (DRF-1285).

The phrases and the effect live in :mod:`apps.nutrition_proactive.optout`,
which imports nothing from ``apps.skills`` precisely so the global-surface
caller in ``apps/channels/max/handler.py`` can import it at module scope
without touching the registry's documented module-load cycle. This file is
the thin half: it exists to put the same behaviour on the per-tenant
surface, through the registry.

Both surfaces call one implementation. They must never drift -- an
off-switch that works on one bot and not the other is worse than none,
because the person has already been told it worked.
"""

from __future__ import annotations

from typing import ClassVar

from apps.nutrition_proactive.optout import (
    CONFIRMATION,
    OPT_OUT_PHRASES,
    apply_opt_out,
    matches_opt_out,
    normalise,
    try_handle_opt_out,
)
from apps.skills.base import SkillContext, SkillResult
from apps.skills.registry import register

__all__ = [
    "CONFIRMATION",
    "OPT_OUT_PHRASES",
    "ProactiveOptOutSkill",
    "apply_opt_out",
    "matches_opt_out",
    "normalise",
    "try_handle_opt_out",
]


@register
class ProactiveOptOutSkill:
    """Stop every bot-initiated message, from one message."""

    name: ClassVar[str] = "proactive_opt_out"

    def matches(self, context: SkillContext) -> bool:
        return matches_opt_out(context.message_text or "")

    def handle(self, context: SkillContext) -> SkillResult:
        return SkillResult(
            reply_text=apply_opt_out(context.bot_user),
            action_type="proactive_opt_out",
            meta={"reply_kind": "proactive_opt_out"},
        )
