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

from apps.channels.max.outbound import send_message
from apps.channels.max.parser import CanonicalEvent, parse_max_webhook
from apps.conversations.services import record_message, resolve_active_conversation
from apps.events.services import emit
from apps.identity.services import resolve_or_create_bot_user
from apps.orchestrator.memory import short_term

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
    """

    event = parse_max_webhook(payload)
    logger.info(
        "channels.max.handler.received channel_user_id=%s text_len=%d attachments=%d",
        event.channel_user_id,
        len(event.text),
        len(event.attachments),
    )

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

    # Sprint 2 echo decision — single function, no skill dispatch yet.
    reply_text = _echo_text(event)

    # Persist the assistant turn BEFORE sending — if send fails, we
    # still have the intended reply on record. The send failure causes
    # PEL retention via the consumer, retry will re-send (idempotent
    # at the MAX API level via channel_message_id deduplication, which
    # is on MAX's side, not ours).
    record_message(
        conversation,
        role="assistant",
        content=reply_text,
        rendered_text=reply_text,
        trace_id=trace_id,
    )
    short_term.append(
        conversation.id,
        role="assistant",
        content=reply_text,
    )

    # Outbound — MaxAPIError propagates up (handler does not swallow).
    send_message(chat_id=event.chat_id, text=reply_text)

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
        },
    )
    logger.info(
        "channels.max.handler.completed bot_user=%s conversation=%s reply_len=%d",
        bot_user.id,
        conversation.id,
        len(reply_text),
    )
