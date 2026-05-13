"""Orchestrator pipeline — turn() (DRF-535 / Sprint 6 / O1).

The 19-step contract that takes one inbound ChannelMessage from the
ingress consumer and produces a fully-routed outbound reply. Per
PHASE0_DESIGN §5.1. This is the FIRST sprint that wires every component
sprints 1-5 built — without this, every previous deliverable is
plumbing nobody calls.

### The 19 steps

 1. Resolve tenant from ChannelMessage.tenant_slug + enter tenant_scope.
 2. resolve_or_create_bot_user (Sprint 2 / A2).
 3. resolve_or_create_conversation (Sprint 2 / B*).
 4. Save user Message (Sprint 2 + audit row).
 5. Load MemorySnapshot (O6 / DRF-540).
 6. Intent classification — gpt-4o-mini structured JSON (O2 / DRF-536).
 7. Safety pre-check — regex keyword guard (O3 / DRF-537).
 8. If verdict in {block, clarify} → respond canned, skip skill.
 9. If verdict == handoff → AdminTask + canned reply.
10. Skill dispatch (Sprint 3 / D1 dispatcher).
11. Tool invocation if skill emitted tool_calls (O7 / DRF-541).
12. Safety post-check — outbound text (O4 / DRF-538).
13. Composer → final text + UI keyboard (O5 / DRF-539).
14. Save assistant Message.
15. Update short-term memory.
16. Emit message_sent event.
17. Write audit row.
18. Replay recorder.capture (Sprint 5 / A2 — refined by O8 / DRF-542).
19. Channel outbound send (Sprint 2 + O9 / DRF-546 refinement).

### Why one big function

Each step IS small (one helper call). The reason they sit in one
function instead of being scattered across many is that the ordering
+ short-circuit branches (block / handoff) ARE the contract. Splitting
would obscure the flow and let bugs creep in through partial reorders.

### Safety boundary

Outer try/except wraps the entire body. ANY unhandled exception →
emits `pipeline_error` event, returns a TurnResult with a fallback
"sorry, something went wrong" reply. Webhook handler upstream returns
200 regardless so MAX doesn't retry storm.

### Performance budget per §5.2

End-to-end p95 ≤ 4000ms goal (alert at 6000ms). Each step has its own
sub-budget — covered by G4 latency SLO test (DRF-553).
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from asgiref.sync import sync_to_async

from apps.orchestrator.composer import ComposedReply, compose
from apps.orchestrator.intent_router import IntentDecision, classify
from apps.orchestrator.memory.coordinator import MemorySnapshot, load_snapshot
from apps.orchestrator.safety.post_check import (
    post_check,
)
from apps.orchestrator.safety.pre_check import SafetyVerdict, pre_check
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

# Sprint 8 / T2 (DRF-706) — OTel instrumentation. We open one root span
# per call to `turn()` and emit a structured event for each of the 19
# pipeline steps. Sentry's `before_send` (E1) reads the active span's
# trace_id; AuditLog + ReplayTrace (T3 / T4 follow-ups) attach to it too.
#
# Why events, not 19 child spans: a per-step child span tree multiplies
# context-manager bookkeeping over every early return in `_run_under_tenant`
# (~12 short-circuit branches). Span EVENTS give us the same observability
# (a developer can filter on `pipeline.step=intent` in Tempo / Jaeger),
# at a fraction of the diff churn — and the test (T5 / DRF-709) explicitly
# asserts events on the root, not child-span count.
try:
    from opentelemetry import trace as _otel_trace

    _tracer: Any = _otel_trace.get_tracer(__name__)
except ImportError:  # pragma: no cover — optional dep
    _otel_trace = None  # type: ignore[assignment]
    _tracer = None


@contextmanager
def _step_event(span: Any, step: str, **attrs: Any) -> Any:
    """Emit a span event marking the start AND end of one pipeline step.

    Two events instead of one because Tempo / Jaeger UIs render an event
    as a single instant — we want to surface duration via the start/end
    pair without converting events to child spans.
    """
    if span is None:
        yield
        return
    span.add_event(f"step.{step}.start", attributes={k: str(v) for k, v in attrs.items()})
    try:
        yield
        span.add_event(f"step.{step}.end")
    except Exception as exc:
        span.add_event(
            f"step.{step}.error",
            attributes={"error.type": type(exc).__name__, "error.message": str(exc)[:200]},
        )
        raise


@contextmanager
def _root_span(message: Any, trace_id: str) -> Any:
    """Open the per-turn OTel root span. No-op when ``_tracer`` is None."""
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span("pipeline.turn") as span:
        span.set_attribute("pipeline.trace_id", trace_id)
        span.set_attribute("channel", getattr(message, "channel", ""))
        span.set_attribute("tenant.slug", getattr(message, "tenant_slug", ""))
        span.set_attribute("is_shadow", bool(getattr(message, "is_shadow", False)))
        yield span


logger = logging.getLogger(__name__)


# Canned text used when the pipeline can't route normally.
_FALLBACK_BLOCK = (
    "Извините, я не могу ответить на этот запрос. Передам менеджеру — ответит в течение 30 минут."
)
_FALLBACK_CLARIFY = (
    "Не совсем понял, можете уточнить? Или напишите «оператор», и я свяжу с менеджером."
)
_FALLBACK_HANDOFF = "Передаю менеджеру — ответят в течение 30 минут."
_FALLBACK_ERROR = "Извините, что-то пошло не так. Попробуйте позже или напишите «оператор»."


@dataclass(frozen=True)
class ChannelMessage:
    """Inbound message from any channel — shaped uniformly by the channel
    adapter at ingress (Sprint 2 / D-track).

    Fields:
      tenant_slug: routes to the owning Tenant (resolved in step 1).
      channel: 'max' | 'telegram' | 'whatsapp' | 'web'.
      channel_user_id: stable user id within the channel.
      chat_id: outbound destination (may equal channel_user_id in DMs).
      display_name: channel-reported display name (for resolve enrichment).
      text: the user's message body.
      trace_id: pipeline trace id (UUID7 string). Recorder + observability hooks.
      raw: optional raw inbound payload for forensic / replay.
    """

    tenant_slug: str
    channel: str
    channel_user_id: str
    chat_id: str
    text: str
    display_name: str = ""
    trace_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    # Sprint 8 / N2 (DRF-701) — shadow-mode signalling from edge.
    # nginx mirror sets `X-Shadow: 1` on the mirrored copy; the ingress
    # view (apps/ingress/views.py::max_webhook) reads the header and
    # threads it through. S2 (DRF-717) consumes this in step 19 to
    # short-circuit outbound. Per-message override of `tenant.shadow_mode`.
    is_shadow: bool = False


@dataclass
class TurnResult:
    """Outcome of one pipeline turn. Returned to the ingress consumer
    for observability + retry decisions. ``ok=False`` is an EXPECTED
    failure (block, clarify, error fallback); only unhandled exceptions
    bubble out of turn().

    ``short_circuited_at_step`` is a ``float`` because Sprint 7 / O2
    (DRF-556) inserts step 10.5 (post-skill handoff) between the
    Sprint 6 step 10 (dispatch) and step 11 (tool invocation stub).
    Existing int values (0, 8, 9, 10) keep equality semantics:
    ``8 == 8.0`` is True in Python.
    """

    ok: bool
    trace_id: str
    reply: ComposedReply | None = None
    intent: IntentDecision | None = None
    pre_check_verdict: str = ""
    post_check_verdict: str = ""
    error: str = ""
    short_circuited_at_step: float = 0  # 0 = ran to completion


async def turn(message: ChannelMessage) -> TurnResult:
    """Execute one full pipeline turn for ``message``.

    Args:
      message: parsed ChannelMessage from the ingress consumer.

    Returns:
      :class:`TurnResult` carrying the composed reply (when one was
      produced), the intent decision, and short-circuit info.
    """

    trace_id = message.trace_id or str(uuid.uuid4())

    with _root_span(message, trace_id) as span:
        try:
            # --- Step 1: tenant resolution + scope ---
            with _step_event(span, "tenant_resolve"):
                tenant = await sync_to_async(_resolve_tenant)(message.tenant_slug)
            if tenant is None:
                if span is not None:
                    span.set_attribute("short_circuit_step", 1)
                return _error_result(trace_id, "unknown_tenant", step=1)

            if span is not None:
                span.set_attribute("tenant.id", str(tenant.id))
            return await _run_under_tenant(message, tenant, trace_id, span=span)

        except Exception as exc:  # noqa: BLE001 — outer safety boundary
            logger.exception("pipeline.turn.unhandled trace_id=%s err=%s", trace_id, exc)
            if span is not None:
                span.set_attribute("error", True)
                span.set_attribute("error.type", type(exc).__name__)
            # Sprint 8 / E2 (DRF-711) — additive Sentry capture. logger.exception
            # already runs (diagnostic), Sentry is the operator paging layer.
            # Tags are pulled by E1's scrub_event hook from the active OTel span;
            # we add `tenant_slug` here because the outer except runs BEFORE
            # tenant resolution might have succeeded.
            _sentry_capture_pipeline_error(exc, trace_id, message)
            await sync_to_async(_emit_pipeline_error, thread_sensitive=False)(trace_id, str(exc))
            return _error_result(trace_id, f"unhandled: {exc}", step=0)


async def _run_under_tenant(
    message: ChannelMessage,
    tenant: Tenant,
    trace_id: str,
    *,
    span: Any = None,
) -> TurnResult:
    """Steps 2-19. Entered after tenant_scope is established."""

    # Sprint 8 / S2 (DRF-717) — shadow-mode early decision. Per-message
    # `is_shadow` (from the X-Shadow edge header, N2) OR the per-tenant
    # `shadow_mode` flag (S1) → write rows under is_shadow=True and
    # short-circuit outbound at step 19. The decision is made ONCE here
    # so every downstream step writes a consistent row set.
    is_shadow = bool(getattr(message, "is_shadow", False) or getattr(tenant, "shadow_mode", False))
    if span is not None:
        # The outer span already had a coarse is_shadow attribute on the
        # channel message; override with the effective decision so the
        # dashboard filter `is_shadow=true` reaches tenant-mode rows too.
        span.set_attribute("is_shadow", is_shadow)

    # tenant_scope is a sync context manager. Async ORM calls happen
    # inside sync_to_async wrappers below, which inherit ContextVar.
    with tenant_scope(tenant):
        # --- Step 2: resolve_or_create_bot_user ---
        with _step_event(span, "resolve_bot_user"):
            bot_user = await sync_to_async(_resolve_bot_user)(message)

        # --- Step 3: resolve_or_create_conversation ---
        with _step_event(span, "resolve_conversation"):
            conversation = await sync_to_async(_resolve_conversation)(bot_user, is_shadow=is_shadow)

        # --- Step 4: save user Message ---
        with _step_event(span, "save_user_message"):
            await sync_to_async(_save_user_message)(conversation, message.text, trace_id)

        # --- Step 5: load memory snapshot ---
        with _step_event(span, "load_memory"):
            memory_snapshot = await sync_to_async(load_snapshot)(conversation)

        # --- Step 6: intent classification ---
        with _step_event(span, "intent_classify"):
            intent_decision = await classify(
                message.text,
                memory_snapshot=_memory_to_dict(memory_snapshot),
                brand_voice=None,  # Sprint 6 doesn't read BrandVoiceConfig here; Sprint 7+
            )
            if span is not None and intent_decision is not None:
                span.set_attribute("intent", getattr(intent_decision, "intent", "") or "")
                span.set_attribute("skill", getattr(intent_decision, "skill", "") or "")

        # --- Step 7: safety pre-check ---
        with _step_event(span, "pre_check"):
            pre_result = pre_check(message.text, intent_decision=intent_decision)
            if span is not None:
                span.set_attribute("pre_check_verdict", pre_result.verdict.value)

        # --- Step 8: blocked / clarify short-circuit ---
        if pre_result.verdict == SafetyVerdict.BLOCK:
            reply = await sync_to_async(_canned_reply)(_FALLBACK_BLOCK)
            await sync_to_async(_save_assistant)(conversation, reply.text, "block", trace_id)
            return TurnResult(
                ok=True,
                trace_id=trace_id,
                reply=reply,
                intent=intent_decision,
                pre_check_verdict=pre_result.verdict.value,
                short_circuited_at_step=8,
            )

        if pre_result.verdict == SafetyVerdict.CLARIFY:
            reply = await sync_to_async(_canned_reply)(_FALLBACK_CLARIFY)
            await sync_to_async(_save_assistant)(conversation, reply.text, "clarify", trace_id)
            return TurnResult(
                ok=True,
                trace_id=trace_id,
                reply=reply,
                intent=intent_decision,
                pre_check_verdict=pre_result.verdict.value,
                short_circuited_at_step=8,
            )

        # --- Step 9: handoff ---
        if pre_result.verdict == SafetyVerdict.HANDOFF:
            await sync_to_async(_create_handoff)(
                conversation, reason=pre_result.reason or "pre_check_handoff"
            )
            reply = await sync_to_async(_canned_reply)(_FALLBACK_HANDOFF)
            await sync_to_async(_save_assistant)(conversation, reply.text, "handoff", trace_id)
            return TurnResult(
                ok=True,
                trace_id=trace_id,
                reply=reply,
                intent=intent_decision,
                pre_check_verdict=pre_result.verdict.value,
                short_circuited_at_step=9,
            )

        # --- Step 10: skill dispatch ---
        with _step_event(span, "skill_dispatch"):
            skill_result = await sync_to_async(_dispatch_skill)(
                conversation, bot_user, message.text, trace_id, intent_decision
            )
            if span is not None and skill_result is not None:
                span.set_attribute(
                    "skill.action_type", getattr(skill_result, "action_type", "") or ""
                )
        if skill_result is None:
            # No skill matched + no echo fallback hit — pipeline fallback.
            reply = await sync_to_async(_canned_reply)(_FALLBACK_CLARIFY)
            await sync_to_async(_save_assistant)(conversation, reply.text, "no_skill", trace_id)
            return TurnResult(
                ok=False,
                trace_id=trace_id,
                reply=reply,
                intent=intent_decision,
                pre_check_verdict=pre_result.verdict.value,
                error="no_skill_matched",
                short_circuited_at_step=10,
            )

        # --- Step 10.5: post-skill handoff (Sprint 7 / O2 / DRF-556) ---
        # A KB-driven skill (e.g. FAQ on a low-confidence retrieval) can
        # request handoff AFTER running. Sprint 6 only had pre-skill
        # handoff at step 9 via SafetyVerdict.HANDOFF; this branch is the
        # post-dispatch counterpart. We reuse _create_handoff (same one
        # step 9 uses) so a single AdminTask flow handles both pre- and
        # post-skill cases.
        #
        # The skill MAY have set its own ``reply_text`` (a softer
        # "переключаю на менеджера…" line); we honour it when non-empty
        # and fall back to the canned _FALLBACK_HANDOFF when blank.
        if skill_result.should_handoff:
            reason = skill_result.handoff_reason or "skill_requested_handoff"
            await sync_to_async(_create_handoff)(conversation, reason=reason)
            handoff_text = skill_result.reply_text or _FALLBACK_HANDOFF
            reply = await sync_to_async(_canned_reply)(handoff_text)
            await sync_to_async(_save_assistant)(conversation, reply.text, "handoff", trace_id)
            return TurnResult(
                ok=True,
                trace_id=trace_id,
                reply=reply,
                intent=intent_decision,
                pre_check_verdict=pre_result.verdict.value,
                short_circuited_at_step=10.5,
            )

        # --- Step 11: tool invocation (Phase 0: skills don't emit tool_calls) ---
        # Reserved for Sprint 7+ when skills start emitting tool_calls_made.

        # --- Step 12: safety post-check ---
        with _step_event(span, "post_check"):
            post_result = post_check(getattr(skill_result, "reply_text", ""))
            if span is not None:
                span.set_attribute("post_check_verdict", post_result.verdict.value)

        # --- Step 13: compose ---
        with _step_event(span, "compose"):
            reply = compose(skill_result, post_check=post_result)

        # --- Step 14: save assistant Message ---
        with _step_event(span, "save_assistant"):
            await sync_to_async(_save_assistant)(
                conversation,
                reply.text,
                getattr(skill_result, "action_type", "") or "skill_reply",
                trace_id,
            )

        # --- Step 15: update short-term memory ---
        with _step_event(span, "update_memory"):
            await sync_to_async(_update_short_term)(conversation, reply.text, trace_id)

        # --- Step 16: emit message_sent event ---
        with _step_event(span, "emit_message_sent"):
            await sync_to_async(_emit_message_sent, thread_sensitive=False)(
                bot_user, conversation, trace_id, intent_decision
            )

        # --- Step 17: write audit row (each layer already writes its own;
        # final summary audit goes here) ---
        with _step_event(span, "write_audit"):
            await sync_to_async(_write_pipeline_audit, thread_sensitive=False)(
                trace_id, intent_decision, pre_result.verdict.value, post_result.verdict.value
            )

        # --- Step 18: replay recorder.capture ---
        with _step_event(span, "replay_capture"):
            await sync_to_async(_replay_capture, thread_sensitive=False)(
                trace_id,
                message,
                intent_decision,
                pre_result.verdict.value,
                skill_result,
                post_result.verdict.value,
                reply,
            )

        # --- Step 19: channel outbound send (Sprint 6 / O9 / DRF-546) ---
        # Send the composed reply to the channel with retry-3x backoff.
        # Permanent failure → AdminTask of type=MANUAL for operator triage
        # (DLQ via tasks table — no separate queue infra in Phase 0).
        #
        # Sprint 8 / S2 (DRF-717): when `is_shadow` is True we DO NOT
        # send outbound — every other observability hook (audit, replay,
        # delta) already ran. The shadow Conversation/Message rows let
        # the daily delta (S3) measure agreement without showing the
        # platform's reply to the real user.
        if is_shadow:
            with _step_event(span, "send_outbound_skipped_shadow"):
                await sync_to_async(_write_shadow_drop_audit, thread_sensitive=False)(
                    trace_id, conversation
                )
            if span is not None:
                span.set_attribute("outbound_ok", True)
                span.set_attribute("shadow_dropped_outbound", True)
            return TurnResult(
                ok=True,
                trace_id=trace_id,
                reply=reply,
                intent=intent_decision,
                pre_check_verdict=pre_result.verdict.value,
                post_check_verdict=post_result.verdict.value,
                error="",
            )

        with _step_event(span, "send_outbound"):
            outbound_ok = await sync_to_async(_send_outbound, thread_sensitive=False)(
                message, reply, conversation, trace_id
            )
            if span is not None:
                span.set_attribute("outbound_ok", bool(outbound_ok))

        return TurnResult(
            ok=outbound_ok,
            trace_id=trace_id,
            reply=reply,
            intent=intent_decision,
            pre_check_verdict=pre_result.verdict.value,
            post_check_verdict=post_result.verdict.value,
            error="" if outbound_ok else "outbound_failed",
        )


# --- Helpers (sync, called via sync_to_async from turn()) -------------------


def _resolve_tenant(slug: str) -> Tenant | None:
    """Step 1 helper."""
    try:
        return Tenant.objects.get(slug=slug)
    except Tenant.DoesNotExist:
        return None


def _resolve_bot_user(message: ChannelMessage):
    """Step 2 helper. Caller is already inside tenant_scope."""
    from apps.identity.services import resolve_or_create_bot_user

    return resolve_or_create_bot_user(
        channel=message.channel,
        channel_user_id=message.channel_user_id,
        chat_id=message.chat_id,
        display_name=message.display_name,
    )


def _resolve_conversation(bot_user, *, is_shadow: bool = False):
    """Step 3 helper. Find/create the active Conversation for bot_user.

    Sprint 8 / S2 (DRF-717): when ``is_shadow`` is True, we look for an
    open shadow row and create one if missing — this row lives ALONGSIDE
    the primary active Conversation (N3 / DRF-702 relaxed the unique
    constraint so both can coexist). Outbound is suppressed for shadow
    turns at step 19; the row exists purely for the delta dashboard.
    """
    from apps.conversations.models import Conversation

    conv = (
        Conversation.objects.filter(bot_user=bot_user, is_active=True, is_shadow=is_shadow)
        .order_by("-created_at")
        .first()
    )
    if conv is None:
        conv = Conversation.objects.create(
            tenant=bot_user.tenant,
            bot_user=bot_user,
            is_shadow=is_shadow,
        )
    return conv


def _save_user_message(conversation, text: str, trace_id: str):
    """Step 4 helper."""
    from apps.conversations.models import Message

    Message.objects.create(
        tenant=conversation.tenant,
        conversation=conversation,
        role="user",
        content=text,
        trace_id=_uuid_or_none(trace_id),
    )


def _save_assistant(conversation, text: str, action_type: str, trace_id: str):
    """Step 14 helper."""
    from apps.conversations.models import Message

    Message.objects.create(
        tenant=conversation.tenant,
        conversation=conversation,
        role="assistant",
        content=text,
        action_type=action_type,
        trace_id=_uuid_or_none(trace_id),
    )


def _uuid_or_none(value: str):
    """Coerce a string trace_id to UUID, return None on bad shape."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None


