"""Normalized orchestration boundary — `orchestrate_turn` (Migration Plan
Step 5 prep / OR-BOT-1..6).

ONE seam between the live ingress/guards and the live post-processing/
delivery. The channel handlers keep owning everything around the brain
(OR-BOT-1): webhook/dedup/idempotency, bot+conversation resolution,
safety gate, operator mute, callbacks, media, consent gates, memory
commands, persistence, keyboards, delivery. This module owns ONLY the
normalized call into the current production brains:

- ``surface="per_tenant"`` → the skill-registry dispatch (MAX + Telegram
  share the same brain contract);
- ``surface="global"``    → the concierge turn (nationwide tenant-less
  pilot bot, ``tenant=None`` by design — OR-BOT-3).

The seam itself has NO side effects: it never persists messages, never
sends outbound, never mutates booking/consent/MemoryEntry, never writes
metrics/audit. It does NOT call ``apps.orchestrator.pipeline.turn``
(OR-BOT-4: that pipeline bundles its own tenant/persist/delivery/audit
side effects). No new behaviour is introduced and no feature flag gates
it — the mapping is 1:1 with the pre-seam direct calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

Surface = Literal["per_tenant", "global"]

SURFACE_PER_TENANT: Surface = "per_tenant"
SURFACE_GLOBAL: Surface = "global"


@dataclass(frozen=True)
class TurnContext:
    """Normalized brain input. Handles only — no ORM graph traversal here.

    ``tenant`` is informational (None is the VALID global-pilot value —
    OR-BOT-3); the seam never resolves, fabricates, or looks up a tenant.
    Consent state is not carried: consent guards run BEFORE the seam and
    neither brain needs them inside.
    """

    surface: Surface
    conversation: Any  # ORM handle (global: sentinel-scoped conversation)
    bot_user: Any  # ORM handle (global: sentinel BotUser)
    text: str
    channel: str = ""
    trace_id: str = ""
    tenant: Any = None  # tenant_or_none — None for the global pilot
    has_attachments: bool = False
    # Global-brain-only inputs (concierge call kwargs).
    user_message_id: Any = None
    memory_block: str = ""
    # DRF-1284: consent-gated weekly nutrition picture. "" when the gate is
    # closed or Ayla gave nothing — the seam stays a pure carrier and never
    # builds it (the handler owns every consent read, per this module's
    # contract).
    nutrition_block: str = ""
    extra_system: str = ""


@dataclass(frozen=True)
class TurnReply:
    """Normalized brain output. Pure data — the seam caller performs all
    persistence / delivery / state transitions.

    ``matched=False`` mirrors a None SkillResult (no skill matched) so the
    caller's legacy fallback (echo) still fires. ``assistant_persisted``
    mirrors DiscoveryReply.persisted (the concierge store already wrote
    the assistant turn — the caller must not double-record).
    """

    matched: bool = True
    reply_text: str = ""
    action_type: str = ""
    action_data: dict[str, Any] | None = None
    should_send: bool = True
    should_handoff: bool = False
    handoff_reason: str = ""
    new_state: Any = None
    should_close_conversation: bool = False
    assistant_persisted: bool = False
    meta: dict[str, Any] | None = None


def orchestrate_turn(context: TurnContext) -> TurnReply:
    """Route the normalized turn to the legacy brain for ``context.surface``.

    Adapter selection uses the surface the caller already knows — NO
    tenant resolution happens here (OR-BOT-3: no fake/default tenant, no
    unknown_tenant short-circuit). The per-tenant brain is a tenant-scoped
    operation and fails closed when no tenant is in scope; the global
    brain legitimately runs with ``tenant=None``.

    OR-SHADOW-1: the legacy reply is computed FIRST and is the only one
    that matters. When ``ORCHESTRATOR_SHADOW_ENABLED`` is on (default
    off), ONE shadow job is enqueued for async observe-only execution —
    a failure here never affects the legacy turn.
    """

    if context.surface == SURFACE_GLOBAL:
        reply = _global_legacy_adapter(context)
    else:
        reply = _per_tenant_legacy_adapter(context)

    from apps.orchestrator.shadow_turn import shadow_enabled

    if shadow_enabled():
        try:
            from apps.orchestrator.shadow_turn import dispatch_shadow_turn

            dispatch_shadow_turn(context, reply)
        except Exception:  # noqa: BLE001 — shadow must never break the turn
            logger.exception("orchestrator.turn_seam.shadow_dispatch_failed")

    return reply


def _per_tenant_legacy_adapter(context: TurnContext) -> TurnReply:
    """Per-tenant brain: ``apps.skills.registry.dispatch`` (MAX + Telegram)."""

    from apps.tenancy.context import current_tenant

    if current_tenant() is None:
        # Tenant-scoped operation (skills read commercial data) — fail
        # closed. Reaching here means a per-tenant caller ran outside
        # tenant_scope: a wiring bug, never the global pilot (it uses
        # surface="global").
        raise RuntimeError("turn_seam: per_tenant surface requires an active tenant_scope")

    from apps.skills.base import SkillContext
    from apps.skills.registry import dispatch

    result = dispatch(
        SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text=context.text,
            trace_id=context.trace_id,
            has_attachments=context.has_attachments,
        )
    )
    if result is None:
        return TurnReply(matched=False)
    return TurnReply(
        matched=True,
        reply_text=result.reply_text,
        action_type=result.action_type or "",
        action_data=result.action_data,
        should_send=result.should_send,
        should_handoff=result.should_handoff,
        handoff_reason=result.handoff_reason or "",
        new_state=result.new_state,
        should_close_conversation=result.should_close_conversation,
        meta=result.meta,
    )


def _global_legacy_adapter(context: TurnContext) -> TurnReply:
    """Global tenant-less brain: ``apps.orchestrator.concierge`` concierge turn.

    ``tenant=None`` is the designed input (OR-BOT-3) — no tenant check,
    no sentinel fabrication, the concierge brain is unchanged.
    """

    from apps.orchestrator.concierge import generate_concierge_reply

    reply = generate_concierge_reply(
        context.text,
        bot_user=context.bot_user,
        conversation=context.conversation,
        user_message_id=context.user_message_id,
        memory_block=context.memory_block,
        nutrition_block=context.nutrition_block,
        extra_system=context.extra_system,
        trace_id=context.trace_id or None,
    )
    return TurnReply(
        matched=True,
        reply_text=reply.text,
        action_data=reply.action_data,
        assistant_persisted=reply.persisted,
    )


def turn_reply_to_skill_result(reply: TurnReply) -> Any:
    """Rebuild the legacy ``SkillResult`` (or None) for downstream code that
    already consumes it (handoff helper, reply-kind analytics, silence log).

    Inverse of the per-tenant adapter — keeps every post-seam code path
    byte-identical to the pre-seam direct dispatch.
    """

    if not reply.matched:
        return None
    from apps.skills.base import SkillResult

    return SkillResult(
        reply_text=reply.reply_text,
        action_type=reply.action_type,
        action_data=reply.action_data,
        should_send=reply.should_send,
        should_close_conversation=reply.should_close_conversation,
        new_state=reply.new_state,
        should_handoff=reply.should_handoff,
        handoff_reason=reply.handoff_reason,
        # SkillResult.meta is a required dict on dev (default_factory=dict);
        # TurnReply keeps None as "no meta" — normalise at the boundary.
        meta=reply.meta or {},
    )
