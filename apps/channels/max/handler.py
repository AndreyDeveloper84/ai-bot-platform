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
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.conf import settings

from apps.channels.max.global_onboarding import needs_onboarding, run_onboarding_turn
from apps.channels.max.outbound import (
    make_inline_keyboard_attachment_rows,
    send_message,
)
from apps.channels.max.parser import CanonicalEvent, ParseError, parse_max_webhook
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
from apps.identity.services import (
    resolve_or_create_bot_user,
    resolve_or_create_global_bot_user,
)
from apps.orchestrator.discovery import (
    CALLBACK_DISCOVER_BOOK_PREFIX,
    DiscoveryReply,
    generate_discovery_reply,
)
from apps.orchestrator.handoff import handoff_to_booking
from apps.orchestrator.memory import short_term
from apps.orchestrator.safety.gate import evaluate_inbound
from apps.tools.idempotency import AlreadyClaimed, with_idempotency

logger = logging.getLogger(__name__)


# Welcome message — ported verbatim from
# `legacy_maxbot/texts.py::GREETING_NEW_USER` (running in prod since
# 2026-04). Sprint 3 AI Concierge will replace this with personalised
# welcome flow via tenant.brand_voice persona.
_WELCOME_TEXT = (
    "Здравствуйте! 👋\n\n"
    "Это бот массажного салона «Формула тела» в Пензе.\n"
    "Помогу записаться, расскажу об услугах и отвечу на частые вопросы.\n\n"
    "Выберите раздел:"
)

_FALLBACK_NO_ECHO = "(нечем эхом) 🙂"
_FALLBACK_EMPTY = "?"


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

    bot_user = resolve_or_create_global_bot_user(
        channel=event.channel,
        channel_user_id=event.channel_user_id,
        chat_id=event.chat_id,
    )
    conversation = resolve_active_global_conversation(bot_user)
    assert conversation is not None  # noqa: S101 — create_if_missing=True never returns None

    # Prior short-term history (before this turn) feeds the discovery prompt.
    history = short_term.recall(conversation.id)

    # Persist + remember the inbound turn (sentinel-scoped, current_tenant()=None).
    record_global_message(conversation, role="user", content=event.text, trace_id=trace_id)
    short_term.append(conversation.id, role="user", content=event.text)

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

    # Reply, in priority order:
    #   0. Safety pre-check (#1053) — a red-flag phrase (suicide / self-harm /
    #      emergency) or a BLOCK phrase (drugs / diagnosis / legal) short-circuits
    #      to a canned reply BEFORE discovery. Variant A on the tenant-less path:
    #      canned reply only, NO AdminTask (founder decision 2026-07-03, #1076).
    #   1. Onboarding (#1046, behind GLOBAL_BOT_ONBOARDING flag) — welcome + 152-ФЗ
    #      consent capture. Variant A «soft gate»: we greet + capture consent but
    #      do NOT block discovery on it. When onboarding runs we do NOT call
    #      generate_discovery_reply this turn.
    #   2. Discovery → booking handoff (the user tapped a master card → transition
    #      into tenant T's booking flow, #1020).
    #   3. A normal tenant-less discovery turn (which may itself surface cards).
    safety = evaluate_inbound(event.text)
    if not safety.allowed:
        _emit_safety_shortcircuit(bot_user, safety, is_global=True)
        reply = DiscoveryReply(text=safety.reply_text)
    elif getattr(settings, "GLOBAL_BOT_ONBOARDING", False) and needs_onboarding(
        bot_user, event.text
    ):
        reply = run_onboarding_turn(conversation, bot_user, event.text, trace_id)
    elif event.text.startswith(CALLBACK_DISCOVER_BOOK_PREFIX):
        reply = _discovery_handoff_reply(event, bot_user, trace_id)
    else:
        reply = generate_discovery_reply(
            event.text,
            history=history,
            trace_id=str(trace_id) if trace_id else None,
        )

    # Persist + remember the assistant turn, then send to MAX (with any keyboard).
    record_global_message(
        conversation,
        role="assistant",
        content=reply.text,
        rendered_text=reply.text,
        trace_id=trace_id,
    )
    short_term.append(conversation.id, role="assistant", content=reply.text)
    send_message(
        chat_id=event.chat_id,
        text=reply.text,
        attachments=_build_attachments(reply.action_data),
    )