def _canned_reply(text: str) -> ComposedReply:
    """Build a fallback ComposedReply with no skill_result."""
    return ComposedReply(text=text, final_send=True)


def _create_handoff(conversation, *, reason: str):
    """Step 9 helper."""
    from apps.handoff.models import AdminTask
    from apps.handoff.services import create_admin_task

    create_admin_task(
        conversation,
        task_type=AdminTask.TaskType.HANDOFF,
        reason=reason,
    )


def _dispatch_skill(
    conversation,
    bot_user,
    text: str,
    trace_id: str,
    intent: IntentDecision | None = None,
):
    """Step 10 helper.

    ``intent`` is the step-6 :class:`IntentDecision` for this turn,
    threaded into :class:`SkillContext.intent` so Sprint 7+ KB-driven
    skills (FAQ, booking) can branch on it without re-classifying.
    Optional for backward-compat with Sprint 3 skills + tests that
    construct a context directly.
    """
    from apps.skills.base import SkillContext
    from apps.skills.registry import dispatch

    ctx = SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        trace_id=trace_id,
        intent=intent,
    )
    return dispatch(ctx)


def _update_short_term(conversation, text: str, trace_id: str):
    """Step 15 helper."""
    from apps.orchestrator.memory import short_term

    short_term.append(
        conversation.id,
        role="assistant",
        content=text,
        trace_id=trace_id,
    )


