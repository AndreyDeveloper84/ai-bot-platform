"""MAX channel handler — Sprint 2 echo pipeline (DRF-442 / D3).

Bridges the gap between the incoming webhook (parsed by D1) and the
outbound send (D2), persisting both turns to the Conversation domain
(B3) and the short-term memory (C1). Per Sprint 2 plan locked
decision #5: echo-only. No FSM port from legacy `start.py`. AI
Concierge with skill dispatch lands in Sprint 3.

### Pipeline (one call per inbound webhook)

    parse_max_webhook(payload)                   ← D1
        ↓
    resolve_or_create_bot_user(channel="max",    ← A2
                               channel_user_id,
                               chat_id)
        ↓
    resolve_active_conversation(bot_user)        ← B3
        ↓
    record_message(role="user", content=text)    ← B3
    short_term.append(role="user")               ← C1
        ↓
    [reply_text decision per event.text / .attachments]
        ↓
    record_message(role="assistant", content=reply, rendered_text=reply)
    short_term.append(role="assistant")
        ↓
    send_message(chat_id, text=reply)            ← D2

### Reply logic (Sprint 2 echo)

- `/start` (case-sensitive, exact match) → welcome message (ported from
  `legacy_maxbot/texts.py::GREETING_NEW_USER`). Sprint 3 will switch
  this to the AI Concierge welcome flow.
- non-empty text → echo back verbatim.
- attachment-only message (empty text, attachments present) → reply
  "(нечем эхом) 🙂" per plan decision Day-1 open #1.
- everything else (empty text, no attachments) → reply "?"

### Tenant context contract

This handler **does not** enter `tenant_scope` itself. The consumer
loop (Sprint 1 / C3) is responsible for entering tenant_scope +
trace_id_scope from the Redis Stream entry fields *before* dispatching
to handlers. The B3 services raise ValueError if called without a
tenant in scope; that's our loud-failure path for a consumer bug.

### Error contract

- Parser errors (`ParseError`) propagate up — consumer logs + PEL-retains
  the entry. Sprint 3 may downgrade some parse errors to ACK-with-log
  (e.g. unsupported update_type).
- Outbound errors (`MaxAPIError`) propagate up — consumer doesn't ACK
  the entry so the PEL retains it for retry.
- Domain-layer errors (ValueError, CrossTenantError from B3) propagate;
  these are programmer bugs and should crash loudly.

The handler **does not** swallow exceptions. The consumer's
handler_failed flag governs PEL retention; the right place to decide
"retry vs DLQ" is there, not here.

### Two-handler safety contract (#1053 de-drift)

There are two live inbound paths — per-tenant :func:`_handle_max_event_inner`
(skill dispatch) and global :func:`_handle_global_max_event_inner` (discovery).
They evolved separately; #1053 removed the *functional* drift by routing BOTH
through the SAME shared helpers so safety can't diverge:

* :func:`apps.orchestrator.safety.gate.evaluate_inbound` — the single verdict
  source (which phrases short-circuit + the canned reply text). Change the safety
  policy in ONE place.
* :func:`_emit_safety_shortcircuit` — the single PII-safe observability emit.
* :func:`_dispatch_skill_handoff` — the single should_handoff→AdminTask path
  (per-tenant; the global booking handoff mirrors it in
  ``apps.orchestrator.handoff``).

The safety turn is tagged ``action_type="safety_pre_check"`` on BOTH paths
(``record_message`` and ``record_global_message`` both carry it) so a crisis turn
is distinguishable in the Message table regardless of path.

Two divergences are INTENTIONAL (and pinned by
``apps/channels/tests/test_handler_safety_parity.py``): the global tenant-less
path creates NO AdminTask on a red-flag (Variant A, #1076), and only the
per-tenant gate carries the safety HUMAN_HANDOFF barge-guard — the global path
mutes pre-gate via ``global_handoff_muted`` (DRF-1015), which also covers tasks
sitting in a salon's queue. The remaining split — the per-tenant
``record_message`` vs sentinel ``record_global_message`` FUNCTIONS themselves —
is intrinsic to tenant vs tenant-less persistence and is deliberately NOT
merged, but they now emit parity rows (same ``action_type``). The parity test
fails CI if the two paths ever drift on the shared safety verdict, reply, or
marker.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import replace
from typing import Any

from django.conf import settings

from apps.channels.max.global_onboarding import (
    needs_onboarding,
    resolve_welcome_tap,
    run_onboarding_turn,
)
from apps.channels.max.outbound import (
    edit_message_or_send,
    make_inline_keyboard_attachment_rows,
    send_message,
)
from apps.channels.max.parser import CanonicalEvent, ParseError, parse_max_webhook
from apps.channels.max.quick_actions import (
    AI_UNAVAILABLE_TEXT,
    RETRY_CALLBACK,
    STALE_TAP_TEXT,
    ai_unavailable_action_data,
    first_contact_action_data,
    is_stale_tap,
    resolve_tap_text,
)
from apps.channels.max.photo import (
    PhotoDownloadError,
    PhotoTooLargeError,
    download_photo,
    extract_first_photo_url,
    safe_hostname,
)
from apps.conversations.models import Conversation
from apps.conversations.services import (
    record_global_message,
    record_message,
    resolve_active_conversation,
    resolve_active_global_conversation,
)
from apps.events.services import emit
from apps.consent.memory import can_store_green_memory
from apps.handoff.silence import mark_handoff_announced, notify_silence
from apps.identity.services import (
    resolve_or_create_bot_user,
    resolve_or_create_global_bot_user,
)
from apps.identity.services.global_tenant import get_global_bot_tenant
from apps.observability.ai_metrics import record_ai_request
from apps.observability.models import AIRequestMetric
from apps.persona.memory_commands import handle_memory_command
from apps.persona.memory_surface import render_current_personal_context
from apps.persona.voice import SALON_BUSINESS_NAME
from apps.orchestrator.concierge import generate_direct_show_masters_reply
from apps.orchestrator.fast_path import claims_direct_show_masters
from apps.orchestrator.discovery import (
    CALLBACK_DISCOVER_BOOK_PREFIX,
    CATALOG_CALLBACK_PREFIXES,
    CATALOG_STALE_CARD_TEXT,
    CLARIFY_CALLBACK_PREFIX,
    CLARIFY_STALE_TEXT,
    ClarifyOutcome,
    DiscoveryReply,
    execute_catalog_callback,
    execute_clarify_callback,
    resolve_discover_tap,
)
from apps.orchestrator.handoff import (
    BOOKING_CALLBACK_PREFIXES,
    global_handoff_muted,
    handoff_to_booking,
    matches_human_handoff_request,
    route_booking_callback,
    route_global_human_handoff,
    try_continue_booking,
)
from apps.orchestrator.intent_resolution import resolve_and_log_turn_intent
from apps.orchestrator.nutrition_global import (
    resolve_anketa_tap,
    resolve_food_tap,
    resolve_nutri_stop_tap,
    try_handle_structured_nutrition_turn,
)
from apps.nutrition_proactive.optout import try_handle_opt_out, try_handle_surface_stop
from apps.orchestrator.visits import (
    CALLBACK_VISIT_REPEAT_PREFIX,
    VISIT_CALLBACK_PREFIXES,
    route_visit_callback,
    route_visits,
)
from apps.orchestrator.memory import short_term
from apps.orchestrator.memory.personal_context import record_explicit_green_facts
from apps.orchestrator.memory_ask import maybe_weave_question, try_handle_answer
from apps.orchestrator.memory_block import build_concierge_memory_block
from apps.orchestrator.nutrition_context import build_nutrition_context_block
from apps.orchestrator.safety.gate import (
    OUTBOUND_ACTION_TYPE,
    evaluate_inbound,
    guard_outbound,
)
from apps.orchestrator.turn_seam import (
    SURFACE_GLOBAL,
    SURFACE_PER_TENANT,
    TurnContext,
    orchestrate_turn,
    turn_reply_to_skill_result,
)
from apps.skills.booking.lookup import is_personal_booking_lookup
from apps.tools.idempotency import AlreadyClaimed, with_idempotency

logger = logging.getLogger(__name__)


# Welcome message — ported verbatim from
# `legacy_maxbot/texts.py::GREETING_NEW_USER` (running in prod since
# 2026-04). The salon NAME now comes from the one place that owns it
# (apps.persona.voice, DRF-1265) — the same «Формула тела» the FAQ and
# booking skills introduce — instead of a string only this file knew.
# The rest of the wording stays byte-identical to the legacy greeting:
# «массажного салона … в Пензе» is descriptive copy, not an identity
# field, and changing it is a product call. Sprint 3 AI Concierge will
# replace this with personalised welcome flow via tenant.brand_voice persona.
_WELCOME_TEXT = (
    "Здравствуйте! 👋\n\n"
    f"Это бот массажного салона «{SALON_BUSINESS_NAME}» в Пензе.\n"
    "Помогу записаться, расскажу об услугах и отвечу на частые вопросы.\n\n"
    "Выберите раздел:"
)

_FALLBACK_NO_ECHO = "(нечем эхом) 🙂"
_FALLBACK_EMPTY = "?"

# #1047 — user-facing line when a skill escalates to a human but doesn't set its
# own reply_text. Booking's _handoff always sets one («переключаю на менеджера…»),
# so this is only the defensive fallback. Operational copy (low sensitivity vs the
# crisis copy) — founder may tweak.
_HANDOFF_FALLBACK_TEXT = "Передаю ваш вопрос менеджеру — он ответит здесь в ближайшее время."


def _last_assistant_content(history: list[dict[str, Any]] | None) -> str | None:
    """Most recent assistant message text in short-term history, or None.

    Used by the M-B4 «забудь всё» two-step confirmation to detect a pending
    prompt without an extra state store.
    """
    for item in reversed(history or []):
        if item.get("role") == "assistant":
            content = item.get("content")
            return content if isinstance(content, str) else None
    return None


def _last_user_content(
    history: list[dict[str, Any]] | None,
    conversation: Any = None,
) -> str | None:
    """Последняя реплика САМОГО человека, или None.

    Что подставляет «Повторить» с экрана «AI недоступна» (DRF-1348): повтор —
    это «отправь то же самое ещё раз», а не «спроси модель заново», поэтому
    подставляется не запрос к модели, а то, что человек написал.

    Короткая память читается первой (она уже в руках вызывающего, лишнего
    чтения нет), таблица сообщений — запасной путь: у короткой памяти TTL, а
    сбой был как раз тем, из-за чего человек ждал и вернулся к кнопке позже.
    Best-effort: сбой чтения даёт None, и вызывающий отвечает честно вместо
    того, чтобы повторить пустоту.

    Тап по самой кнопке сюда попасть не может: подстановка стоит ДО записи
    входящего хода, поэтому ``cb:retry:last`` в истории не оказывается.
    """
    for item in reversed(history or []):
        if item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                return content
    conversation_id = getattr(conversation, "id", None)
    if conversation_id is None:
        return None
    try:
        from apps.conversations.models import Message

        row = (
            Message.all_tenants.filter(conversation_id=conversation_id, role="user")
            .order_by("-created_at")
            .values_list("content", flat=True)
            .first()
        )
    except Exception:  # noqa: BLE001 — a retry must never break the turn
        logger.exception(
            "channels.max.global.retry_history_probe_failed conversation=%s",
            conversation_id,
        )
        return None
    return row if isinstance(row, str) and row.strip() else None


def _last_clarification_offer(conversation: Any) -> tuple[str, list[str]]:
    """The question + options of the most recent multi-select on this dialog.

    ``("", [])`` when there is none, which every caller treats as "the question
    is gone" rather than guessing.

    **Why the Message row and not the tapped keyboard.** MAX echoes the
    original message back on a callback (``message.body`` — the parser reads
    ``mid`` from it), and it is tempting to read the labels straight off the
    keyboard the person tapped. The repo's own callback fixture
    (``apps/channels/max/tests/test_parser.py:165``) has ``attachments: []`` on
    a body that demonstrably carried an inline keyboard, so that echo is not
    something to build on without a live capture proving otherwise. The
    assistant turn, by contrast, is written by us, on this path, every time.

    **Why this needs no new table.** ``Message.action_data`` has existed since
    Sprint 3 and the renderer already puts the offer there
    (``discovery.render_multiselect_clarification``). Until DRF-1362 the global
    sibling of ``record_message`` simply did not forward the field — the fix
    was to stop dropping it, not to invent a place to keep it.

    Best-effort: a read failure degrades to "no offer", so a database hiccup
    costs the multi-select and never the turn.
    """
    conversation_id = getattr(conversation, "id", None)
    if conversation_id is None:
        return "", []
    try:
        from apps.conversations.models import Message

        rows = (
            Message.all_tenants.filter(conversation_id=conversation_id, role="assistant")
            .order_by("-created_at")
            .values_list("content", "action_data")[:_CLARIFY_LOOKBACK]
        )
        for content, action_data in rows:
            if not isinstance(action_data, dict):
                continue
            block = action_data.get("clarification")
            if not isinstance(block, dict):
                continue
            options = [str(o) for o in (block.get("options") or []) if str(o).strip()]
            if options:
                return (content if isinstance(content, str) else ""), options
    except Exception:  # noqa: BLE001 — a tap must never break the turn
        logger.exception(
            "channels.max.global.clarify_offer_probe_failed conversation=%s",
            conversation_id,
        )
    return "", []


#: How far back to look for the offer a tap answers. A tap normally answers the
#: message directly above it, but a redraw writes an assistant row of its own,
#: so the original can sit several rows up after a few toggles. Bounded so a
#: stale tap from far up the dialog reads as stale instead of silently
#: re-opening a question the conversation has long moved past.
_CLARIFY_LOOKBACK = 12


def _build_attachments(action_data: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    """Pull the channel-agnostic keyboard from ``SkillResult.action_data``.

    Two shapes are accepted, in priority order:

    1. **Platform-canonical envelope** —
       ``action_data["attachments"] = [{"type": "inline_keyboard",
                                        "payload": {"buttons": [...]}}]``.
       The booking skill (B7 / reminder callbacks) emits this shape
       because Telegram's ``_extract_keyboard`` reads it directly (see
       :func:`apps.channels.telegram.handler._extract_keyboard`); we
       must match that contract or booking keyboards silently vanish
       on MAX. ``buttons`` is the channel-agnostic ``[{label, callback}]``
       list which we run through :func:`make_inline_keyboard_attachment_rows`
       (or ``…_attachment`` for the flat-list variant) to get the MAX
       wire shape.

    2. **Flat short-form** — ``action_data["buttons"]`` (or
       ``["button_rows"]``) at the top level. Used by the welcome skill
       and food_scanner where the producer doesn't need to be channel-aware.

    Returns None when neither key is present so the outbound body stays
    exactly as before — guarantees zero regression for skills that don't
    opt in to keyboards.
    """
    if not action_data:
        return None

    # (1) Platform-canonical envelope — booking + reminders.
    envelope_attachments = action_data.get("attachments")
    if isinstance(envelope_attachments, list):
        for att in envelope_attachments:
            if not isinstance(att, dict) or att.get("type") != "inline_keyboard":
                continue
            payload = att.get("payload") or {}
            buttons = payload.get("buttons")
            if not isinstance(buttons, list) or not buttons:
                continue
            # ``buttons`` may be a flat list of {label, callback} dicts OR a
            # pre-shaped 2-D matrix. booking emits the flat form (the
            # `result.pending.keyboard` is a single row); telegram's adapter
            # accepts both, so we mirror.
            if buttons and isinstance(buttons[0], list):
                return [make_inline_keyboard_attachment_rows(buttons)]
            from apps.channels.max.outbound import make_inline_keyboard_attachment

            return [make_inline_keyboard_attachment(buttons, columns=1)]

    # (2) Flat short-form — welcome + food_scanner.
    rows = action_data.get("button_rows")
    if isinstance(rows, list) and rows:
        return [make_inline_keyboard_attachment_rows(rows)]
    buttons = action_data.get("buttons")
    if isinstance(buttons, list) and buttons:
        from apps.channels.max.outbound import make_inline_keyboard_attachment

        columns = action_data.get("button_columns") or 1
        return [make_inline_keyboard_attachment(buttons, columns=int(columns))]
    return None


def _emit_safety_shortcircuit(bot_user: Any, safety: Any, *, is_global: bool) -> None:
    """Emit an observability event when the safety gate short-circuits a turn (#1053).

    PII-safe: only the verdict + match count ship — never the raw user text or the
    matched substrings, so a self-harm / suicide phrase never lands in the
    analytics bus.
    """
    emit(
        "channels.max.safety.pre_check_triggered",
        payload={
            "bot_user_id": str(getattr(bot_user, "id", "")),
            "verdict": safety.verdict,
            "matched_count": len(safety.matched_patterns),
            "is_global_bot": is_global,
        },
    )


def _skill_selected_label(skill_result: Any) -> str:
    """Skill registry name off a dispatch result; ``""`` when there is none.

    Same extraction order as the pipeline / confidence-floor helpers:
    ``meta["skill"]`` first (FAQ sets it explicitly), ``action_type`` as the
    fallback — the dispatcher doesn't expose the skill instance.
    """
    if skill_result is None:
        return ""
    meta = getattr(skill_result, "meta", {}) or {}
    return meta.get("skill") or getattr(skill_result, "action_type", "") or ""


def _record_live_path_metric(
    *,
    bot_user: Any,
    conversation: Any,
    trace_id: str | uuid.UUID | None,
    message_text: str,
    t_start: float,
    outcome: str,
    tenant: Any = None,
    skill_selected: str = "",
    fallback_triggered: bool = False,
) -> None:
    """DRF-1209 step 2 — one ``AIRequestMetric`` row for a live-path turn the
    concierge does NOT meter.

    Covers the two silent families: per-tenant skill-dispatch turns
    (:func:`_handle_max_event_inner`) and the deterministic global branches
    (safety / opt_out / stale_tap / human handoff / visits / onboarding).
    Concierge-covered outcomes — the concierge LLM passes and the DRF-1283
    deterministic show-masters render — are deliberately NOT written here;
    ``concierge._record_concierge_metric`` owns them and a second row would
    double-count the same inbound message.

    Field shape mirrors the concierge writer exactly: uuid5 fallback for a
    non-UUID trace id (keeps log grep and ``request_id`` correlated), and the
    non-LLM shape for these model-less outcomes — ``llm_pass_index=None``,
    NULL tokens / cost (the schema's documented «no LLM call» encoding, NOT
    zeros, which would drag AVG(cost) toward zero). ``tenant=None`` means the
    tenant-less global path and resolves the ``global_bot`` sentinel lazily,
    exactly like the concierge rows.

    Gated by ``LIVE_PATH_AI_METRIC_ENABLED`` (default OFF): flag off = zero
    new rows, zero extra DB work. Best-effort, mirroring
    ``pipeline._safe_emit_ai_request_metric`` / the concierge writer:
    observability must never crash the turn — failures log WARN with
    trace_id + outcome.
    """
    if not getattr(settings, "LIVE_PATH_AI_METRIC_ENABLED", False):
        return
    try:
        latency_total_ms = int((time.monotonic() - t_start) * 1000)
        try:
            request_uuid = uuid.UUID(str(trace_id))
        except (ValueError, TypeError, AttributeError):
            # Same deterministic fallback as pipeline / concierge (see
            # pipeline._safe_emit_ai_request_metric for the rationale).
            request_uuid = uuid.uuid5(
                uuid.NAMESPACE_DNS, str(trace_id) if trace_id else "live-no-trace"
            )

        record_ai_request(
            tenant=tenant if tenant is not None else get_global_bot_tenant(),
            bot_user=bot_user,
            conversation=conversation,
            request_id=request_uuid,
            message_text_length=len(message_text),
            skill_selected=skill_selected,
            fallback_triggered=fallback_triggered,
            latency_total_ms=latency_total_ms,
            outcome=outcome,
        )
    except Exception as emit_exc:  # noqa: BLE001 — observability never crashes the turn
        logger.warning(
            "channels.max.handler.ai_metric_emit_failed trace=%s outcome=%s err=%s",
            trace_id,
            outcome,
            emit_exc,
        )


def _capture_live_replay(
    *,
    trace_id: str | uuid.UUID | None,
    event: CanonicalEvent,
    surface: str,
    branch: str,
    pre_verdict: str,
    post_verdict: str,
    reply_text: str,
    skill_name: str = "",
    keyboard_size: int = 0,
) -> None:
    """DRF-1209 step 18 — one ``ReplayTrace`` row for a live-path turn.

    Until now ``apps.replay.recorder`` was called only by the DEPRECATED
    ``apps.orchestrator.pipeline.turn`` and the offline replay runner — the
    path that actually answers people wrote no traces. This helper ports the
    SAME recorder onto the live handler: same sampling gate
    (``REPLAY_SAMPLE_RATE_*``, decided inside the recorder), same ``regex_v2``
    redaction before persist, same swallow-everything contract.

    The six pipeline stages do not exist on the live path, so the snapshots
    are built from what the live path actually has — inbound, routing (which
    branch / skill answered), pre-check verdict, the final reply, and the
    outbound guard verdict — in the field shape of the pipeline's
    ``_replay_capture`` steps so existing ReplayTrace readers parse them.
    Live rows carry ``source: "live_path"`` + ``surface`` on the inbound
    payload to stay distinguishable from pipeline-captured rows.

    The global path runs at ``current_tenant()=None`` BY DESIGN (the
    CrossTenantError invariant), and the recorder skips tenant-less captures
    — so global rows are parked under the ``global_bot`` sentinel tenant for
    the duration of the capture call only, exactly like the concierge
    ``AIRequestMetric`` rows. The scope is entered around the recorder call
    and nowhere else; the tenant-less invariant of the surrounding handler
    is untouched.

    Gated by ``REPLAY_LIVE_CAPTURE_ENABLED`` (default OFF): flag off = zero
    new rows, zero extra DB work. Best-effort, mirroring
    ``_record_live_path_metric``: observability must never crash the turn —
    failures log WARN with trace_id + branch.
    """
    if not getattr(settings, "REPLAY_LIVE_CAPTURE_ENABLED", False):
        return
    try:
        from apps.replay.recorder import capture as recorder_capture
        from apps.tenancy.context import tenant_scope

        steps = [
            {
                "step": "inbound",
                "payload": {
                    "text": event.text,
                    "channel": event.channel,
                    "channel_user_id": event.channel_user_id,
                    # Live-path marker — distinguishes these rows from the
                    # deprecated pipeline's captures without a schema change.
                    "source": "live_path",
                    "surface": surface,
                },
            },
            {
                "step": "routing",
                "payload": {"branch": branch, "skill": skill_name},
            },
            {
                "step": "pre_check",
                "payload": {"verdict": pre_verdict},
            },
            {
                "step": "post_check",
                "payload": {"verdict": post_verdict},
            },
            {
                "step": "composer",
                "payload": {
                    "final_text": reply_text,
                    "safety_revised": post_verdict == "block",
                    "keyboard_size": keyboard_size,
                },
            },
        ]
        trace = str(trace_id) if trace_id else ""
        if surface == "max_global":
            with tenant_scope(get_global_bot_tenant()):
                recorder_capture(trace, steps)
        else:
            recorder_capture(trace, steps)
    except Exception as capture_exc:  # noqa: BLE001 — observability never crashes the turn
        logger.warning(
            "channels.max.handler.replay_capture_failed trace=%s branch=%s err=%s",
            trace_id,
            branch,
            capture_exc,
        )


def _deliver_crisis_reply(
    *,
    chat_id: str,
    text: str,
    bot_user: Any,
    trace_id: str | uuid.UUID | None,
    is_global: bool,
    attachments: list[dict[str, Any]] | None = None,
) -> None:
    """Send a safety/crisis reply, alerting LOUDLY if delivery fails (#1082).

    A crisis reply that fails to send is categorically worse than a normal one:
    ``with_idempotency`` has already claimed the key, so a PEL retry hits
    ``AlreadyClaimed`` and never resends — a person in crisis silently gets
    nothing, while the DB shows a «delivered» reply. We can't guarantee delivery
    here, but we MUST make the failure loud so ops follow up out-of-band.

    On failure: a high-priority ERROR log (the durable, alerting-hooked signal —
    survives even if the re-raised exception rolls a surrounding transaction
    back) plus a distinct ``channels.max.safety.crisis_delivery_failed`` event
    (analytics; separate from the PII-safe ``pre_check_triggered``). Both are
    PII-safe — never the crisis text or the matched phrase. The exception is
    re-raised: propagation / idempotency semantics are unchanged, the alert is
    the only addition.
    """
    try:
        send_message(chat_id=chat_id, text=text, attachments=attachments)
    except Exception:
        logger.error(
            "channels.max.safety.crisis_delivery_failed bot_user=%s is_global=%s trace=%s",
            getattr(bot_user, "id", "?"),
            is_global,
            trace_id,
        )
        try:
            emit(
                "channels.max.safety.crisis_delivery_failed",
                payload={
                    "bot_user_id": str(getattr(bot_user, "id", "")),
                    "is_global_bot": is_global,
                    "trace_id": str(trace_id) if trace_id else "",
                },
            )
        except Exception:  # noqa: BLE001 — the alert event must not mask the send failure
            logger.exception("channels.max.safety.crisis_delivery_alert_emit_failed")
        raise


def _confidence_floor_reason(skill_result: Any) -> str:
    """Diagnostic slug when a skill's confidence is below the configured
    threshold; empty string otherwise (DRF-1209 — pipeline step 10.5 ported
    to the live per-tenant path).

    Ported 1:1 from the DEPRECATED ``apps.orchestrator.pipeline`` helper of
    the same name: per-skill threshold via
    ``settings.SKILL_CONFIDENCE_HANDOFF_THRESHOLD`` (explicit ``None``
    disables enforcement for that skill), falling back to the global
    ``settings.AI_CONFIDENCE_HANDOFF_THRESHOLD``; the skill name comes from
    ``meta["skill"]`` with fallback to ``action_type``; ``confidence=None``
    (deterministic skills) skips enforcement. The returned slug keeps the
    pipeline's contract format
    ``pipeline_confidence_floor(confidence=X, threshold=Y)`` — ops queries
    may already match on it.

    The whole check is gated by ``SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED``
    (default OFF): flag off → always ``""`` → byte-identical behaviour.
    """
    if skill_result is None:
        return ""
    if not getattr(settings, "SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED", False):
        return ""
    confidence = getattr(skill_result, "confidence", None)
    if confidence is None:
        return ""

    meta = getattr(skill_result, "meta", {}) or {}
    skill_name = meta.get("skill") or getattr(skill_result, "action_type", "") or ""

    per_skill = getattr(settings, "SKILL_CONFIDENCE_HANDOFF_THRESHOLD", {}) or {}
    if skill_name in per_skill:
        threshold = per_skill[skill_name]
        if threshold is None:
            # Explicit disable for this skill.
            return ""
    else:
        threshold = getattr(settings, "AI_CONFIDENCE_HANDOFF_THRESHOLD", 0.5)

    try:
        threshold_f = float(threshold)
    except (TypeError, ValueError):
        return ""

    if confidence >= threshold_f:
        return ""

    return f"pipeline_confidence_floor(confidence={confidence:.2f}, threshold={threshold_f:.2f})"


def _dispatch_skill_handoff(
    conversation: Any,
    skill_result: Any,
    chat_id: str,
    trace_id: str | uuid.UUID | None,
    *,
    reason_override: str | None = None,
    handoff_text_override: str | None = None,
) -> str:
    """Escalate to a human when a skill returns ``should_handoff`` (#1047).

    The MAX handler previously ignored ``SkillResult.should_handoff``, so booking's
    ``_handoff`` (and any KB-driven escalation) wrote an audit line + a «переключаю
    на менеджера» reply but NEVER created an AdminTask or flipped state — the
    operator got no task and the bot kept answering. This creates the AdminTask
    atomically (state → HUMAN_HANDOFF + emit + audit, via
    ``handoff.services.create_admin_task``), sends the skill's user-facing line
    once, then returns; the D3 silence path (dispatch returns ``should_send=False``
    at ``state == HUMAN_HANDOFF``) mutes every subsequent turn until an operator
    resolves the task.

    DRF-1209: the keyword-only overrides let the confidence-floor enforcement
    (pipeline step 10.5) feed THIS SAME escalation path instead of building a
    parallel one — ``reason_override`` carries the pipeline-format diagnostic
    slug, ``handoff_text_override`` forces the canned line when the skill never
    asked for handoff (its own answer must not reach the user).

    Requires ``current_tenant()`` — the consumer enters ``tenant_scope`` before
    dispatching to this handler (module contract), which ``create_admin_task``
    needs.

    Returns the exact line that was sent (post-guard), so the caller's
    observability (DRF-1209 replay capture) records what the person
    actually read rather than re-deriving the pre-guard text.
    """
    from apps.handoff.models import AdminTask
    from apps.handoff.services import create_admin_task

    reason = (
        reason_override
        if reason_override is not None
        else (skill_result.handoff_reason or "skill_requested_handoff")
    )
    create_admin_task(conversation, task_type=AdminTask.TaskType.HANDOFF, reason=reason)

    handoff_text = (
        handoff_text_override
        if handoff_text_override is not None
        else (skill_result.reply_text or _HANDOFF_FALLBACK_TEXT)
    )
    # DRF-1210 — an escalation line is still a line a person reads, and the
    # skills that set one are free to compose it from model output. Guarded
    # like every other outbound on this surface; the AdminTask above is
    # already created either way, so a block loses the sentence, not the
    # escalation.
    _guarded = guard_outbound(handoff_text, surface="max", trace_id=trace_id)
    if _guarded.blocked:
        handoff_text = _guarded.text
    record_message(
        conversation,
        role="assistant",
        content=handoff_text,
        rendered_text=handoff_text,
        action_type=skill_result.action_type or "handoff",
        trace_id=trace_id,
    )
    short_term.append(conversation.id, role="assistant", content=handoff_text)
    # DRF-1486 — этот диалог говорит человеку, что подключает сотрудника, и
    # факт этого решает, ЧТО человек прочитает, когда молчание включится на
    # его следующем сообщении.
    #
    # ДО отправки, ровно как ``record_message`` выше и по той же причине:
    # ``send_message`` пробрасывает MaxAPIError наверх, автоматического
    # ретрая нет (запись остаётся в PEL до ручного XCLAIM). Записать после
    # отправки значило бы, что упавший ход оставляет диалог с меткой
    # «здесь ничего не говорили» — и на следующем сообщении человек прочитал
    # бы «вы просили связать вас с сотрудником в ДРУГОМ нашем чате» ровно в
    # том чате, где он и спрашивал. Раньше такой сбой давал молчание; врать
    # хуже, чем молчать.
    mark_handoff_announced(conversation=conversation, chat_id=chat_id)
    send_message(
        chat_id=chat_id,
        text=handoff_text,
        attachments=_build_attachments(skill_result.action_data),
    )
    emit(
        "channels.max.handler.handoff",
        payload={"conversation_id": str(conversation.id), "reason": reason[:200]},
    )
    logger.info(
        "channels.max.handler.handoff conversation=%s reason=%s",
        conversation.id,
        reason,
    )
    return handoff_text


def _reply_kind(event: CanonicalEvent, skill_result: Any, reply_text: str) -> str:
    """Label for the ``channels.max.outbound.sent`` analytics event.

    Prefers the responding skill's own ``meta["reply_kind"]`` — every skill
    that cares already sets one (``welcome``, ``menu_fallback``,
    ``menu_help``, ``welcome_ask_prompt``, …). The Sprint-2 positional
    guess below it labelled EVERY non-empty text turn ``"echo"``, which
    since DRF-963 is simply wrong: the honest fallback, a booking reply and
    a real echo all reported the same kind, so «how often does the bot
    still miss?» — the pilot's headline conversational metric — was not
    answerable from the bus.

    **This widens the vocabulary for every surface, not just menu.** Most
    skills already set ``meta["reply_kind"]`` — food_scanner, water,
    nutrition_anketa, health_screening, cross_domain, food_clarify,
    food_correction and the welcome family all do — so a photo turn that
    used to report ``"no_echo"`` now reports ``"food_scanner_card"``, and a
    ``cb:welcome:consent_yes`` tap that used to report ``"echo"`` now
    reports ``"welcome_s5_first_action"``. That is the intent (the old
    labels were positional guesses, not facts), but a dashboard counting
    the ``echo`` / ``no_echo`` share will show a step change at deploy.

    The positional branch below is now reached only when the responding
    skill sets no meta at all, or on the registry-empty fallback path.
    """
    skill_kind = (
        (getattr(skill_result, "meta", None) or {}).get("reply_kind") if skill_result else ""
    )
    if skill_kind:
        return str(skill_kind)
    if reply_text == _WELCOME_TEXT:
        return "welcome"
    if event.text.strip():
        return "echo"
    if event.attachments:
        return "no_echo"
    return "empty_prompt"


def _echo_text(event: CanonicalEvent) -> str:
    """Decide the assistant reply text for a Sprint 2 echo turn."""

    text = event.text.strip()
    if text == "/start":
        return _WELCOME_TEXT
    if text:
        # Echo verbatim.
        return event.text
    if event.attachments:
        return _FALLBACK_NO_ECHO
    return _FALLBACK_EMPTY


def handle_max_event(payload: dict, trace_id: str | uuid.UUID | None = None) -> None:
    """Process one MAX webhook payload end-to-end.

    Called by the worker consumer (Sprint 1 / C3) after it enters
    `tenant_scope` + `trace_id_scope` from the Redis Stream entry.

    Args:
      payload: raw MAX webhook JSON (already parsed from request body
               by D4's view).
      trace_id: optional explicit trace identifier (the consumer
                normally sets `current_trace_id()` ContextVar; this
                arg is for direct-call testing).

    Idempotency (Sprint 2.5 H4):
      Wrapped in `with_idempotency` keyed on
      `webhook:max:{channel_message_id}`. Under PEL retries (consumer
      crash / handler exception), the second invocation hits
      `AlreadyClaimed` and short-circuits — preventing duplicate
      Message rows, duplicate memory appends, and duplicate outbound
      sends. The first invocation's outbound MaxAPIError still
      propagates (retry policy on MAX API side, not ours).
    """

    # Tolerate-and-skip for unsupported update types: parser raises
    # ParseError, we log + emit an event + return cleanly. This prevents
    # PEL retry-storms when MAX delivers a lifecycle update we don't
    # parse yet (e.g. bot_started, message_edited). Dev incident
    # 2026-05-21: bot_started poisoned the PEL because handler raised
    # and consumer didn't ACK — bot went silent for the user.
    try:
        event = parse_max_webhook(payload)
    except ParseError as exc:
        logger.info(
            "channels.max.handler.skipped_unsupported update_type=%r reason=%s",
            (payload or {}).get("update_type") if isinstance(payload, dict) else None,
            exc,
        )
        emit(
            "channels.max.handler.skipped",
            payload={
                "update_type": (payload or {}).get("update_type")
                if isinstance(payload, dict)
                else None,
                "reason": str(exc)[:200],
            },
        )
        return

    logger.info(
        "channels.max.handler.received channel_user_id=%s text_len=%d attachments=%d",
        event.channel_user_id,
        len(event.text),
        len(event.attachments),
    )

    # Callback events carry their own unique id (callback_id) — distinct
    # from any message id. Key off that so a button-tap retry collapses
    # cleanly with the original tap, and a button-tap doesn't collide
    # with the bot's preceding message (which shares the message_id the
    # button was attached to).
    callback_id = (event.raw or {}).get("callback_id", "") if isinstance(event.raw, dict) else ""
    if callback_id:
        idempotency_key = f"webhook:max:callback:{callback_id}"
    else:
        idempotency_key = f"webhook:max:{event.channel_message_id or event.channel_user_id}"
    try:
        with with_idempotency(idempotency_key, ttl_seconds=86_400):
            _handle_max_event_inner(event, trace_id)
    except AlreadyClaimed:
        logger.info(
            "channels.max.handler.dedup_short_circuit channel_message_id=%s",
            event.channel_message_id,
        )
        emit(
            "channels.max.handler.dedup",
            payload={
                "channel_message_id": event.channel_message_id,
                "idempotency_key": idempotency_key,
            },
        )
        return


def handle_global_max_event(payload: dict, trace_id: str | uuid.UUID | None = None) -> None:
    """Process one MAX webhook for the nationwide GLOBAL (tenant-less) bot.

    #1019 + #1026 / EPIC #1014. The sibling of :func:`handle_max_event` for the
    legacy per-tenant path. Called by ``GlobalMaxHandler(requires_tenant=False)``
    after the consumer enters ``trace_id_scope`` + ``tenant_scope(None)`` —
    discovery runs at ``current_tenant()=None`` and a tenant is selected only at
    booking.

    Pipeline (#1026 — mirrors the per-tenant handler, tenant-less):
      parse → resolve global BotUser (sentinel) → resolve the global
      Conversation (sentinel) → record the user turn → generate a discovery
      reply through the AI runtime (``apps.orchestrator.discovery`` →
      ``apps.llm.router`` with the frozen ``AYLA_MARKETPLACE_VOICE``) → record
      the assistant turn → send to MAX.

    Tenant-safety: persistence is sentinel-scoped via the ``*_global_*``
    conversation services (``all_tenants`` + explicit ``tenant=sentinel``), so
    ``current_tenant()`` stays ``None`` throughout and any commercial/catalog
    read here still raises ``CrossTenantError`` (the invariant). This handler
    performs NO commercial reads. Marketplace ``show_masters`` + the
    ``tenant_scope(master.tenant)`` booking handoff remain the seam for P3
    (#1020) / P0 (#1016/#1017). Do NOT call the per-tenant
    ``resolve_active_conversation`` / ``record_message`` here.
    """

    # Tolerate-and-skip unsupported update types (same contract as the
    # per-tenant handler) so the PEL doesn't retry-storm on lifecycle updates.
    try:
        event = parse_max_webhook(payload)
    except ParseError as exc:
        logger.info(
            "channels.max.global.skipped_unsupported update_type=%r reason=%s",
            (payload or {}).get("update_type") if isinstance(payload, dict) else None,
            exc,
        )
        return

    # Idempotency — mirror the per-tenant handler so PEL retries (consumer crash
    # / handler exception) don't double-persist or double-send the discovery turn.
    callback_id = (event.raw or {}).get("callback_id", "") if isinstance(event.raw, dict) else ""
    if callback_id:
        idempotency_key = f"webhook:max_global:callback:{callback_id}"
    else:
        idempotency_key = f"webhook:max_global:{event.channel_message_id or event.channel_user_id}"
    try:
        with with_idempotency(idempotency_key, ttl_seconds=86_400):
            _handle_global_max_event_inner(event, trace_id)
    except AlreadyClaimed:
        logger.info(
            "channels.max.global.dedup_short_circuit channel_message_id=%s",
            event.channel_message_id,
        )
        emit(
            "channels.max.global.dedup",
            payload={
                "channel_message_id": event.channel_message_id,
                "idempotency_key": idempotency_key,
            },
        )
        return


def _handle_global_max_event_inner(event: CanonicalEvent, trace_id: str | uuid.UUID | None) -> None:
    """Inner tenant-less discovery pipeline — parse-already-done. Side-effects only."""

    # DRF-1487 — вниз, за развилку молчания, уехало «печатает…», и только оно
    # (см. ниже, сразу после ``global_handoff_muted``). «Прочитано» осталось
    # здесь: у двух индикаторов разная семантика, и разъехаться они обязаны
    # именно по ней. ``mark_seen`` — констатация: сообщение дошло и его
    # увидели; это правда даже тогда, когда отвечать будет человек, и это
    # единственная обратная связь, которая у клиента 04.09 вообще была.
    # ``typing_on`` — обещание ответа, и вот его-то и нельзя давать тому,
    # кому бот не ответит. DRF-1348 поставил на эту строку оба; DRF-1487
    # оставляет здесь честный из них.
    if event.chat_id:
        from apps.channels.max.outbound import send_chat_action

        send_chat_action(chat_id=event.chat_id, action="mark_seen")

    bot_user = resolve_or_create_global_bot_user(
        channel=event.channel,
        channel_user_id=event.channel_user_id,
        chat_id=event.chat_id,
    )
    conversation = resolve_active_global_conversation(bot_user)
    assert conversation is not None  # noqa: S101 — create_if_missing=True never returns None

    # DRF-1209 step 2 — turn clock for the AIRequestMetric row (the flag
    # check itself lives inside _record_live_path_metric).
    t_start = time.monotonic()

    # Prior short-term history (before this turn) feeds the discovery prompt.
    history = short_term.recall(conversation.id)

    # DRF-1348 / DRF-1051 — тап становится сообщением ДО всего остального.
    #
    # Макет C01, блок ВАЖНО: «Нажатие на Quick Action вставляет текст в
    # composer и отправляет его по тому же pipeline, что и свободный текст.
    # Нет отдельных команд и сценариев». На MAX буквально «вставить в
    # composer» нельзя — payload кнопки приходит тем же полем, что и
    # набранный текст, — поэтому «тот же pipeline» строится подстановкой
    # здесь, ВЫШЕ и записи сообщения, и первой развилки лестницы. Ниже по
    # течению тап отличить не от чего: истории, safety, быстрой ветке и
    # консьержу достаётся ровно та строка, которую человек мог набрать сам.
    #
    # Три семейства, и только три (см. ``quick_actions.resolve_tap_text``):
    # чипы C01, главное меню (DRF-1051 — сегодня его тап уезжает в модель
    # сырым payload'ом) и «Повторить». Колбэки, несущие id — ``cb:discover:``,
    # ``cb:catalog:``, ``cb:book:``, ``cb:welcome:``, ``cb:visit:`` — сюда не
    # попадают: у них свои ветки ниже, и подставлять им текст было бы
    # выдумыванием реплики за человека.
    #
    # Побочно закрывает класс DRF-990 для этих кнопок: в историю глобального
    # диалога ложится фраза, а не «cb:…», который модель охотно толкует.
    stale_tap = False
    tap_text = resolve_tap_text(
        event.text,
        last_user_text=(
            _last_user_content(history, conversation)
            if (event.text or "").strip() == RETRY_CALLBACK
            else None
        ),
    )
    if tap_text is not None:
        event = replace(event, text=tap_text)
    elif is_stale_tap(event.text):
        # Кнопка была настоящая, но фразы за ней уже нет (снятый чип, повтор
        # без истории). Отдать её payload модели нельзя — это и есть дефект
        # DRF-1051; выдумать за человека фразу — хуже.
        stale_tap = True

    # DRF-988 — post-handoff booking taps are NOT chat text: they route into
    # tenant T's booking pipeline (branch 2.5 below) and must not pollute the
    # global dialog history that grounds the concierge LLM (raw cb:book:*
    # payloads in history provoked hallucinated replies, e.g. the «2026 год»
    # refusal). Skip user-turn persistence for them; the assistant reply is
    # still recorded as usual.
    is_booking_callback = event.text.startswith(BOOKING_CALLBACK_PREFIXES)
    # DRF-1304 — the catalog chips are the same class of event for the same
    # reason: a tap is not something the person said, and «cb:catalog:services:
    # {uuid}» sitting in history is exactly the raw payload DRF-988 found the
    # model happy to interpret. The assistant reply (the salon/service list) is
    # still recorded, so the next turn keeps the grounding that matters.
    is_catalog_callback = event.text.startswith(CATALOG_CALLBACK_PREFIXES)

    # DRF-1362 — a multi-select tap, resolved ONCE here so the offer is read
    # from the database once no matter which way the tap goes.
    #
    # «Продолжить» is the one clarify tap that is not a redraw: it IS the
    # person's answer, so it re-enters the turn as ordinary text and every
    # branch below sees «маникюр, стрижка» rather than a `cb:` payload. That
    # is the established move on this surface — ``resolve_tap_text`` just
    # above does the same thing for the same reason — and it is what makes
    # submit a RE-RESOLUTION: the accumulated choice is answered by the same
    # machinery that answers anything a person says, not by a second parallel
    # path that could disagree with it.
    #
    # Toggles and «Ни один вариант» keep their payload and are answered by the
    # branch below. A payload that matched the prefix but decoded to nothing
    # is NOT allowed to fall through — a raw `cb:clarify:…` string reaching
    # the concierge is precisely the DRF-988 defect.
    clarify_outcome = None
    if event.text.startswith(CLARIFY_CALLBACK_PREFIX):
        _clarify_question, _clarify_options = _last_clarification_offer(conversation)
        clarify_outcome = execute_clarify_callback(event.text, _clarify_options, _clarify_question)
        if clarify_outcome is not None and clarify_outcome.submit_text:
            event = replace(event, text=clarify_outcome.submit_text)
            clarify_outcome = None
        elif clarify_outcome is None:
            clarify_outcome = ClarifyOutcome(reply=DiscoveryReply(text=CLARIFY_STALE_TEXT))

    # A submitted answer no longer starts with the prefix, so it is persisted
    # as the user turn it now is; a redraw tap still does not reach history.
    is_clarify_redraw_tap = event.text.startswith(CLARIFY_CALLBACK_PREFIX)

    # DRF-990 — the anketa taps. Same defect class as DRF-988/DRF-1304, and
    # NOT closed by DRF-1268: that one routes `cb:anketa:*` deterministically
    # in the CURRENT turn, while history is what the concierge reads on the
    # NEXT one — where «cb:anketa:choice:gender:female» under role=user reads
    # as something the person typed at it.
    #
    # But the answer is not `cb:catalog:` either, so it does not get
    # `cb:catalog:`'s treatment. Tapping «Женский» IS the person saying
    # something about themselves, and the anketa's own text steps (age,
    # height, weight) are typed and land in history always — dropping only
    # the two keyboard steps would leave a record where «30» is present and
    # the gender is missing. So a choice tap is REWRITTEN to the phrase it
    # is («Женский», «Похудеть») and kept; `start`/`edit` are navigation and
    # are dropped like their catalog neighbours.
    #
    # The rewrite happens HERE and not in ``resolve_tap_text`` above on
    # purpose: that one replaces ``event.text`` for the whole turn, and the
    # anketa is ROUTED by its payload (``NutritionAnketaSkill.matches``,
    # ``_STRUCTURED_CALLBACK_PREFIXES``) — substituting the phrase there
    # would break the flow the golden fixtures replay. Routing keeps the
    # payload; history gets the phrase.
    anketa_tap = resolve_anketa_tap(event.text)

    # DRF-990, продолжение — те же две правки для остальных семейств.
    #
    # Замер по всем формам `cb:`, какие репозиторий вообще упоминает, показал,
    # что гейт ниже — это СПИСОК ИСКЛЮЧЕНИЙ, а не правило: мимо истории идут
    # ровно перечисленные семейства, всё остальное пишется дословно. Здесь
    # закрываются те, что доказуемо доезжают до этого пути.
    #
    # ФРАЗА (тап — высказывание человека, у семейства есть НАБРАННЫЕ шаги,
    # которые в историю попадают всегда, и молчание оставило бы половину
    # разговора):
    #
    #   `cb:welcome:*` — приветствие и 152-ФЗ согласие. Шире анкеты: её
    #       открывают не все, приветствие проходит каждый новый пользователь.
    #       Вход НАБИРАЮТ («/start» или свободная фраза), дальше только тапы.
    #   `cb:food:*` — буквальный близнец анкеты (тот же
    #       `_STRUCTURED_CALLBACK_PREFIXES`). Еду называют текстом и уточняют
    #       текстом; «✅ В дневник» / «❌ Не то» — подтверждение и поправка.
    #
    # Доводы целиком — в комментариях у самих резолверов
    # (`global_onboarding.resolve_welcome_tap`, `nutrition_global.resolve_food_tap`).
    # Как и у анкеты, перевод стоит ЗДЕСЬ, а не в `resolve_tap_text` выше: оба
    # семейства маршрутизируются ПО payload'у (`WelcomeSkill.matches`,
    # `needs_onboarding`, `try_handle_structured_nutrition_turn`), и подмена
    # `event.text` сломала бы и опрос согласия, и дневник еды.
    welcome_tap = resolve_welcome_tap(event.text)
    food_tap = resolve_food_tap(event.text)

    # DRF-1468 — тап «Не присылать» (`cb:nutri:stop:*`). МОЛЧАНИЕ по той же
    # причине, что у `cb:catalog:*` и навигации анкеты: метка одна на все
    # поверхности, фразы за тапом нет, а сырой payload в истории — дефект
    # DRF-988. Ход остаётся виден по ответу-подтверждению бота.
    nutri_stop_tap = resolve_nutri_stop_tap(event.text)

    # МОЛЧАНИЕ — то же решение и по той же причине, что у `cb:book:*`
    # (DRF-988) и `cb:catalog:*` (DRF-1304): текст несёт id карточки, которую
    # бот сам нарисовал, а не слова человека.
    #
    #   `cb:visit:*` — карточка визита и «Записаться ещё». Ветка в лестнице
    #       есть с самого начала, в гейте персистенса семейства не было.
    #
    # Семейство `cb:discover:*` — то же решение, но оно переехало ниже, из
    # `startswith` в резолвер по форме; довод там же.
    is_visit_callback = event.text.startswith(VISIT_CALLBACK_PREFIXES)

    # DRF-990, третий заход — СЕМЕЙСТВО `cb:discover:`, а не глагол `book:`.
    #
    # Боевой замер пилота 30.08: 55 из 68 сырых строк нажатия в истории — это
    # `cb:discover`. Прогон пятнадцати форм через этот самый хендлер показал,
    # что #1329 закрыл глагол `book:` ЦЕЛИКОМ (все арности, включая битый id,
    # перебор сегментов и пустой хвост), а мимо гейта идёт ровно то, что
    # глаголом `book:` не является: `cb:discover:`, `cb:discover` и любой
    # глагол семейства, которого сегодня ещё нет. То есть строка `startswith
    # (CALLBACK_DISCOVER_BOOK_PREFIX)` сторожила ГЛАГОЛ — а решение «тап по
    # карточке репликой не является» принято про СЕМЕЙСТВО.
    #
    # Заменено на резолвер по ФОРМЕ — тем же механизмом, что у анкеты,
    # приветствия и еды, и по той же причине: `startswith` съел бы реплику
    # человека, который набрал «cb:discover: …» руками. Довод «молчание», а
    # не «фраза», целиком в комментарии у самого резолвера
    # (`apps.orchestrator.discovery.resolve_discover_tap`).
    #
    # Устройство гейта — список исключений, а не правило: форма, которую ни
    # одно семейство не разобрало, по-прежнему пишется дословно. Это
    # отдельный открытый вопрос, и здесь чинится частный случай.
    discover_tap = resolve_discover_tap(event.text)

    # `stale_tap` — кнопка была настоящая, но фразы за ней уже нет (снятый
    # чип `cb:qa:{слаг}`, «Повторить» без истории). DRF-1051 закрыл для неё
    # МАРШРУТИЗАЦИЮ — модель payload'а не видит, — но не персистенс, и сырая
    # строка продолжала ложиться в историю. Фразы у снятой кнопки нет по
    # определению («выдумать за человека фразу хуже» — `quick_actions`), так
    # что единственный честный исход тот же, что и у нераспознанного тапа
    # анкеты: в историю не идёт ничего. Экран «кнопка устарела» бот при этом
    # отвечает и записывает, так что ход в истории виден.

    # Persist + remember the inbound turn (sentinel-scoped, current_tenant()=None).
    #
    # Аннотация здесь — не формальность перед mypy, а предмет самой правки.
    # `str | None` говорит ровно то, что решают резолверы выше: `str` — «человек
    # это сказал, и вот какими словами», `None` — «человек этим не сказал
    # НИЧЕГО» (навигация, снятая кнопка), и тогда в историю не идёт ни строки.
    # Сузить до `str` — приведением, `cast` или `type: ignore` — значило бы
    # заявить в типе, что молчания не бывает, тогда как молчание тут половина
    # решения; следующий читатель обязан увидеть его здесь, а не вычитывать из
    # ветки `inbound_history_text is None` десятью строками ниже.
    inbound_history_text: str | None = event.text
    for tap in (anketa_tap, welcome_tap, food_tap, discover_tap, nutri_stop_tap):
        if tap is None:
            # «Это не тап моего семейства» — резолвер пропускает ход дальше и
            # не трогает ни текст, ни персистенс.
            continue
        # Претендовать на payload может ровно один резолвер: семейства
        # различаются префиксом, а форма проверяется строго. Поэтому первый же
        # разбор — окончательный, и его `history_text` (фраза ИЛИ None) и есть
        # ответ на вопрос «чем этот тап был как реплика».
        inbound_history_text = tap.history_text
        break
    if (
        is_booking_callback
        or is_catalog_callback
        or is_clarify_redraw_tap
        or is_visit_callback
        or stale_tap
        or inbound_history_text is None
    ):
        user_msg = None
    else:
        user_msg = record_global_message(
            conversation, role="user", content=inbound_history_text, trace_id=trace_id
        )
        short_term.append(conversation.id, role="user", content=inbound_history_text)

    logger.info(
        "channels.max.global.received bot_user=%s conversation=%s text_len=%d",
        bot_user.id,
        conversation.id,
        len(event.text),
    )
    emit(
        "channels.max.global.received",
        payload={
            "bot_user_id": str(bot_user.id),
            "conversation_id": str(conversation.id),
            "channel": event.channel,
            "text_len": len(event.text),
            "attachments": len(event.attachments),
            "is_global_bot": True,
        },
    )

    # DRF-1015 — human-handoff mute on the global path. While an operator
    # drives ANY of this user's dialogs (the global one under a platform-queue
    # task, or a salon's under a tenant task), the bot stays silent here too:
    # the user turn is already recorded above (parity with the per-tenant
    # path), but nothing is sent. The mute lifts on its own when the operator
    # closes the task (DRF-980) — no linkage bookkeeping.
    if global_handoff_muted(
        conversation=conversation,
        channel=event.channel,
        channel_user_id=event.channel_user_id,
    ):
        logger.info(
            "channels.max.global.silenced_by_handoff conversation=%s",
            conversation.id,
        )
        # DRF-1486 — молчание объясняется ровно один раз за эпизод. Само
        # молчание правильное и остаётся (оператор и бот не говорят
        # одновременно); чего человеку не хватало 04.09 — фразы о том, ЧТО
        # происходит: он писал салонному боту, а онемел витринный, и связи
        # между этими двумя событиями для него не существовало. Функция сама
        # помнит, что уже сказала: второе и пятое входящее не получают ничего.
        notify_silence(
            conversation=conversation,
            bot_user=bot_user,
            chat_id=event.chat_id,
            trace_id=trace_id,
        )
        return

    # DRF-1487 — «печатает…» ПОСЛЕ решения отвечать, а не до.
    #
    # Замер боевого контура, диалог 6e8fdde2, 13:34:20–13:34:34 UTC: на каждое
    # входящее уходили два ``POST /chats/518410834/actions`` → 200, а следом
    # ``silenced_by_handoff``. Пять сообщений — пять пар индикаторов и ноль
    # ответов. Бот, показавший «печатает», ОБЕЩАЕТ ответ, и обещание не
    # выполнялось пять раз подряд.
    #
    # Выбран перенос, а не явное снятие индикатора на ветке отказа: снимать
    # нечем. У MAX в наборе действий (``outbound._CHAT_ACTIONS``) есть
    # ``typing_on`` и нет ``typing_off`` — «печатает…» гаснет только по
    # таймауту или по приходу сообщения. Ветки отказа, на которой можно было бы
    # что-то снять, физически не существует.
    #
    # Цена переноса измерена и мала: между прежней позицией и этой строкой
    # стоят только резолверы личности и диалога, чтение короткой памяти,
    # запись входящего хода и один запрос mute — ни навыков, ни LLM. Это
    # единственный ранний ``return`` во всей функции (ветка выше), поэтому
    # ниже индикатор уже ничем не задерживается: следующая тяжёлая работа —
    # консьерж — начинается после него, как и раньше.
    if event.chat_id:
        from apps.channels.max.outbound import send_chat_action

        send_chat_action(chat_id=event.chat_id, action="typing_on")

    # Reply, in priority order:
    #   0. Safety pre-check (#1053) — a red-flag phrase (suicide / self-harm /
    #      emergency) or a BLOCK phrase (drugs / diagnosis / legal) short-circuits
    #      to a canned reply BEFORE discovery. Variant A on the tenant-less path:
    #      canned reply only, NO AdminTask (founder decision 2026-07-03, #1076).
    #   0.5. Human handoff (DRF-1015) — a deterministic «нужен человек» trigger
    #      BEFORE the concierge LLM (same pattern as the cb:book:* branch):
    #      creates the AdminTask (salon's queue when a tenant context exists,
    #      else the platform queue) and confirms the handoff to the user.
    #   0.7. Personal booking lookup (DRF-911) — «покажи мои записи» /
    #      «когда я записан?» answered deterministically with the caller's REAL
    #      bookings (aggregated across salons) BEFORE onboarding and the
    #      concierge LLM. Before onboarding for the same reason the booking
    #      handoff wins there (``needs_onboarding``: «booking handoff wins over
    #      onboarding, unconditionally») — a data question must not be
    #      swallowed by the welcome greeting; the cohort of pre-existing
    #      global BotUsers has ``welcomed_at IS NULL`` (the pilot owner
    #      included). The detector is IMPORTED from the booking skill, never
    #      re-implemented — FAQ («как записаться?») and mutation phrasings
    #      («перенеси мою запись») keep their previous routing.
    #   1. Onboarding (#1046, behind GLOBAL_BOT_ONBOARDING flag) — welcome + 152-ФЗ
    #      consent capture. Variant A «soft gate»: we greet + capture consent but
    #      do NOT block discovery on it. When onboarding runs we do NOT call
    #      generate_discovery_reply this turn.
    #   2. Discovery → booking handoff (the user tapped a master card → transition
    #      into tenant T's booking flow, #1020).
    #   2.5. Post-handoff booking taps (DRF-988): pick_date / pick_slot /
    #      confirm / cancel route back into tenant T's skill pipeline — before
    #      this they fell through to the concierge as raw text (the «2026 год»
    #      refusal instead of the next booking step).
    #   2.6. Typed continuation of a booking already in flight (DRF-968 /
    #      DRF-1101). Until now ONLY taps continued a booking: free text fell
    #      through to the concierge, which has no booking tool and answers
    #      with the master list — the funnel visibly restarting. Claims a turn
    #      only when it can account for it completely (a service THIS master
    #      delivers, or a date / part of day), so everything else keeps
    #      today's routing. Sits inside the else branch, above 2.7, because
    #      2.7 claims service names and would eat the answer to «напишите
    #      название услуги» — that IS the DRF-968 loop.
    #   2.7. New-booking intent (DRF-1102) — a turn that PARSES as exactly
    #      «покажи мастеров по услуге» (claims_direct_show_masters, DRF-1328)
    #      shows masters straight away, deterministically. Until 24.08 the
    #      test was merely «does the text mention a service», which claimed
    #      «Найди мне САЛОНЫ массажа» and answered it with masters; the
    #      default is inverted now — a turn this branch cannot fully account
    #      for belongs to the concierge and its tools. Sits
    #      AFTER the memory-command / pending-answer checks inside the else
    #      branch below (not a top-level elif here) so a forget-phrase that
    #      happens to name a service — «забудь что я люблю массаж» — is
    #      still processed as a memory command, not hijacked into cards.
    #   3. A normal tenant-less discovery turn (which may itself surface cards).
    assistant_action_type = ""
    # DRF-1209 step 18 — the outbound guard's verdict for the replay
    # capture. "" until the guard runs (the crisis-reply exemption below
    # leaves it empty: the guard deliberately does not see that reply).
    post_verdict = ""
    # DRF-1362 — set only by the multi-select branch: this reply REPLACES the
    # message the tap came from instead of following it.
    clarify_redraw = False
    was_memory_command = False
    concierge_turn_ran = False
    safety = evaluate_inbound(event.text)
    if not safety.allowed:
        _emit_safety_shortcircuit(bot_user, safety, is_global=True)
        reply = DiscoveryReply(text=safety.reply_text)
        # Tag the safety turn so it is distinguishable in the Message table on the
        # global path too — parity with the per-tenant handler (#1053 de-drift).
        assistant_action_type = "safety_pre_check"
        # DRF-1209 step 2 — pipeline parity: the safety canned reply counts
        # as OUTCOME_SUCCESS (pipeline step 7 emitted the same).
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected="safety_pre_check",
        )
    elif (_opt_out_reply := try_handle_opt_out(text=event.text, bot_user=bot_user)) is not None:
        # DRF-1285 — «не пиши мне» must work on THIS surface too. The skill
        # registry is dispatched only on the per-tenant path below, and the
        # pilot runs the global bot, so an off-switch that lived only in the
        # registry was an off-switch nobody on the pilot could reach.
        #
        # Placed directly after the safety gate and before every other
        # branch: safety always wins, and after that a request to stop being
        # written to outranks whatever else the turn might have been — an
        # active anketa FSM included. The match set is closed and
        # whole-message, so no other branch's phrases can reach it.
        reply = DiscoveryReply(text=_opt_out_reply)
        assistant_action_type = "proactive_opt_out"
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected="proactive_opt_out",
        )
    elif (
        _surface_stop_reply := try_handle_surface_stop(text=event.text, bot_user=bot_user)
    ) is not None:
        # DRF-1468 — тап «Не присылать» (`cb:nutri:stop:{surface}`). Стоит
        # сразу после текстовой отписки и по той же причине выше всех
        # прочих веток: просьба не писать важнее всего, чем ещё мог быть
        # ход. Отличие от текстовой отписки одно и принципиальное: глушится
        # ОДНА поверхность, платформенное вето не ставится.
        reply = DiscoveryReply(text=_surface_stop_reply)
        assistant_action_type = "proactive_opt_out"
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected="proactive_opt_out",
        )
    elif stale_tap:
        # DRF-1348 — тап, который нечем подставить. Стоит здесь, а не среди
        # прочих колбэковых веток, по правилу ``_PASSTHROUGH_CALLBACK_PREFIXES``:
        # тап по кнопке, которую бот сам нарисовал, обязан дойти до ответа, а
        # не быть проглоченным приветствием или отданным модели сырым.
        reply = DiscoveryReply(text=STALE_TAP_TEXT, action_data=first_contact_action_data())
        assistant_action_type = "stale_tap"
        # The tap could not be resolved to its intended action — a fallback,
        # not a successfully answered turn.
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_FALLBACK,
            skill_selected="stale_tap",
            fallback_triggered=True,
        )
    elif matches_human_handoff_request(event.text):
        reply = route_global_human_handoff(
            global_bot_user=bot_user,
            global_conversation=conversation,
            message_text=event.text,
            trace_id=trace_id,
        )
        # DRF-1486 — этот диалог сам сказал человеку «передаю менеджеру»,
        # и это решает, ЧТО он прочитает, когда на следующем ходу включится
        # молчание. Отличить «спросил здесь» от «переехало с другого бота»
        # по одной базе нельзя: задача в обоих случаях может лежать на
        # салонном диалоге (см. queue addressing в ``route_global_human_
        # handoff``). Различает их только факт доставки подтверждения — он
        # и записывается здесь, рядом с доставкой.
        mark_handoff_announced(conversation=conversation, chat_id=event.chat_id)
        assistant_action_type = "human_handoff"
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_ESCALATED,
            skill_selected="human_handoff",
        )
    elif event.text.startswith(VISIT_CALLBACK_PREFIXES):
        # Cards and repeat taps carry an appointment id the bot itself
        # rendered, so they resolve before the natural-language branches.
        reply = route_visit_callback(
            global_bot_user=bot_user,
            callback_text=event.text,
            trace_id=trace_id,
        )
        # A repeat tap is the start of a booking, not a lookup — the funnel
        # these events exist to measure must not merge the two.
        assistant_action_type = (
            "booking_repeat"
            if event.text.startswith(CALLBACK_VISIT_REPEAT_PREFIX)
            else "visit_card"
        )
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected=assistant_action_type,
        )
    elif is_personal_booking_lookup(event.text):
        # DRF-1032: the answer now comes from the Ayla backend, not from the
        # local mirror — a mirror row can outlive the booking it mirrors
        # (DRF-1034), and history is where that error accumulates.
        reply = route_visits(
            global_bot_user=bot_user,
            trace_id=trace_id,
        )
        assistant_action_type = "booking_lookup"
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected="booking_lookup",
        )
    elif getattr(settings, "GLOBAL_BOT_ONBOARDING", False) and needs_onboarding(
        bot_user, event.text, conversation
    ):
        reply = run_onboarding_turn(conversation, bot_user, event.text, trace_id)
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected="onboarding",
        )
    elif clarify_outcome is not None:
        # DRF-1362 — a multi-select redraw or its close. Sits with the other
        # callback branches and BEFORE the concierge for the same reason they
        # do: the text is an id this bot rendered, not something a person said.
        # Already resolved above; this branch only places it in the ladder.
        reply = clarify_outcome.reply or DiscoveryReply(text=CLARIFY_STALE_TEXT)
        clarify_redraw = clarify_outcome.redraw
        assistant_action_type = "clarification"
    elif event.text.startswith(CATALOG_CALLBACK_PREFIXES):
        # DRF-1304 — a catalog chip tap. Sits with the other callback branches
        # and BEFORE the concierge below for the same reason they do: the text
        # is an id this bot rendered, not something a person said, and the
        # model must never be handed a raw «cb:…» string to interpret.
        # execute_catalog_callback returns a reply for every catalog callback
        # — stale and malformed refs included — so this branch cannot fall
        # through once the prefix matched.
        reply = execute_catalog_callback(event.text) or DiscoveryReply(text=CATALOG_STALE_CARD_TEXT)
        assistant_action_type = "catalog_card"
    elif event.text.startswith(CALLBACK_DISCOVER_BOOK_PREFIX):
        reply = _discovery_handoff_reply(event, bot_user, trace_id)
    elif event.text.startswith(BOOKING_CALLBACK_PREFIXES):
        reply = route_booking_callback(
            global_bot_user=bot_user,
            callback_text=event.text,
            chat_id=event.chat_id,
            trace_id=trace_id,
        )
    else:
        # An active memory identity = canonical ayla_user_id + PERSONAL_DATA
        # consent (green's 152-ФЗ basis). While memory is dormant (no consent)
        # this is None, so neither commands nor surfacing fire and the discovery
        # happy-path is byte-identical. Best-effort: the consent read is a DB
        # call and runs before the reply is sent, so a transient error must
        # degrade to «no memory», never abort the turn.
        ayla_user_id = None
        try:
            if bot_user.ayla_user_id and can_store_green_memory(bot_user):
                ayla_user_id = bot_user.ayla_user_id
        except Exception:  # noqa: BLE001 — memory gating must never break the turn
            logger.exception("channels.max.global.memory_gate_failed bot_user=%s", bot_user.id)
            ayla_user_id = None

        # 2.5. Chat-side 152-ФЗ memory commands (M-B4 / #1113): «покажи что
        #      знаешь», «забудь {X}», «забудь всё». Best-effort — a failure
        #      degrades to normal discovery, never aborts the turn.
        mem_reply: DiscoveryReply | None = None
        if ayla_user_id is not None:
            try:
                cmd = handle_memory_command(
                    user_id=ayla_user_id,
                    text=event.text,
                    last_assistant_text=_last_assistant_content(history),
                    # DRF-1261: the Ayla-side half of the loop (declared-prefs
                    # merge into «покажи», field clearing on «забудь»).
                    bot_user=bot_user,
                )
                if cmd is not None:
                    # DRF-1305 — the show reply now carries «Забыть: {домен}»
                    # chips. Dropping action_data here would have rendered the
                    # list without the buttons that make it actionable, which
                    # is the state the owner ruling was opened against.
                    mem_reply = DiscoveryReply(text=cmd.text, action_data=cmd.action_data)
                    assistant_action_type = cmd.action_type
            except Exception:  # noqa: BLE001 — memory commands must never break the turn
                logger.exception(
                    "channels.max.global.memory_command_failed bot_user=%s", bot_user.id
                )
                mem_reply = None
        was_memory_command = mem_reply is not None

        if mem_reply is not None:
            reply = mem_reply
        else:
            # W5 (S3.5): a pending memory question treats this message as its
            # answer BEFORE the concierge turn runs. Best-effort — a failure
            # degrades to a normal concierge turn, never aborts it.
            ask_reply: DiscoveryReply | None = None
            try:
                ask_reply = try_handle_answer(conversation, bot_user, event.text)
            except Exception:  # noqa: BLE001
                logger.exception("channels.max.global.memory_ask_failed bot_user=%s", bot_user.id)
                ask_reply = None
            # DRF-968 / DRF-1101 — a TYPED turn that continues a booking
            # already in flight. Deliberately ABOVE the fast path: the answer
            # to «напишите название услуги» is a service name, and the fast
            # path claims service names — which is exactly how the ask-the-
            # service question came back as the master list it had just been
            # asked from (DRF-968's loop). Below the memory branches for the
            # same reason the fast path is: a forget-phrase or a pending
            # memory answer is not a booking turn, whatever words it uses.
            #
            # Returns None for anything it cannot fully account for, so a
            # turn it does not claim reaches the concierge byte-identically.
            booking_reply: DiscoveryReply | None = None
            if ask_reply is None:
                booking_reply = try_continue_booking(
                    global_bot_user=bot_user,
                    conversation=conversation,
                    text=event.text,
                    chat_id=event.chat_id,
                    trace_id=trace_id,
                )

            # DRF-1102 — the deterministic branch: answer a general
            # booking/service request without the concierge LLM. It is faster
            # and cheaper than a model turn, and when it finds masters it is
            # right.
            #
            # DRF-1283 — but it no longer owns the turn unconditionally. It
            # returns None when the search matched NOBODY, and that is not an
            # answer: it is the deterministic layer admitting it could not
            # resolve the request, at which point the model is exactly what
            # should run. The turn then falls through to the concierge below
            # — same path an unrecognised turn takes, memory blocks and all.
            #
            # This was unsafe until 23.08: the concierge was single-pass, so a
            # show_masters call ate the whole turn and the model, with nothing
            # to say over the tool result, re-asked forever — the very failure
            # DRF-1102 added this branch to stop. DRF-1266 made it multi-pass
            # (tool result comes back as an ordinary message, capped by
            # CONCIERGE_MAX_LLM_PASSES), which is what makes the fallthrough
            # safe now.
            #
            # DRF-1328 — and it no longer claims every turn that merely
            # MENTIONS a service. «Найди мне САЛОНЫ массажа» did exactly
            # that on 24.08 and came back as master cards, twice, while
            # ``show_salons`` (DRF-1304, shipped the day before) sat unused
            # because the model never got the turn. The gate now parses the
            # turn and takes it only when it is exactly «покажи мастеров по
            # услуге» — everything else is the concierge's, including the
            # capabilities nobody has built yet
            # (``apps.orchestrator.fast_path``).
            direct_reply: DiscoveryReply | None = None
            if (
                ask_reply is None
                and booking_reply is None
                and claims_direct_show_masters(event.text)
            ):
                direct_reply = generate_direct_show_masters_reply(
                    event.text,
                    trace_id=str(trace_id) if trace_id else None,
                    bot_user=bot_user,
                    conversation=conversation,
                )

            if ask_reply is not None:
                reply = ask_reply
            elif booking_reply is not None:
                reply = booking_reply
                assistant_action_type = "booking_continued"
            elif direct_reply is not None:
                reply = direct_reply
                assistant_action_type = "discovery_show_masters_direct"
            else:
                # DRF-1268 — structured nutrition turns (cb:anketa:* /
                # cb:food:* taps, /anketa, an active anketa FSM claiming its
                # answer, photo-only turns) route to the nutrition skills
                # DETERMINISTICALLY, before the concierge LLM. Free text is
                # never claimed here — it belongs to the concierge with the
                # nutrition tools. Best-effort: a failure degrades to the
                # concierge turn below.
                nutrition_result = None
                try:
                    nutrition_result = try_handle_structured_nutrition_turn(
                        text=event.text,
                        attachments=event.attachments,
                        bot_user=bot_user,
                        conversation=conversation,
                        trace_id=str(trace_id) if trace_id else "",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "channels.max.global.nutrition_turn_failed bot_user=%s", bot_user.id
                    )
                    nutrition_result = None
                if nutrition_result is not None:
                    reply = DiscoveryReply(
                        text=nutrition_result.reply_text,
                        action_data=nutrition_result.action_data,
                    )
                    assistant_action_type = nutrition_result.action_type or "nutrition_skill"
                else:
                    # Memory surfacing (M-C1 / #1101): inject the user's GREEN memory into
                    # the discovery prompt. Best-effort: these DB reads run BEFORE the reply
                    # is sent, so a transient error must degrade to «no memory», never abort
                    # the turn (the idempotency key is already claimed — a raise would lose
                    # the reply on retry).
                    personal_context_block = ""
                    try:
                        if ayla_user_id is not None:
                            personal_context_block = (
                                render_current_personal_context(ayla_user_id) or ""
                            )
                    except Exception:  # noqa: BLE001 — memory surfacing must never break the turn
                        logger.exception(
                            "channels.max.global.memory_surface_failed bot_user=%s", bot_user.id
                        )
                        personal_context_block = ""
                    # W5 task 2: consent-gated ai-core memory block (declared prefs +
                    # inferred green). "" when the memory_green gate is closed —
                    # not a single fact reaches the prompt then. Fail-closed.
                    memory_block = ""
                    try:
                        memory_block = build_concierge_memory_block(bot_user)
                    except Exception:  # noqa: BLE001 — belt-and-braces; the module is fail-closed
                        logger.exception(
                            "channels.max.global.memory_block_failed bot_user=%s", bot_user.id
                        )
                        memory_block = ""
                    # DRF-1284: the weekly nutrition picture (Ayla deficits
                    # aggregate). "" whenever the 152-ФЗ gate is closed
                    # (PERSONAL_DATA + HEALTH, both required — nutrition is
                    # special-category health data, not the green zone the block
                    # above rides), or Ayla is unreachable, or the week is empty.
                    # Best-effort exactly like its neighbours: this runs AFTER the
                    # idempotency key is claimed, so a raise would lose the reply
                    # on retry rather than retry it.
                    nutrition_block = ""
                    try:
                        nutrition_block = build_nutrition_context_block(bot_user)
                    except Exception:  # noqa: BLE001 — belt-and-braces; module is fail-closed
                        logger.exception(
                            "channels.max.global.nutrition_context_failed bot_user=%s",
                            bot_user.id,
                        )
                        nutrition_block = ""
                    if nutrition_block:
                        # Cost attribution (DRF-1211): the block grows the prompt,
                        # and the growth lands in AIRequestMetric.llm_tokens_input.
                        # Without this line that growth is unattributable — the
                        # metric row's request_id IS trace_id, so this joins the
                        # two. Length only: the block holds health data.
                        logger.info(
                            "channels.max.global.nutrition_context_attached trace=%s chars=%d",
                            trace_id,
                            len(nutrition_block),
                        )
                    # W5 task 1: the concierge DM runs on ayla-ai-core AIConcierge
                    # (apps.orchestrator.concierge) — history from the Message table,
                    # assistant turn persisted by its store (reply.persisted=True).
                    # Routed via the normalized orchestration seam
                    # (apps.orchestrator.turn_seam): tenant=None is the designed
                    # global-pilot input (OR-BOT-3), the concierge brain is unchanged.
                    turn_reply = orchestrate_turn(
                        TurnContext(
                            surface=SURFACE_GLOBAL,
                            conversation=conversation,
                            bot_user=bot_user,
                            text=event.text,
                            channel=event.channel,
                            trace_id=str(trace_id) if trace_id else "",
                            tenant=None,
                            # Почти каждое семейство, чей ход мимо истории
                            # проходит (booking 2.5, каталог, уточнение,
                            # визиты, cb:discover:book, протухший тап,
                            # приветствие), имеет СВОЮ ветку выше и до
                            # консьержа не доходит. None здесь приезжает от
                            # тапа, у которого ветки нет: нераспознанный тап
                            # анкеты/еды правильной формы, а с DRF-990
                            # (третий заход) — ещё и глагол семейства
                            # `cb:discover:`, кроме `book:`. Своего маршрута
                            # у такого глагола сегодня нет, и он доезжает
                            # сюда сырым `event.text` — это и есть тот самый
                            # «гейт как список исключений», отдельный
                            # открытый вопрос. Ход при этом не теряется:
                            # ответ бота записывается как обычно.
                            user_message_id=user_msg.id if user_msg is not None else None,
                            memory_block=memory_block,
                            nutrition_block=nutrition_block,
                            extra_system=personal_context_block or "",
                        )
                    )
                    if turn_reply.outage:
                        # DRF-1348 — состояние «AI недоступна» из макета C01.
                        # До сих пор сюда приходила строка «короткий
                        # технический сбой — отвечу через минуту», которая
                        # обещает то, чего не будет: ход потерян, и никто к
                        # нему не вернётся, пока человек не напишет сам. Здесь
                        # это заменено на правду плюс кнопка, которая делает
                        # обещанное — отправляет ту же реплику ещё раз.
                        #
                        # Флаг ставится ровно там, где не дошли до модели
                        # (DiscoveryReply.outage), а не там, где модель плохо
                        # ответила: предлагать «Повторить» для взятого хода
                        # значило бы врать вторым способом.
                        logger.info(
                            "channels.max.global.ai_unavailable bot_user=%s trace=%s",
                            bot_user.id,
                            trace_id,
                        )
                        reply = DiscoveryReply(
                            text=AI_UNAVAILABLE_TEXT,
                            action_data=ai_unavailable_action_data(),
                            persisted=turn_reply.assistant_persisted,
                        )
                        assistant_action_type = "ai_unavailable"
                        # Ни разбор намерения (DRF-1273), ни вплетение вопроса
                        # памяти (W5) на этом ходу не нужны: первое — ещё один
                        # вызов той же недоступной модели, второе — вопрос
                        # поверх извинения. Ход не состоялся.
                    else:
                        reply = DiscoveryReply(
                            text=turn_reply.reply_text,
                            action_data=turn_reply.action_data,
                            persisted=turn_reply.assistant_persisted,
                        )
                        concierge_turn_ran = True
                        # W5 (S3.5): organically weave ONE memory question when the Ayla
                        # anti-spam engine allows asking. Best-effort.
                        try:
                            reply = maybe_weave_question(conversation, bot_user, reply)
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "channels.max.global.memory_ask_weave_failed bot_user=%s",
                                bot_user.id,
                            )

    # DRF-1325 — the time half of «хочу на массаж завтра вечером». On
    # 2026-08-23 it was dropped without a word and the booking landed five
    # days out at 11:30. Runs on every turn of this surface, right before the
    # reply is persisted, so it sees the FINAL reply and cannot change which
    # branch produced it.
    reply = _remember_time_preference(conversation, bot_user, event.text, reply)

    # DRF-1210 — the last thing between the assistant and a person on the ONLY
    # surface where the person is a client. Every branch above lands here: the
    # concierge's prose, the deterministic renders, the memory answers, the
    # nutrition cards. Deliberately AFTER the branch has been chosen, for the
    # same reason ``_remember_time_preference`` is — this must see the FINAL
    # reply and must not be able to change which branch produced it.
    #
    # The one exemption is the inbound gate's own canned crisis reply. Its copy
    # is founder-approved and it is the sole live self-harm response
    # (``gate.py``: «change it only via a new founder sign-off»); a regex
    # deciding on its own to replace a helpline number with «спросите
    # администратора салона» is the one failure here that could cost more than
    # it saves. Nothing else is exempt — including the contour's own canned
    # lines, which a test pins clean rather than a whitelist excuses.
    if assistant_action_type != "safety_pre_check":
        guarded = guard_outbound(reply.text, surface="max", bot_user=bot_user, trace_id=trace_id)
        post_verdict = "block" if guarded.blocked else "allow"
        if guarded.blocked:
            # The keyboard goes with the text. ``outbound.py``'s rule is that
            # the reply is REPLACED, not edited, and master cards left hanging
            # under «тут нужен человек» would be an edited reply by another
            # name. ``persisted=False``: whatever the producer wrote, the
            # transcript has to end up holding what the person actually read.
            reply = DiscoveryReply(text=guarded.text, action_data=None, persisted=False)
            assistant_action_type = OUTBOUND_ACTION_TYPE
            # DRF-1362 — and it is never an in-place edit either. ``outbound.py``
            # 's rule is that a blocked reply is REPLACED, not edited; quietly
            # rewriting the multi-select message the person was looking at,
            # keyboard stripped, is an edited reply by another name. A new
            # message is also the only form in which «тут нужен человек» reads
            # as the turn stopping rather than the question changing.
            clarify_redraw = False

    # Persist + remember the assistant turn, then send to MAX (with any keyboard).
    # W5: the AIConcierge store already persisted concierge turns
    # (reply.persisted=True) — skip here to avoid a double row; every other
    # branch records as before.
    if not reply.persisted:
        record_global_message(
            conversation,
            role="assistant",
            content=reply.text,
            rendered_text=reply.text,
            action_type=assistant_action_type,
            # DRF-1362 — the offer a clarification made has to outlive the
            # message that made it: the tap answering it arrives a turn later
            # and needs the options by name. Passed for every branch, not just
            # this one, because the field was always meant to be written here
            # (``record_message`` has written it since Sprint 3) and a keyboard
            # that cannot be reconstructed afterwards is a gap on every path.
            action_data=reply.action_data,
            trace_id=trace_id,
        )
    short_term.append(conversation.id, role="assistant", content=reply.text)
    if assistant_action_type == "safety_pre_check":
        # Crisis reply — alert loudly on delivery failure (#1082).
        _deliver_crisis_reply(
            chat_id=event.chat_id,
            text=reply.text,
            bot_user=bot_user,
            trace_id=trace_id,
            is_global=True,
            attachments=_build_attachments(reply.action_data),
        )
    elif clarify_redraw and event.channel_message_id:
        # DRF-1362 — the whole point of the ticket: two taps update ONE
        # message instead of stacking three near-identical ones.
        # ``channel_message_id`` is the mid of the message the tapped keyboard
        # hung under (``parser.py`` fills it from ``message.body.mid`` on a
        # callback), so this rewrites exactly the screen the person is looking
        # at — the same move ``legacy_maxbot/handlers/health_screening.py:265``
        # has made on this channel since Phase 3.2A.
        #
        # ``edit_message_or_send`` falls back to a new message on ANY refusal,
        # and MAX refuses edits routinely — an old message, a deleted one, or
        # simply the second edit inside the same half-second. The tap is
        # answered either way; only the polish is lost.
        edit_message_or_send(
            chat_id=event.chat_id,
            message_id=event.channel_message_id,
            text=reply.text,
            attachments=_build_attachments(reply.action_data),
        )
    else:
        send_message(
            chat_id=event.chat_id,
            text=reply.text,
            attachments=_build_attachments(reply.action_data),
        )

    # DRF-1209 step 18 — replay capture for the live global path. ONE point
    # after the reply is delivered: every branch above (safety, opt_out,
    # stale tap, handoff, visits, onboarding, callbacks, concierge) lands
    # here with its FINAL reply, so a single call covers them all and a
    # capture failure can never affect what the user saw. Flag-gated +
    # best-effort inside (REPLAY_LIVE_CAPTURE_ENABLED, default OFF). Runs
    # after the send — zero added user-visible latency. The muted-handoff
    # early return above answers nothing, so it captures nothing.
    _capture_live_replay(
        trace_id=trace_id,
        event=event,
        surface="max_global",
        branch=assistant_action_type or ("concierge" if concierge_turn_ran else ""),
        pre_verdict=safety.verdict,
        post_verdict=post_verdict,
        reply_text=reply.text,
        keyboard_size=len(_build_attachments(reply.action_data) or []),
    )

    # DRF-1273 — canonical intent resolution (Output Contract 0.5) for
    # free-text concierge turns. Runs AFTER the reply is delivered: zero
    # added user-visible latency, and a resolver failure can never affect
    # what the user saw. Best-effort, flag-gated inside
    # (INTENT_RESOLUTION_LIVE_ENABLED); the contract lands in the turn log
    # as ``orchestrator.intent_resolution.ok``.
    if concierge_turn_ran:
        try:
            resolve_and_log_turn_intent(
                text=event.text,
                bot_user=bot_user,
                conversation=conversation,
                user_message_id=user_msg.id if user_msg is not None else None,
                trace_id=str(trace_id) if trace_id else "",
                # DRF-1385 — трасса выбора инструментов ЭТОГО хода, пронесённая
                # консьержем через шов (TurnReply). Берётся из turn_reply, а не
                # из reply: reply выше пересобирается (weave/guard), а трасса
                # описывает выбор МОДЕЛИ, который эти пересборки не отменяют.
                # getattr — как в шве: прежние производители поля не знают.
                tool_trace=getattr(turn_reply, "tool_trace", None),
            )
        except Exception:  # noqa: BLE001 — resolution must never break the turn
            logger.exception(
                "channels.max.global.intent_resolution_failed bot_user=%s", bot_user.id
            )

    # Memory write (M-B2 / #1099): learn explicit green facts the user stated
    # this turn (e.g. «я веган»). Best-effort + consent-gated inside; never
    # affects the reply already sent. No active questioning in the pilot.
    #
    # SKIP when this turn was a memory command (M-B4): «забудь что я веган»
    # contains the substring «я веган», so re-running the extractor here would
    # instantly re-create the fact the user just asked to forget — nullifying
    # the 152-ФЗ erasure. A forget/show turn must never write memory.
    if not was_memory_command:
        record_explicit_green_facts(bot_user, event.text)


def _remember_time_preference(conversation, bot_user, text: str, reply):
    """Parse «завтра вечером», store it, and say it was heard (DRF-1325).

    Two separate jobs, deliberately not merged:

    **Storing** happens on every turn that names a time. The preference is
    read back inside ``tenant_scope(T)`` by the booking flow
    (``apps.orchestrator.handoff.carry_time_preference``), which is what
    turns «завтра вечером» into tomorrow's evening slots instead of a bare
    calendar. It is stored even when this turn's reply says nothing about
    time — the next tap is where it pays off.

    **Acknowledging** happens only on a reply that offers masters to book
    AND has not been persisted yet. The second condition is not cosmetic: a
    concierge turn is written to the Message table by its own store before
    control returns here, so prefixing its text would send one thing and
    record another — and the replay fixtures read the record. On that path
    the request is instead read back one tap later, by the picker itself
    («Вы просили завтра вечером — вот что есть:»).

    Best-effort throughout: a preference is a hint, and no hint is worth a
    turn.
    """
    try:
        from apps.orchestrator.time_preference import (
            describe,
            local_today,
            parse_time_preference,
            save_time_preference,
        )

        # Weekday words need to know what day it is; the global bot has no
        # tenant, so this falls back to Europe/Moscow — the same default
        # Tenant.timezone carries and the zone all nine pilot masters use.
        today = local_today(getattr(bot_user, "tenant", None))
        pref = parse_time_preference(text, weekday_today=today.weekday())
        if pref is None:
            return reply
        save_time_preference(conversation, pref)

        if reply.persisted or not _offers_booking(reply):
            return reply
        heard = describe(pref, None, today)
        day = pref.day_offset
        if day is not None:
            from datetime import timedelta

            heard = describe(pref, (today + timedelta(days=day)).isoformat(), today)
        if not heard:
            return reply
        return DiscoveryReply(
            text=f"{_TIME_HEARD_LINE.format(heard=heard)}\n\n{reply.text}",
            action_data=reply.action_data,
            persisted=reply.persisted,
        )
    except Exception:  # noqa: BLE001 — a hint must never break a turn
        logger.exception("channels.max.global.time_pref_failed")
        return reply


# Deliberately not «записываю на завтра вечером»: nothing is booked yet and
# no time has been checked. It states what was heard and what will be done
# with it — which is exactly as much as this layer knows.
_TIME_HEARD_LINE = "Поняла: {heard}. Подберу время под это."


def _offers_booking(reply) -> bool:
    """True when the reply carries at least one «записаться» button."""
    data = getattr(reply, "action_data", None)
    if not isinstance(data, dict):
        return False
    for attachment in data.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        payload = attachment.get("payload")
        if not isinstance(payload, dict):
            continue
        for button in payload.get("buttons") or []:
            if isinstance(button, dict) and str(button.get("callback") or "").startswith(
                CALLBACK_DISCOVER_BOOK_PREFIX
            ):
                return True
    return False


def _discovery_handoff_reply(
    event: CanonicalEvent, global_bot_user, trace_id: str | uuid.UUID | None
) -> DiscoveryReply:
    """Parse the ``cb:discover:book:{tenant}:{master}[:{service}[:{query}]]``
    callback and route into tenant T's booking flow via the handoff layer
    (#1020, service context DRF-962, query context DRF-1324).

    All ids are UUIDs (no ``:`` inside) and the query ref is base64url (whose
    alphabet has no ``:`` either), so colon splits stay unambiguous.

    Both trailing segments are optional and POSITIONAL. The service part is
    absent on cards rendered before DRF-962 and on a query where no single
    service matched; the query part is absent on cards rendered before
    DRF-1324 and on a query with nothing to carry. A serviceless card that
    DOES carry a query sends an EMPTY third segment («…:{master}::{ref}»), so
    the fourth position always means the same thing.

    Every degradation here is one-way: a malformed id loses the whole tap
    (generic reply), a malformed service or query segment loses only itself
    and the handoff answers with the ask-the-service menu — narrowed if the
    query survived, whole if it did not. Nothing about a bad segment can send
    the user to a service they did not ask for.
    """
    payload = event.text[len(CALLBACK_DISCOVER_BOOK_PREFIX) :]
    try:
        parts = payload.split(":")
        if len(parts) not in (2, 3, 4):
            raise ValueError(f"expected 2 to 4 segments, got {len(parts)}")
        tenant_id = uuid.UUID(parts[0])
        master_id = uuid.UUID(parts[1])
    except (ValueError, AttributeError):
        logger.warning("channels.max.global.handoff.bad_payload payload=%r", payload)
        return DiscoveryReply(
            text="Не удалось открыть запись — попробуйте выбрать мастера ещё раз."
        )

    # The service part is genuinely optional: a corrupt third segment must not
    # throw away two valid ids — degrade to the serviceless handoff (which
    # asks for the service) instead of the generic error.
    service_id: uuid.UUID | None = None
    if len(parts) >= 3 and parts[2]:
        try:
            service_id = uuid.UUID(parts[2])
        except (ValueError, AttributeError):
            logger.warning("channels.max.global.handoff.bad_service_part payload=%r", payload)

    query_ref = parts[3] if len(parts) == 4 else ""

    return handoff_to_booking(
        global_bot_user=global_bot_user,
        tenant_id=tenant_id,
        master_id=master_id,
        service_id=service_id,
        query_ref=query_ref,
        chat_id=event.chat_id,
        trace_id=trace_id,
    )


def _handle_max_event_inner(event: CanonicalEvent, trace_id: str | uuid.UUID | None) -> None:
    """Inner pipeline — parse-already-done caller. Side-effects only."""

    # DRF-1209 step 2 — turn clock for the AIRequestMetric row. Read
    # unconditionally: one monotonic() call is free, and the flag check
    # lives inside _record_live_path_metric.
    t_start = time.monotonic()

    bot_user = resolve_or_create_bot_user(
        channel=event.channel,
        channel_user_id=event.channel_user_id,
        chat_id=event.chat_id,
    )
    conversation = resolve_active_conversation(bot_user)
    # `create_if_missing=True` (default) → never returns None. The
    # narrow tells mypy this; an assertion in case the contract slips.
    assert conversation is not None  # noqa: S101 — contract guard

    # MAX UX indicators: tell the chat we've read the message and we're
    # typing a reply BEFORE doing any heavy work (LLM call, DB writes).
    # Both are best-effort fire-and-forget — failures are logged inside
    # send_chat_action and do not propagate. The user sees the «прочитано /
    # печатает…» chrome that mysite's MAX SDK provided automatically
    # (post-cutover regression 2026-05-20).
    #
    # DRF-1487 — но «печатает…» НЕ когда бот молчит. Оба индикатора стояли
    # первой строкой функции, а решение промолчать принимается на 230 строк
    # ниже: замер 04.09 показал пять пар индикаторов и ноль ответов подряд.
    #
    # Расходятся они по семантике, а не по удобству. «Прочитано» —
    # констатация факта доставки, правдивая и тогда, когда отвечать будет
    # оператор; убрать её значило бы оставить человека вообще без признаков
    # жизни, то есть усугубить ровно тот дефект, который чинит DRF-1486.
    # «Печатает…» — обещание ответа от БОТА, и его-то и нельзя давать тому,
    # кому бот не ответит.
    #
    # Условие, а не перенос вниз, — по замеру: единственный молчащий
    # ``return`` здесь стоит ПОСЛЕ ``orchestrate_turn``, то есть после
    # навыков и LLM. Перенести индикатор туда значило бы задержать
    # «печатает…» на всё время работы модели для каждого обычного хода —
    # дороже, чем сам дефект. Условие же стоит два запроса (резолверы выше)
    # и повторяет ровно тот предикат, по которому ниже молчит диспетчер
    # (``skills.registry.dispatch``: state == HUMAN_HANDOFF), так что
    # разойтись они не могут.
    if event.chat_id:
        from apps.channels.max.outbound import send_chat_action

        send_chat_action(chat_id=event.chat_id, action="mark_seen")
        if conversation.state != Conversation.State.HUMAN_HANDOFF:
            send_chat_action(chat_id=event.chat_id, action="typing_on")

    # Persist the inbound turn.
    record_message(
        conversation,
        role="user",
        content=event.text,
        trace_id=trace_id,
    )
    short_term.append(
        conversation.id,
        role="user",
        content=event.text,
    )

    # --- Safety pre-check (#1053) — BEFORE photo download + skill dispatch ---
    # A red-flag (suicide / self-harm / acute emergency) or a BLOCK phrase (drugs
    # / definitive diagnosis / legal advice) must never reach echo / FAQ /
    # food_scanner. S1-B = detection + canned reply only; the AdminTask +
    # HUMAN_HANDOFF flip on this per-tenant path is S1-C (#1047), deliberately NOT
    # done here. We short-circuit before the photo download so a blocked turn
    # doesn't waste a CDN fetch either.
    #
    # HUMAN_HANDOFF guard: when a human operator is already driving this
    # conversation, the bot MUST stay silent (skills.registry.dispatch enforces
    # this via should_send=False at state==HUMAN_HANDOFF). The safety short-circuit
    # runs BEFORE dispatch, so without this guard it would barge a canned crisis
    # reply over the operator — the worst moment to auto-inject. When in handoff we
    # skip the short-circuit and fall through to dispatch, which mutes the turn.
    safety = evaluate_inbound(event.text)
    if not safety.allowed and conversation.state != Conversation.State.HUMAN_HANDOFF:
        _emit_safety_shortcircuit(bot_user, safety, is_global=False)
        record_message(
            conversation,
            role="assistant",
            content=safety.reply_text,
            rendered_text=safety.reply_text,
            action_type="safety_pre_check",
            trace_id=trace_id,
        )
        short_term.append(conversation.id, role="assistant", content=safety.reply_text)
        _deliver_crisis_reply(
            chat_id=event.chat_id,
            text=safety.reply_text,
            bot_user=bot_user,
            trace_id=trace_id,
            is_global=False,
        )
        logger.info(
            "channels.max.handler.safety_shortcircuit conversation=%s verdict=%s",
            conversation.id,
            safety.verdict,
        )
        # DRF-1209 step 2 — pipeline parity: the safety canned reply counts
        # as OUTCOME_SUCCESS (pipeline step 7 emitted the same).
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            tenant=conversation.tenant,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
            skill_selected="safety_pre_check",
        )
        # DRF-1209 step 18 — replay capture (flag-gated, best-effort inside).
        # post_verdict="": the crisis reply is exempt from the outbound guard.
        _capture_live_replay(
            trace_id=trace_id,
            event=event,
            surface="max_per_tenant",
            branch="safety_pre_check",
            pre_verdict=safety.verdict,
            post_verdict="",
            reply_text=safety.reply_text,
        )
        return

    # Photo bytes path — Веха 2 of the photo adapter port.
    #
    # The food_scanner skill consumes `conversation.last_photo_bytes`
    # (set as a runtime Python attribute, NOT a DB field — see ADR-0011
    # + skill docstring). Channel-adapter contract: if the inbound
    # message carries an IMAGE attachment, we stream the bytes from
    # MAX CDN here and stash them on the conversation instance for the
    # skill to pick up via getattr.
    #
    # On any failure (oversize, timeout, network, 4xx/5xx) we set the
    # attribute to None — food_scanner already handles the None case
    # gracefully (PHOTO_NO_BYTES reply). Photo download MUST NOT raise
    # through the dispatcher; the customer should always get a reply,
    # even if it's «не получилось скачать фото».
    if event.attachments:
        photo_url = extract_first_photo_url(event.attachments)
        if photo_url is not None:
            try:
                conversation.last_photo_bytes = download_photo(photo_url)  # type: ignore[attr-defined]
            except PhotoTooLargeError:
                # Log hostname only — MAX CDN URLs commonly include
                # signed bearer tokens in the querystring (PR #893 B2).
                logger.warning(
                    "channels.max.handler.photo_too_large conversation=%s host=%s",
                    conversation.id,
                    safe_hostname(photo_url),
                )
                conversation.last_photo_bytes = None  # type: ignore[attr-defined]
            except PhotoDownloadError as exc:
                logger.warning(
                    "channels.max.handler.photo_download_failed conversation=%s host=%s exc=%s",
                    conversation.id,
                    safe_hostname(photo_url),
                    exc,
                )
                conversation.last_photo_bytes = None  # type: ignore[attr-defined]

    # Sprint 3 / D1 — dispatch through the skill registry, routed via the
    # normalized orchestration seam (apps.orchestrator.turn_seam). The seam
    # keeps the skills.registry import lazy internally (the echo-skill ↔
    # handler.py module-load cycle), and the SkillResult is rebuilt 1:1 so
    # every branch below is byte-identical to the pre-seam direct dispatch.
    # The EchoSkill is the final catch-all in registration order so
    # dispatch() always returns a SkillResult under normal load; the
    # `_echo_text` fallback below stays only for the defensive
    # "registry empty" edge case (e.g. tests that reset the registry).
    skill_result = turn_reply_to_skill_result(
        orchestrate_turn(
            TurnContext(
                surface=SURFACE_PER_TENANT,
                conversation=conversation,
                bot_user=bot_user,
                text=event.text,
                channel=event.channel,
                trace_id=str(trace_id) if trace_id else "",
                has_attachments=bool(event.attachments),
            )
        )
    )

    # Post-dispatch handoff (#1047): a skill (e.g. booking) requested escalation to
    # a human via should_handoff. Create the AdminTask + flip HUMAN_HANDOFF + send
    # the skill's line once, then stop.
    #
    # Checked BEFORE the D3 silence path so an escalation is NEVER swallowed: the
    # HUMAN_HANDOFF mute returns should_send=False AND should_handoff=False (see
    # skills.registry.dispatch), so it still falls through to the silence branch
    # below — but a skill that sets should_handoff=True with should_send=False
    # would otherwise be silently dropped here (the exact silent-drop this ticket
    # fixes). Re-escalation of an already-handed-off turn can't happen because the
    # mute result carries should_handoff=False.
    # DRF-1209 — pipeline step 10.5 on the live path: even a skill that forgot
    # to set should_handoff is escalated when its confidence is below the
    # threshold. The floor FEEDS the same _dispatch_skill_handoff path (no
    # parallel AdminTask mechanics); gated by SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED
    # (default off → floor_reason is always "" and nothing below changes).
    confidence_floor_reason = _confidence_floor_reason(skill_result)
    if skill_result is not None and (skill_result.should_handoff or confidence_floor_reason):
        if not confidence_floor_reason:
            handoff_sent_text = _dispatch_skill_handoff(
                conversation, skill_result, event.chat_id, trace_id
            )
        else:
            # Pipeline reason contract: the skill's own slug first, the floor
            # diagnostic appended ("a | b"); floor-only escalation carries just
            # the diagnostic. When the skill never asked for handoff its own
            # answer must NOT reach the user — force the canned handoff line.
            skill_reason = skill_result.handoff_reason or (
                "skill_requested_handoff" if skill_result.should_handoff else ""
            )
            handoff_sent_text = _dispatch_skill_handoff(
                conversation,
                skill_result,
                event.chat_id,
                trace_id,
                reason_override=(
                    f"{skill_reason} | {confidence_floor_reason}"
                    if skill_reason
                    else confidence_floor_reason
                ),
                handoff_text_override=(
                    None if skill_result.should_handoff else _HANDOFF_FALLBACK_TEXT
                ),
            )
        # DRF-1209 step 2 — the escalation is this turn's terminal outcome.
        _record_live_path_metric(
            bot_user=bot_user,
            conversation=conversation,
            trace_id=trace_id,
            message_text=event.text,
            t_start=t_start,
            tenant=conversation.tenant,
            outcome=AIRequestMetric.OUTCOME_ESCALATED,
            skill_selected=_skill_selected_label(skill_result),
        )
        # DRF-1209 step 18 — replay capture (flag-gated, best-effort inside).
        # post_verdict="": the outbound guard ran INSIDE _dispatch_skill_handoff;
        # handoff_sent_text is already the post-guard line the person read.
        _capture_live_replay(
            trace_id=trace_id,
            event=event,
            surface="max_per_tenant",
            branch=skill_result.action_type or "handoff",
            pre_verdict=safety.verdict,
            post_verdict="",
            reply_text=handoff_sent_text,
            skill_name=_skill_selected_label(skill_result),
        )
        return

    # Silent path (Sprint 3 / D3): conversation is mid-handoff. Dispatcher
    # returns SkillResult(should_send=False) → we record nothing, send
    # nothing, log the silence + return. Operator drives until
    # resolve_admin_task flips state back.
    if skill_result is not None and not skill_result.should_send:
        silenced_by = (skill_result.meta or {}).get("silenced_by", "skill_request")
        logger.info(
            "channels.max.handler.silenced conversation=%s reason=%s",
            conversation.id,
            silenced_by,
        )
        # DRF-1486 — объяснить молчание нужно и здесь, и в первую очередь
        # здесь: инцидент 04.09 НАЧАЛСЯ в салонном боте. Там человеку сказали
        # «передаю менеджеру», и дальше он писал именно сюда — без ответа и
        # без единого признака, что его вообще слышат.
        #
        # Только на handoff-молчании, а не на любом ``should_send=False``:
        # навык, попросивший тишины по своим причинам, к оператору отношения
        # не имеет, и объяснять за него «с вами работает сотрудник» значило
        # бы соврать. Функция сама помнит, что уже сказала, — второе и пятое
        # входящее не получают ничего.
        if silenced_by == "human_handoff":
            notify_silence(
                conversation=conversation,
                bot_user=bot_user,
                chat_id=event.chat_id,
                trace_id=trace_id,
            )
        return

    reply_text = skill_result.reply_text if skill_result is not None else _echo_text(event)
    action_type = skill_result.action_type if skill_result is not None else ""
    action_data = skill_result.action_data if skill_result is not None else None
    closing = skill_result is not None and skill_result.should_close_conversation

    # DRF-1210 — the per-tenant client path gets the same outbound guard as the
    # global one. It is a different brain (skill registry, not the concierge)
    # but the same person on the other end, and a KB-driven answer is model
    # text like any other. No crisis exemption is needed here: the inbound
    # short-circuit above returns before reaching this line.
    _guarded = guard_outbound(reply_text, surface="max", bot_user=bot_user, trace_id=trace_id)
    if _guarded.blocked:
        reply_text = _guarded.text
        action_type = OUTBOUND_ACTION_TYPE
        action_data = None

    # Persist the assistant turn BEFORE sending — if send fails, we
    # still have the intended reply on record. The send failure causes
    # PEL retention via the consumer, retry will re-send (idempotent
    # at the MAX API level via channel_message_id deduplication, which
    # is on MAX's side, not ours).
    #
    # When the skill requested close_conversation (e.g. PrivacyConsentSkill
    # data_delete), the Conversation row has been wiped during dispatch.
    # Writing an assistant Message into it would violate the FK. We send
    # the reply (chat_id-based, doesn't need a Conversation), log the
    # closing path, and skip the persistence step.
    if not closing:
        record_message(
            conversation,
            role="assistant",
            content=reply_text,
            rendered_text=reply_text,
            action_type=action_type,
            action_data=action_data,
            trace_id=trace_id,
        )
        short_term.append(
            conversation.id,
            role="assistant",
            content=reply_text,
        )

        # Sprint 3 / D4: persist skill-requested state transition. The
        # update is a single UPDATE keyed on pk so concurrent state writes
        # from other turns can't trample. handoff_initiated already flipped
        # state inside C2's create_admin_task; this branch covers any
        # future skill that requests a transition without that side-effect.
        if skill_result is not None and skill_result.new_state is not None:
            Conversation.all_tenants.filter(pk=conversation.pk).update(state=skill_result.new_state)
            conversation.state = skill_result.new_state
    else:
        logger.info(
            "channels.max.handler.closing_conversation conversation=%s reply_len=%d",
            conversation.id,
            len(reply_text),
        )

    # Extract optional inline keyboard from the skill result. The
    # platform's keyboard contract (see apps/orchestrator/ui/keyboards.py)
    # stores the channel-agnostic ``[{label, callback}]`` list in
    # ``action_data["buttons"]``; the channel adapter converts it to the
    # native wire format. Mirrors apps/channels/telegram/handler._extract_keyboard.
    attachments = _build_attachments(action_data)

    # DRF-1209 step 2 — the skill answered; this is the turn's terminal
    # outcome. Written BEFORE the outbound send (concierge parity): a send
    # failure is a delivery problem, not a different turn outcome.
    _record_live_path_metric(
        bot_user=bot_user,
        conversation=conversation,
        trace_id=trace_id,
        message_text=event.text,
        t_start=t_start,
        tenant=conversation.tenant,
        outcome=AIRequestMetric.OUTCOME_SUCCESS,
        skill_selected=_skill_selected_label(skill_result),
    )

    # DRF-1486 — этот ход заканчивается тем, что диалог уходит в handoff, а
    # человеку прямо сейчас уходит строка об этом. Значит, подтверждение
    # доставлено ЗДЕСЬ, и на следующем сообщении молчание объясняется словами
    # «ваш вопрос уже у сотрудника», а не «вы просили в другом чате».
    #
    # Проверяется состояние диалога, а не имя навыка: задачу заводит и
    # ``HumanHandoffSkill``, и booking через ``should_handoff``, и порог
    # уверенности (DRF-1209), — общее у них ровно одно, флип в HUMAN_HANDOFF
    # внутри ``create_admin_task``. Список навыков здесь пришлось бы дополнять
    # при каждом новом источнике эскалации, и первый же забытый вернул бы
    # человеку неверную формулировку.
    #
    # До отправки — как ``record_message`` выше: ``send_message`` пробрасывает
    # MaxAPIError, автоматического ретрая нет, и упавший ход не должен
    # оставлять диалог с меткой «здесь ничего не говорили».
    if conversation.state == Conversation.State.HUMAN_HANDOFF:
        mark_handoff_announced(conversation=conversation, chat_id=event.chat_id)

    # Outbound — MaxAPIError propagates up (handler does not swallow).
    send_message(chat_id=event.chat_id, text=reply_text, attachments=attachments)

    # DRF-1209 step 18 — replay capture for the live per-tenant path
    # (flag-gated + best-effort inside, REPLAY_LIVE_CAPTURE_ENABLED default
    # OFF). After the send: zero added user-visible latency, and a capture
    # failure can never affect what the user saw. The silent handoff-mute
    # path above answers nothing, so it captures nothing.
    _capture_live_replay(
        trace_id=trace_id,
        event=event,
        surface="max_per_tenant",
        branch=action_type,
        pre_verdict=safety.verdict,
        post_verdict="block" if _guarded.blocked else "allow",
        reply_text=reply_text,
        skill_name=_skill_selected_label(skill_result),
        keyboard_size=len(attachments or []),
    )

    emit(
        "channels.max.outbound.sent",
        payload={
            "conversation_id": str(conversation.id),
            "chat_id": event.chat_id,
            "reply_kind": _reply_kind(event, skill_result, reply_text),
            "has_keyboard": bool(attachments),
        },
    )
    logger.info(
        "channels.max.handler.completed bot_user=%s conversation=%s reply_len=%d",
        bot_user.id,
        conversation.id,
        len(reply_text),
    )