def _discovery_handoff_reply(
    event: CanonicalEvent, global_bot_user, trace_id: str | uuid.UUID | None
) -> DiscoveryReply:
    """Parse the ``cb:discover:book:{tenant_id}:{master_id}`` callback and route
    into tenant T's booking flow via the handoff layer (#1020).

    Both ids are UUIDs (no ``:`` inside), so a single split after the prefix is
    unambiguous. Malformed payloads degrade to a graceful reply.
    """
    payload = event.text[len(CALLBACK_DISCOVER_BOOK_PREFIX) :]
    try:
        tenant_part, master_part = payload.split(":", 1)
        tenant_id = uuid.UUID(tenant_part)
        master_id = uuid.UUID(master_part)
    except (ValueError, AttributeError):
        logger.warning("channels.max.global.handoff.bad_payload payload=%r", payload)
        return DiscoveryReply(
            text="Не удалось открыть запись — попробуйте выбрать мастера ещё раз."
        )

    return handoff_to_booking(
        global_bot_user=global_bot_user,
        tenant_id=tenant_id,
        master_id=master_id,
        chat_id=event.chat_id,
        trace_id=trace_id,
    )


def _handle_max_event_inner(event: CanonicalEvent, trace_id: str | uuid.UUID | None) -> None:
    """Inner pipeline — parse-already-done caller. Side-effects only."""

    # MAX UX indicators: tell the chat we've read the message and we're
    # typing a reply BEFORE doing any heavy work (LLM call, DB writes).
    # Both are best-effort fire-and-forget — failures are logged inside
    # send_chat_action and do not propagate. Done first so the user
    # sees the «прочитано / печатает…» chrome that mysite's MAX SDK
    # provided automatically (post-cutover regression 2026-05-20).
    if event.chat_id:
        from apps.channels.max.outbound import send_chat_action

        send_chat_action(chat_id=event.chat_id, action="mark_seen")
        send_chat_action(chat_id=event.chat_id, action="typing_on")

    bot_user = resolve_or_create_bot_user(
        channel=event.channel,
        channel_user_id=event.channel_user_id,
        chat_id=event.chat_id,
    )
    conversation = resolve_active_conversation(bot_user)
    # `create_if_missing=True` (default) → never returns None. The
    # narrow tells mypy this; an assertion in case the contract slips.
    assert conversation is not None  # noqa: S101 — contract guard

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
    safety = evaluate_inbound(event.text)
    if not safety.allowed:
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
        send_message(chat_id=event.chat_id, text=safety.reply_text)
        logger.info(
            "channels.max.handler.safety_shortcircuit conversation=%s verdict=%s",
            conversation.id,
            safety.verdict,
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

    # Sprint 3 / D1 — dispatch through the skill registry. Lazy import
    # of skills.registry breaks the echo-skill ↔ handler.py module-load
    # cycle (echo skill needs the legacy welcome/fallback strings that
    # live up here). The EchoSkill is the final catch-all in registration
    # order so dispatch() always returns a SkillResult under normal load;
    # the `_echo_text` fallback below stays only for the defensive
    # "registry empty" edge case (e.g. tests that reset the registry).
    from apps.skills.base import SkillContext
    from apps.skills.registry import dispatch as skill_dispatch

    skill_result = skill_dispatch(
        SkillContext(
            conversation=conversation,
            bot_user=bot_user,
            message_text=event.text,
            trace_id=str(trace_id) if trace_id else "",
            has_attachments=bool(event.attachments),
        )
    )

    # Silent path (Sprint 3 / D3): conversation is mid-handoff. Dispatcher
    # returns SkillResult(should_send=False) → we record nothing, send
    # nothing, log the silence + return. Operator drives until
    # resolve_admin_task flips state back.
    if skill_result is not None and not skill_result.should_send:
        logger.info(
            "channels.max.handler.silenced conversation=%s reason=%s",
            conversation.id,
            (skill_result.meta or {}).get("silenced_by", "skill_request"),
        )
        return

    reply_text = skill_result.reply_text if skill_result is not None else _echo_text(event)
    action_type = skill_result.action_type if skill_result is not None else ""
    action_data = skill_result.action_data if skill_result is not None else None
    closing = skill_result is not None and skill_result.should_close_conversation

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

    # Outbound — MaxAPIError propagates up (handler does not swallow).
    send_message(chat_id=event.chat_id, text=reply_text, attachments=attachments)

    emit(
        "channels.max.outbound.sent",
        payload={
            "conversation_id": str(conversation.id),
            "chat_id": event.chat_id,
            "reply_kind": (
                "welcome"
                if reply_text == _WELCOME_TEXT
                else "echo"
                if event.text.strip()
                else "no_echo"
                if event.attachments
                else "empty_prompt"
            ),
            "has_keyboard": bool(attachments),
        },
    )
    logger.info(
        "channels.max.handler.completed bot_user=%s conversation=%s reply_len=%d",
        bot_user.id,
        conversation.id,
        len(reply_text),
    )