def _emit_message_sent(bot_user, conversation, trace_id: str, intent: IntentDecision):
    """Step 16 helper."""
    from apps.events.services import emit
    from apps.events.vocabulary import MESSAGE_SENT

    emit(
        MESSAGE_SENT,
        distinct_id=str(bot_user.id),
        dialog_id=conversation.id,
        properties={"trace_id": trace_id, "intent": intent.intent},
    )


def _write_pipeline_audit(
    trace_id: str,
    intent: IntentDecision,
    pre_verdict: str,
    post_verdict: str,
):
    """Step 17 helper."""
    from apps.audit.services import write_audit

    write_audit(
        "pipeline.turn.completed",
        payload={
            "trace_id": trace_id,
            "intent": intent.intent,
            "skill": intent.skill,
            "pre_check": pre_verdict,
            "post_check": post_verdict,
        },
    )


def _sentry_capture_pipeline_error(exc: BaseException, trace_id: str, message: Any) -> None:
    """Sprint 8 / E2 (DRF-711) — additive Sentry capture for the outer
    pipeline boundary.

    Defensive: import is lazy so test environments without `sentry_sdk`
    available don't crash. The capture itself uses Sentry's scope API
    to add tags so the dashboard's filter (`trace_id:abc`) finds
    pipeline crashes alongside other event types.
    """
    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover — optional dep
        return
    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("trace_id", trace_id)
            scope.set_tag("tenant_slug", getattr(message, "tenant_slug", "") or "")
            scope.set_tag("channel", getattr(message, "channel", "") or "")
            scope.set_tag("pipeline_step", "turn")
            sentry_sdk.capture_exception(exc)
    except Exception:  # noqa: BLE001 — Sentry never breaks the request
        logger.warning("pipeline.sentry_capture_failed trace_id=%s", trace_id)


