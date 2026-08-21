"""Celery task for the side-effect-free shadow turn (OR-SHADOW + gate).

Transport decision (activation gate, §3/§7): the shadow compute runs on
the EXISTING Celery worker pool — NOT on the MAX stream consumer. The
stream consumer is a single sequential loop that also drains production
DM traffic; an LLM-bound shadow job there would head-of-line block live
replies (STOP-condition §17.1). The Celery pool is the platform's
existing async-job pool (audit sweep, reminders) — shadow saturation
there delays only non-latency-critical jobs, never a user reply.

Retry/amplification policy: ``acks_late=False`` + no retry config — a
crashed shadow job is LOST (acceptable for observe-only diagnostics) and
never redelivered, so no uncontrolled amplification is possible.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


@shared_task(
    name="orchestrator.shadow_turn",
    ignore_result=True,
    acks_late=False,  # observe-only: a lost job is fine, redelivery is not
    soft_time_limit=30,  # hard ceiling far above the soft per-turn budget
)
def run_shadow_turn_task(payload: dict[str, Any]) -> None:
    """Compute + compare + log for one dispatched shadow job."""

    try:
        run_shadow_from_payload(payload)
    except Exception:  # noqa: BLE001 — diagnostics must never propagate
        logger.exception("orchestrator.shadow.failed trace_id=%s", payload.get("trace_id", ""))


def run_shadow_from_payload(payload: dict[str, Any]) -> None:
    """Compute-only shadow brain + comparison for one job. Never mutates."""

    from apps.conversations.models import Conversation
    from apps.orchestrator.shadow_turn import (
        EXEC_NOT_EVALUABLE,
        ShadowTurnResult,
        compare_turns,
        compute_shadow_turn,
        log_shadow_comparison,
    )
    from apps.orchestrator.turn_seam import TurnReply
    from apps.tenancy.models import Tenant

    trace_id = str(payload.get("trace_id", ""))
    surface = str(payload.get("surface", ""))

    conversation_id = payload.get("conversation_id")
    conversation = (
        Conversation.all_tenants.filter(id=conversation_id).first() if conversation_id else None
    )
    if conversation is None:
        shadow = ShadowTurnResult(
            execution_status=EXEC_NOT_EVALUABLE,
            not_evaluable_reasons=("missing_context:conversation",),
        )
        comparison = compare_turns(TurnReply(**payload["legacy_reply"]), shadow)
        log_shadow_comparison(
            trace_id=trace_id, surface=surface, shadow=shadow, comparison=comparison
        )
        return

    # Real lookup of the tenant the producer stamped (None for the global
    # pilot). Never fabricated — tenant=None takes classify's tenant-less
    # legacy path (OR-SHADOW-4).
    tenant = None
    tenant_id = payload.get("tenant_id") or None
    if tenant_id:
        tenant = Tenant.objects.filter(id=tenant_id).first()

    shadow = compute_shadow_turn(
        text=str(payload.get("text", "")),
        conversation=conversation,
        tenant=tenant,
    )
    comparison = compare_turns(TurnReply(**payload["legacy_reply"]), shadow)
    log_shadow_comparison(trace_id=trace_id, surface=surface, shadow=shadow, comparison=comparison)
