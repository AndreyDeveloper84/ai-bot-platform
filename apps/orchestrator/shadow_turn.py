"""Side-effect-free shadow execution of the new-brain compute subset
(OR-SHADOW-1..6).

The legacy brain (``turn_seam.orchestrate_turn`` adapters) stays
AUTHORITATIVE — this module only OBSERVES. ``compute_shadow_turn`` runs
the compute-only subset of the 19-step pipeline's brain (steps 5–7 +
the step-8/9 branch DECISIONS), never the full ``pipeline.turn()``
(OR-SHADOW-2). Steps that execute skills / tools / compose are NEVER
performed — the skill registry mutates (booking writes, handoff flips,
memory writes, event rows), and under the tools policy every skill is
MUTATING-or-UNKNOWN → fail-closed skip with the intended action recorded.

# What runs in shadow (Phase A audit)

| Step | Function | Read | Write | External side effect | Shadow-safe? | Action |
|---|---|---|---|---|---|---|
| 5 load_memory | ``memory.coordinator.load_snapshot`` | short_term (Redis), ClientProfile | — | — | SAFE_READ (degrades to empty) | reuse as-is |
| 6 intent_classify | ``intent_router.classify`` | LLM call (read-only semantics); tenant=None → legacy path | — (legacy path writes no audit) | LLM HTTP | SAFE (bounded by router timeouts) | reuse as-is |
| 7 pre_check | ``safety.pre_check.pre_check`` | regex on text | — | — | pure compute | shadow-data only (OR-SHADOW-5) |
| 8/9 block/clarify/handoff | branch decisions in ``turn`` | — | — | — | decision logic only | computed, never persisted |
| 10 skill_dispatch | ``skills.registry.dispatch`` | — | booking/handoff/memory/events | skill side effects | MUTATING/UNKNOWN | SKIPPED (intended action recorded) |
| 10.5 post-skill handoff | — | — | — | — | needs step 10 | NOT_EVALUABLE |
| 11 tool invocation | stub | — | — | — | — | NOT_APPLICABLE |
| 12 post_check | ``safety.post_check.post_check`` | — | — | — | pure, but needs step-10 reply | NOT_EVALUABLE |
| 13 compose | ``composer.compose`` | — | — | — | needs step 10 | NOT_EVALUABLE |

# Failure isolation / latency (§11) + transport (activation gate §3/§7)
#
# Shadow runs ASYNC on the EXISTING Celery worker pool — deliberately NOT
# on the ``ingress:*`` stream consumer: that consumer is a single
# sequential loop which also drains production DM traffic, so an
# LLM-bound shadow job there would head-of-line block live replies
# (activation-gate STOP-condition, avoided). The authoritative turn only
# pays one ``apply_async`` (~1ms) when the flag is ON, zero when OFF.
# No fire-and-forget threads; a worker failure can never touch the
# legacy reply, and ``acks_late=False`` means no redelivery amplification.

# Observability (§11/§12)

Structured tenant-less-safe logging only — NO AIRequestMetric (tenant FK
is mandatory there and fake tenants are forbidden; schema unchanged).
No raw user text / memory blocks / tool args in logs — ids, reason
codes, verdicts and latency only (§13 privacy).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, cast

from apps.orchestrator.turn_seam import TurnReply

logger = logging.getLogger(__name__)

# L0 execution statuses.
EXEC_PASS = "PASS"
EXEC_ERROR = "ERROR"
EXEC_TIMEOUT = "TIMEOUT"
EXEC_NOT_EVALUABLE = "NOT_EVALUABLE"

# Parity verdicts (§9).
MATCH = "MATCH"
MISMATCH = "MISMATCH"
NOT_EVALUABLE = "NOT_EVALUABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

# Control decisions (L1 vocabulary).
CONTROL_SEND = "send"
CONTROL_SILENCE = "silence"
CONTROL_HANDOFF = "handoff"
CONTROL_CLOSE = "close"


def shadow_enabled() -> bool:
    from django.conf import settings

    return bool(getattr(settings, "ORCHESTRATOR_SHADOW_ENABLED", False))


def shadow_timeout_ms() -> int:
    from django.conf import settings

    return int(getattr(settings, "ORCHESTRATOR_SHADOW_TIMEOUT_MS", 2500))


@dataclass(frozen=True)
class ShadowTurnResult:
    """What the compute-only shadow brain honestly produced (§8)."""

    execution_status: str
    intent: str = ""
    skill: str = ""
    safety_verdict: str = ""
    control_decision: str | None = None  # None = not decidable without dispatch
    intended_actions: tuple[str, ...] = ()
    not_evaluable_reasons: tuple[str, ...] = ()
    latency_ms: int = 0
    error: str = ""


@dataclass(frozen=True)
class ShadowComparison:
    """Per-level parity verdicts (§9). Missing data is NEVER a mismatch."""

    l0_execution: str
    l1_control: str
    l2_route: str
    l3_business: str
    l4_semantics: str
    l5_literal: str
    legacy_control: str = ""
    shadow_control: str = ""
    legacy_route: str = ""
    shadow_route: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _legacy_control(reply: TurnReply) -> str:
    if not reply.should_send:
        return CONTROL_SILENCE
    if reply.should_handoff:
        return CONTROL_HANDOFF
    if reply.should_close_conversation:
        return CONTROL_CLOSE
    return CONTROL_SEND


def compare_turns(legacy: TurnReply, shadow: ShadowTurnResult) -> ShadowComparison:
    """L0–L5 parity between the authoritative reply and the shadow result."""

    legacy_control = _legacy_control(legacy)
    shadow_control = shadow.control_decision or ""
    if shadow.control_decision is None:
        l1 = NOT_EVALUABLE
    else:
        l1 = MATCH if shadow_control == legacy_control else MISMATCH

    legacy_route = legacy.action_type or ""
    shadow_route = shadow.skill or shadow.intent or ""
    if legacy_route and shadow_route:
        l2 = MATCH if legacy_route == shadow_route else MISMATCH
    else:
        l2 = NOT_EVALUABLE

    return ShadowComparison(
        l0_execution=shadow.execution_status,
        l1_control=l1,
        l2_route=l2,
        # L3: business result needs skill dispatch — never executed in shadow.
        l3_business=NOT_EVALUABLE,
        # L4: no LLM judge without a separate owner decision.
        l4_semantics=NOT_IMPLEMENTED,
        # L5: shadow produces no reply text — nothing literal to compare.
        l5_literal=NOT_APPLICABLE,
        legacy_control=legacy_control,
        shadow_control=shadow_control,
        legacy_route=legacy_route,
        shadow_route=shadow_route,
    )


def compute_shadow_turn(
    *,
    text: str,
    conversation: Any,
    tenant: Any,
    timeout_ms: int | None = None,
) -> ShadowTurnResult:
    """Run the compute-only brain subset. NEVER mutates anything.

    ``tenant=None`` is the designed global-pilot input (OR-SHADOW-4):
    intent classify then takes its tenant-less legacy path; nothing is
    fabricated. A soft per-turn budget bounds the work; exceeding it
    marks the result TIMEOUT (LLM classify is bounded by router timeouts
    of its own).
    """

    from apps.orchestrator.intent_router import classify
    from apps.orchestrator.memory.coordinator import load_snapshot
    from apps.orchestrator.pipeline import _memory_to_dict
    from apps.orchestrator.safety.pre_check import SafetyVerdict, pre_check

    budget = (timeout_ms if timeout_ms is not None else shadow_timeout_ms()) / 1000
    t0 = time.monotonic()

    def _elapsed_over() -> bool:
        return (time.monotonic() - t0) > budget

    def _result(status: str, **kwargs: Any) -> ShadowTurnResult:
        return ShadowTurnResult(
            execution_status=status,
            latency_ms=int((time.monotonic() - t0) * 1000),
            **kwargs,
        )

    # Step 5 — memory snapshot (SAFE_READ; coordinator never raises).
    snapshot = load_snapshot(conversation)
    if _elapsed_over():
        return _result(EXEC_TIMEOUT, error="budget:before_intent")

    # Step 6 — intent classification (LLM, read-only semantics).
    try:
        intent = asyncio.run(
            classify(text, tenant=tenant, memory_snapshot=_memory_to_dict(snapshot))
        )
    except Exception as exc:  # noqa: BLE001 — shadow must never propagate
        return _result(EXEC_ERROR, error=f"classify:{type(exc).__name__}")
    if _elapsed_over():
        return _result(
            EXEC_TIMEOUT,
            intent=intent.intent or "",
            skill=intent.skill or "",
            error="budget:after_intent",
        )

    # Step 7 — safety pre-check as SHADOW DATA ONLY (OR-SHADOW-5: the
    # authoritative safety stays in the live handler path).
    pre = pre_check(text, intent_decision=intent)
    verdict = pre.verdict

    # Steps 8/9 — branch DECISIONS only (no canned persistence, no AdminTask).
    control: str | None
    if verdict == SafetyVerdict.HANDOFF:
        control = CONTROL_HANDOFF
    elif verdict in (SafetyVerdict.BLOCK, SafetyVerdict.CLARIFY):
        control = CONTROL_SEND
    else:
        control = None  # would require step-10 dispatch to decide

    not_evaluable: list[str] = []
    if control is None:
        not_evaluable.append("control:dispatch_never_executed")
    # Steps 10–13 are never performed (mutating/unknown tools policy).
    intended = ("SKIPPED_SKILL_DISPATCH:mutating_or_unknown_tools",)
    not_evaluable += [
        "business:dispatch_never_executed",
        "post_check:no_shadow_reply",
        "compose:no_shadow_reply",
    ]

    return _result(
        EXEC_PASS,
        intent=intent.intent or "",
        skill=intent.skill or "",
        safety_verdict=verdict.value,
        control_decision=control,
        intended_actions=intended,
        not_evaluable_reasons=tuple(not_evaluable),
    )


def shadow_sample_rate() -> float:
    """Fraction of eligible turns dispatched to shadow (0.0–1.0).

    Default 0.0 — flipping ORCHESTRATOR_SHADOW_ENABLED=true alone never
    floods the broker; activation requires an explicit rate (rollout
    ladder: 0.01 → 0.10 → 0.25 → 0.50 → 1.00, global pilot only).
    """

    from django.conf import settings

    try:
        rate = float(getattr(settings, "ORCHESTRATOR_SHADOW_SAMPLE_RATE", 0.0))
    except (TypeError, ValueError):
        return 0.0
    return min(max(rate, 0.0), 1.0)


def shadow_surfaces() -> frozenset[str]:
    """Rollout targeting (§6): which seam surfaces may dispatch shadow jobs.

    Default ``{"global"}`` — the first (and only approved) rollout target:
    the tenant-less global pilot. per-tenant MAX and Telegram are excluded
    unless ops explicitly widens the env list.
    """

    from django.conf import settings

    raw = getattr(settings, "ORCHESTRATOR_SHADOW_SURFACES", "global")
    return frozenset(s.strip() for s in str(raw).split(",") if s.strip())


def shadow_max_backlog() -> int:
    from django.conf import settings

    return int(getattr(settings, "ORCHESTRATOR_SHADOW_MAX_BACKLOG", 500))


def _sample_key(context: Any) -> str:
    """Stable, non-personal sampling key (§5): the per-turn trace id,
    falling back to the conversation id."""

    return str(context.trace_id or context.conversation.id)


def _in_sample(context: Any, rate: float) -> bool:
    """Deterministic, reproducible bucket — same key always lands the same
    way for a given rate, so a sampled turn is replayable."""

    import hashlib

    bucket = int(hashlib.sha256(_sample_key(context).encode()).hexdigest()[:8], 16)
    return bucket / 0xFFFFFFFF < rate


# Dedicated celery queue for the shadow task (CELERY_TASK_ROUTES routes
# only ``orchestrator.shadow_turn`` here). The backlog admission gate and
# operator inspection read THIS Redis list — never the default ``celery``
# queue, or the measurement silently drifts after the queue split.
SHADOW_QUEUE = "shadow"


def _celery_queue_len() -> int:
    """Broker backlog of the dedicated shadow queue (Redis list ``shadow``)."""

    from apps.ingress.streams import _client

    # The sync redis client returns int at runtime; its stub union includes
    # the async variant (Awaitable[int]) — narrow explicitly, semantics unchanged.
    return int(cast(int, _client().llen(SHADOW_QUEUE)))


def dispatch_shadow_turn(context: Any, legacy_reply: TurnReply) -> None:
    """Fire the async shadow job for one authoritative turn (all gates).

    Gates, in order (each one cheap and fail-closed towards NOT dispatching):
      1. ORCHESTRATOR_SHADOW_ENABLED;
      2. surface targeting (default: global pilot only);
      3. deterministic sampling before dispatch (§5);
      4. broker backlog admission limit (§8) — over threshold the job is
         dropped with an observability line, legacy continues untouched.

    The caller wraps this in try/except — a dispatch failure must never
    affect the legacy turn. Transport is the EXISTING Celery worker pool
    (not the MAX stream consumer) — see the module docstring.
    """

    from apps.tenancy.context import current_tenant

    if not shadow_enabled():
        return
    if context.surface not in shadow_surfaces():
        return
    rate = shadow_sample_rate()
    if rate <= 0.0 or (rate < 1.0 and not _in_sample(context, rate)):
        return
    backlog = _celery_queue_len()
    if backlog >= shadow_max_backlog():
        logger.warning(
            "orchestrator.shadow.dispatch_skipped_backlog trace_id=%s backlog=%d limit=%d",
            context.trace_id,
            backlog,
            shadow_max_backlog(),
        )
        return

    tenant = current_tenant()
    legacy = asdict(legacy_reply)
    if legacy.get("new_state") is not None:
        # Enum states aren't JSON-serialisable — the comparison only reads
        # control/action fields, so a string snapshot is sufficient.
        legacy["new_state"] = str(legacy["new_state"])
    payload = {
        "trace_id": context.trace_id,
        "surface": context.surface,
        "channel": context.channel,
        "text": context.text,
        "conversation_id": str(context.conversation.id),
        "tenant_id": str(tenant.id) if tenant is not None else None,
        "legacy_reply": legacy,
    }
    from apps.orchestrator.tasks import run_shadow_turn_task

    run_shadow_turn_task.apply_async(args=[payload])
    logger.info(
        "orchestrator.shadow.attempted trace_id=%s surface=%s rate=%.3f backlog=%d",
        context.trace_id,
        context.surface,
        rate,
        backlog,
    )


def log_shadow_comparison(
    *, trace_id: str, surface: str, shadow: ShadowTurnResult, comparison: ShadowComparison
) -> None:
    """The single observability sink — structured, tenant-less-safe,
    PII-free (ids / codes / verdicts / latency only)."""

    logger.info(
        "orchestrator.shadow.completed trace_id=%s surface=%s status=%s "
        "latency_ms=%d l0=%s l1=%s l2=%s l3=%s l4=%s legacy_control=%s "
        "shadow_control=%s legacy_route=%s shadow_route=%s verdict=%s ne=%s err=%s",
        trace_id,
        surface,
        shadow.execution_status,
        shadow.latency_ms,
        comparison.l0_execution,
        comparison.l1_control,
        comparison.l2_route,
        comparison.l3_business,
        comparison.l4_semantics,
        comparison.legacy_control,
        comparison.shadow_control,
        comparison.legacy_route,
        comparison.shadow_route,
        shadow.safety_verdict,
        ",".join(shadow.not_evaluable_reasons) or "-",
        shadow.error or "-",
    )