def _write_shadow_drop_audit(trace_id: str, conversation) -> None:
    """Step 19 shadow short-circuit (Sprint 8 / S2 / DRF-717).

    Audit-only equivalent of ``_send_outbound``. Lets the dashboard
    (D1) and replay differ count shadow turns as "outbound dropped"
    rather than as silent missing rows.
    """
    from apps.audit.services import write_audit

    write_audit(
        "pipeline.shadow_dropped_outbound",
        payload={
            "trace_id": trace_id,
            "conversation_id": str(conversation.id),
            "tenant_id": str(conversation.tenant_id),
        },
    )


def _replay_capture(
    trace_id: str,
    message: ChannelMessage,
    intent: IntentDecision,
    pre_verdict: str,
    skill_result,
    post_verdict: str,
    reply: ComposedReply,
):
    """Step 18 helper (refined by Sprint 6 / O8 / DRF-542).

    Per PHASE0_DESIGN §7.1 the recorder receives a LIST of step snapshots —
    one entry per pipeline stage. Replay differ + assertion engine read
    individual stages; a single collapsed dict loses the per-stage forensic
    granularity.

    Step shape:
      [
        {step: 'inbound',    payload: {text, channel, channel_user_id}},
        {step: 'intent',     payload: {intent, skill, confidence, risk_level}},
        {step: 'pre_check',  payload: {verdict}},
        {step: 'skill',      payload: {name, reply_text, action_type}},
        {step: 'post_check', payload: {verdict}},
        {step: 'composer',   payload: {final_text, safety_revised, keyboard_size}},
      ]

    The redactor walks every step recursively, so PII in any field is
    redacted before persistence. Recorder still honours sampling rate
    (REPLAY_SAMPLE_RATE_PROD/STAGING/TEST) — sample miss → no row written.

    Failures swallowed — recorder is observability, must never break the
    turn. Sentry alert through logger.exception covers the diagnostic path.
    """

    try:
        from apps.replay.recorder import capture as recorder_capture

        steps = [
            {
                "step": "inbound",
                "payload": {
                    "text": message.text,
                    "channel": message.channel,
                    "channel_user_id": message.channel_user_id,
                },
            },
            {
                "step": "intent",
                "payload": {
                    "intent": intent.intent,
                    "skill": intent.skill,
                    "confidence": intent.confidence,
                    "risk_level": intent.risk_level,
                },
            },
            {
                "step": "pre_check",
                "payload": {"verdict": pre_verdict},
            },
            {
                "step": "skill",
                "payload": {
                    "name": getattr(skill_result, "name", ""),
                    "reply_text": getattr(skill_result, "reply_text", "") or "",
                    "action_type": getattr(skill_result, "action_type", "") or "",
                },
            },
            {
                "step": "post_check",
                "payload": {"verdict": post_verdict},
            },
            {
                "step": "composer",
                "payload": {
                    "final_text": reply.text,
                    "safety_revised": reply.safety_revised,
                    "keyboard_size": len(reply.ui_keyboard),
                },
            },
        ]
        # Module-level capture function honours settings sampling rate
        # and emits REPLAY_CAPTURED event on success.
        recorder_capture(trace_id, steps)
    except Exception:  # noqa: BLE001 — recorder is observability, must never break turn
        logger.exception("pipeline.replay_capture_failed trace_id=%s", trace_id)


