"""Telegram channel handler — Phase 1 / CH1 (DRF-848).

Bridges the webhook view (``apps.channels.telegram.webhook``) and the
skill pipeline (``apps.skills.registry.dispatch``), mirroring the shape
of :mod:`apps.channels.max.handler` so the skill layer stays channel-
blind.

### Pipeline (one call per inbound webhook)

    parse_inbound(payload)                       ← parser
        │
        ├─ None → handler returns immediately (ignored update type)
        ▼
    resolve_or_create_bot_user(channel="telegram", ...)
        │
        ▼
    resolve_active_conversation(bot_user)        ← apps.conversations
        │
        ▼
    record_message(role="user", content=...)
    short_term.append(role="user")
        │
        ▼
    skill_dispatch(SkillContext(..., has_attachments=bool(.attachments)))
        │
        ▼  (skill returned should_send=True)
    record_message(role="assistant", content=reply)
    short_term.append(role="assistant")
        │
        ▼
    send_message(chat_id, text=reply, reply_markup=converted_keyboard, tenant)
        │
        ▼  (for callback_query updates ONLY)
    answer_callback_query(callback_query_id, tenant)

### Tenant context contract

Unlike the MAX handler — which is invoked by a worker that already
entered ``tenant_scope`` from a Redis Stream entry — the Telegram
handler is called *directly from the webhook view* and the view enters
``tenant_scope`` itself. The handler also takes ``tenant`` as an
explicit argument because (a) the outbound calls need it for the
per-tenant bot token and (b) it provides a redundant cross-check that
the in-scope tenant matches the URL-resolved one.

### Skill-pipeline integration

The skill registry is shared with MAX — there is no per-channel
registry. ``SkillContext.has_attachments`` carries the bool fact for
photo messages so the food_scanner skill can pick its image branch
without peeking at channel-specific fields. The raw ``file_id`` list
sits on ``CanonicalEvent.attachments`` (the parser preserves it),
where a future PR can lift it into ``SkillContext`` once the
nutrition pipeline is wired (DRF-849+).

### Outbound failure handling

Unlike MAX (which raises ``MaxAPIError`` and lets the consumer's PEL
retain the entry for retry), Telegram's outbound returns ``False`` on
failure. The webhook view ALWAYS returns 200 to Telegram (we don't
want Telegram retrying indefinitely on our side bugs), so the handler
logs the failure and continues. The assistant message has already
been persisted to the Conversation, matching MAX's pre-send write
contract — a follow-up flow can detect un-sent assistant messages
via auditing if the failure rate spikes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from apps.audit.services import write_audit
from apps.channels.telegram import outbound
from apps.channels.telegram.keyboards import to_inline_keyboard
from apps.channels.telegram.parser import CanonicalEvent, parse_inbound
from apps.conversations.models import Conversation
from apps.conversations.services import record_message, resolve_active_conversation
from apps.events.services import emit
from apps.identity.services import resolve_or_create_bot_user
from apps.orchestrator.memory import short_term
from apps.orchestrator.turn_seam import (
    SURFACE_PER_TENANT,
    TurnContext,
    orchestrate_turn,
    turn_reply_to_skill_result,
)

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


_FALLBACK_EMPTY = "?"
_FALLBACK_NO_ECHO = "(нечем эхом) 🙂"


def handle_inbound(payload: dict[str, Any], tenant: "Tenant") -> None:
    """Process one Telegram webhook update end-to-end.

    Called by :func:`apps.channels.telegram.webhook.telegram_webhook`
    after secret-token verification and tenant resolution. The view
    has already entered ``tenant_scope(tenant)`` so downstream services
    that read ``current_tenant()`` see the right tenant.

    Args:
      payload: raw Telegram Update JSON.
      tenant: the tenant whose webhook URL slug matched. Required for
              per-tenant bot-token outbound; redundantly cross-checked
              against ``current_tenant()`` by the caller.

    Returns:
      None. The view returns 200 unconditionally — handler errors are
      logged + audited but never propagated to the network response
      (Telegram retries 5xx forever, which would flood the queue).
    """
    event = parse_inbound(payload)
    if event is None:
        # Ignored update type (channel_post, edited_message, etc.) or
        # malformed payload. Audit + return 200 silently — Telegram
        # doesn't retry on non-error, so this is the right contract.
        logger.info(
            "channels.telegram.handler.ignored tenant=%s keys=%s",
            tenant.id,
            sorted(payload.keys()) if isinstance(payload, dict) else None,
        )
        return

    logger.info(
        "channels.telegram.handler.received tenant=%s channel_user_id=%s text_len=%d attachments=%d is_callback=%s",
        tenant.id,
        event.channel_user_id,
        len(event.text),
        len(event.attachments),
        bool(event.raw.get("callback_query_id")),
    )

    bot_user = resolve_or_create_bot_user(
        channel="telegram",
        channel_user_id=event.channel_user_id,
        chat_id=event.chat_id,
    )

    # Defense in depth: cross-check that the resolved BotUser is owned by
    # the URL-resolved tenant. ``resolve_or_create_bot_user`` reads
    # ``current_tenant()`` (which the view set), so a mismatch here
    # would mean the view set a different scope from the argument it
    # passed — a bug. We assert via audit + bail rather than continue
    # with a confused identity.
    if bot_user.tenant_id != tenant.id:
        write_audit(
            "telegram.handler.tenant_mismatch",
            target="BotUser",
            target_id=bot_user.id,
            payload={
                "expected_tenant_id": str(tenant.id),
                "got_tenant_id": str(bot_user.tenant_id),
            },
        )
        logger.error(
            "channels.telegram.handler.tenant_mismatch bot_user_tenant=%s url_tenant=%s",
            bot_user.tenant_id,
            tenant.id,
        )
        return

    conversation = resolve_active_conversation(bot_user)
    assert conversation is not None  # noqa: S101 — contract guard

    record_message(
        conversation,
        role="user",
        content=event.text,
        trace_id=None,
    )
    short_term.append(conversation.id, role="user", content=event.text)

    # Routed via the normalized orchestration seam
    # (apps.orchestrator.turn_seam) — the same per-tenant skill-registry
    # brain as MAX; the SkillResult is rebuilt 1:1 so every branch below
    # is byte-identical to the pre-seam direct dispatch. The seam keeps
    # the skills.registry import lazy internally.
    skill_result = turn_reply_to_skill_result(
        orchestrate_turn(
            TurnContext(
                surface=SURFACE_PER_TENANT,
                conversation=conversation,
                bot_user=bot_user,
                text=event.text,
                channel="telegram",
                trace_id="",
                has_attachments=bool(event.attachments),
            )
        )
    )

    if skill_result is not None and not skill_result.should_send:
        logger.info(
            "channels.telegram.handler.silenced conversation=%s reason=%s",
            conversation.id,
            (skill_result.meta or {}).get("silenced_by", "skill_request"),
        )
        # Still clear the spinner for callback taps — leaving it spinning
        # for 30s while the bot is silently handed off is bad UX.
        _answer_callback_if_present(event, tenant)
        return

    reply_text = (
        skill_result.reply_text
        if skill_result is not None and skill_result.reply_text
        else _fallback_reply(event)
    )
    action_type = skill_result.action_type if skill_result is not None else ""
    action_data = skill_result.action_data if skill_result is not None else None
    closing = skill_result is not None and skill_result.should_close_conversation

    if not closing:
        record_message(
            conversation,
            role="assistant",
            content=reply_text,
            rendered_text=reply_text,
            action_type=action_type,
            action_data=action_data,
            trace_id=None,
        )
        short_term.append(conversation.id, role="assistant", content=reply_text)

        if skill_result is not None and skill_result.new_state is not None:
            Conversation.all_tenants.filter(pk=conversation.pk).update(state=skill_result.new_state)
            conversation.state = skill_result.new_state
    else:
        logger.info(
            "channels.telegram.handler.closing_conversation conversation=%s reply_len=%d",
            conversation.id,
            len(reply_text),
        )

    reply_markup = _extract_keyboard(action_data)

    sent_ok = outbound.send_message(
        chat_id=event.chat_id,
        text=reply_text,
        tenant=tenant,
        reply_markup=reply_markup,
    )
    if not sent_ok:
        # Already logged inside outbound. Emit an event for the metrics
        # pipeline; do NOT raise — the view returns 200 to Telegram and
        # an operator dashboard tracks the failure rate.
        emit(
            "channels.telegram.outbound.failed",
            payload={
                "conversation_id": str(conversation.id),
                "chat_id": event.chat_id,
            },
        )
    else:
        emit(
            "channels.telegram.outbound.sent",
            payload={
                "conversation_id": str(conversation.id),
                "chat_id": event.chat_id,
                "has_keyboard": bool(reply_markup),
            },
        )

    # Callback taps require answerCallbackQuery to clear the spinner.
    _answer_callback_if_present(event, tenant)

    logger.info(
        "channels.telegram.handler.completed tenant=%s bot_user=%s conversation=%s reply_len=%d sent_ok=%s",
        tenant.id,
        bot_user.id,
        conversation.id,
        len(reply_text),
        sent_ok,
    )


def _fallback_reply(event: CanonicalEvent) -> str:
    """Pick a fallback reply when the skill dispatcher returns nothing.

    Mirrors :func:`apps.channels.max.handler._echo_text` minus the
    ``/start`` welcome — the Telegram adapter delegates welcome to the
    skill layer (the LLM concierge / FAQ skill picks up the slack).
    Empty text + no attachments → ``"?"``; attachments only →
    ``"(нечем эхом) 🙂"``; otherwise echo the text.
    """
    text = (event.text or "").strip()
    if text:
        return event.text
    if event.attachments:
        return _FALLBACK_NO_ECHO
    return _FALLBACK_EMPTY


def _extract_keyboard(action_data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pull the channel-agnostic keyboard from ``SkillResult.action_data``.

    The platform contract (see :func:`apps.skills.booking.skill._action_data_for_pending`)
    stores keyboards as::

        {"attachments": [
            {"type": "inline_keyboard", "payload": {"buttons": [...]}}
        ]}

    where ``buttons`` is the channel-agnostic ``list[dict]`` shape that
    :func:`apps.channels.telegram.keyboards.to_inline_keyboard` knows
    how to convert. Returns the converted Telegram ``reply_markup``
    dict ready to drop into the JSON body, or ``None`` when the skill
    didn't ask for a keyboard.
    """
    if not action_data:
        return None
    attachments = action_data.get("attachments") or []
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        if attachment.get("type") != "inline_keyboard":
            continue
        payload = attachment.get("payload") or {}
        buttons = payload.get("buttons")
        if buttons:
            return to_inline_keyboard(buttons)
    return None


def _answer_callback_if_present(event: CanonicalEvent, tenant: "Tenant") -> None:
    """Send ``answerCallbackQuery`` when the inbound event came from a button tap.

    Telegram requires this call within ~30s of the callback_query, or
    the user's button stays in the loading state. We do it AFTER the
    outbound reply so the user sees the bot's response first, then the
    spinner clears. Failure to clear is non-fatal — logged, not raised.
    """
    cqid = event.raw.get("callback_query_id")
    if not cqid:
        return
    outbound.answer_callback_query(callback_query_id=str(cqid), tenant=tenant)