def _emit_pipeline_error(trace_id: str, error: str):
    """Outer-catch helper. Best-effort emit — error path can't itself raise."""
    try:
        from apps.events.services import emit

        emit(
            "pipeline_error",
            properties={"trace_id": trace_id, "error": error[:500]},
        )
    except Exception:  # noqa: BLE001
        logger.exception("pipeline.emit_error_failed trace_id=%s", trace_id)


def _memory_to_dict(snapshot: MemorySnapshot) -> dict[str, Any]:
    return {
        "history": list(snapshot.history),
        "long_term": dict(snapshot.long_term),
        "slot_state": dict(snapshot.slot_state),
    }


def _error_result(trace_id: str, error: str, *, step: int) -> TurnResult:
    """Build a TurnResult for an outer-catch error path."""
    return TurnResult(
        ok=False,
        trace_id=trace_id,
        reply=ComposedReply(text=_FALLBACK_ERROR, final_send=True),
        error=error,
        short_circuited_at_step=step,
    )


# --- Step 19: outbound + retry/DLQ (Sprint 6 / O9 / DRF-546) ----------------

# Retry config — exponential backoff. Tuned for the per-attempt 500ms
# budget × 3 retries staying under the 4000ms total-turn p95 goal.
_OUTBOUND_MAX_ATTEMPTS = 3
_OUTBOUND_BACKOFF_SECONDS = (0.1, 0.3, 0.7)


def _send_outbound(
    message: ChannelMessage,
    reply: ComposedReply,
    conversation,
    trace_id: str,
) -> bool:
    """Step 19 helper — send the composed reply with retry-3x.

    Returns:
      True if outbound succeeded (or skipped because reply.final_send=False).
      False if all retries exhausted; AdminTask DLQ row is written before return.

    Phase 0 = MAX only. Sprint 7+ generalises by dispatching on
    message.channel — each channel module exposes a sync ``send_message``.
    """

    # Skill set should_send=False (e.g. handoff guard already handled
    # outbound itself, or canned silence). No send, no retry.
    if not reply.final_send or not reply.text:
        return True

    # Only MAX channel in Phase 0.
    if message.channel != "max":
        logger.warning(
            "pipeline.outbound.unsupported_channel channel=%s trace_id=%s",
            message.channel,
            trace_id,
        )
        return True  # Treat as ok — Sprint 7+ adds the channel adapter

    from apps.channels.max.outbound import MaxAPIError, send_message

    last_error: str = ""
    for attempt in range(_OUTBOUND_MAX_ATTEMPTS):
        try:
            send_message(chat_id=message.chat_id, text=reply.text)
            return True
        except MaxAPIError as exc:
            last_error = f"MaxAPIError: {exc}"
            logger.warning(
                "pipeline.outbound.retry attempt=%d/%d trace_id=%s err=%s",
                attempt + 1,
                _OUTBOUND_MAX_ATTEMPTS,
                trace_id,
                exc,
            )
            if attempt < _OUTBOUND_MAX_ATTEMPTS - 1:
                import time

                time.sleep(_OUTBOUND_BACKOFF_SECONDS[attempt])
        except Exception as exc:  # noqa: BLE001 — defensive; never let outbound break turn
            last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("pipeline.outbound.unexpected trace_id=%s", trace_id)
            break  # Non-retriable, don't waste backoff window

    # All attempts exhausted — DLQ via AdminTask.
    _write_outbound_dlq(conversation, reply, trace_id, last_error)
    return False


def _write_outbound_dlq(
    conversation,
    reply: ComposedReply,
    trace_id: str,
    error: str,
) -> None:
    """Permanent outbound failure → AdminTask for operator triage.

    Phase 0 reuses AdminTask.TaskType.MANUAL (no separate enum value) with
    a descriptive reason. Operator sees the task in admin and reaches the
    user out-of-band. Phase 1 may add a dedicated OUTBOUND_FAILED type.
    """

    try:
        from apps.handoff.models import AdminTask
        from apps.handoff.services import create_admin_task

        # Truncate text in case it's huge.
        snippet = reply.text[:200] + ("…" if len(reply.text) > 200 else "")
        create_admin_task(
            conversation,
            task_type=AdminTask.TaskType.MANUAL,
            reason=f"outbound_failed trace_id={trace_id} err={error[:200]} text={snippet!r}",
        )
    except Exception:  # noqa: BLE001 — DLQ is best-effort
        logger.exception(
            "pipeline.outbound.dlq_write_failed trace_id=%s err=%s",
            trace_id,
            error,
        )
