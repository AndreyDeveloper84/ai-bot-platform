"""Conversation services — single entry points for B/D callers (DRF-436).

Three operations are surfaced; nothing else in the platform should touch
`Conversation` or `Message` directly:

  * :func:`resolve_active_conversation` — find-or-create the open thread
    for a given BotUser inside the current tenant scope.
  * :func:`record_message` — persist a single turn, enforce the
    `Message.tenant_id == Conversation.tenant_id` invariant, bump
    `Conversation.last_message_at` via atomic UPDATE (no save() race),
    propagate `trace_id` from ContextVar.
  * :func:`close_conversation` — flip `is_active=False` + set outcome.

Why "service module" instead of model methods:

  * `record_message()` writes across two tables (Message insert +
    Conversation update). Putting that on a model method conflates
    "I'm a noun" with "I'm a transaction".
  * The tenant invariant check belongs in one place. Every channel
    handler / skill calls `record_message()` — putting the check on
    Message.save() risks subclasses bypassing it.
  * Events + AuditLog emission are infrastructure concerns, not
    domain-model concerns.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.db.models import F
from django.utils import timezone

from apps.audit.services import write_audit
from apps.conversations.models import Conversation, Message
from apps.events.services import emit
from apps.tenancy.context import current_tenant, current_trace_id
from apps.tenancy.exceptions import CrossTenantError

logger = logging.getLogger(__name__)


def resolve_active_conversation(
    bot_user,
    *,
    create_if_missing: bool = True,
) -> Conversation | None:
    """Return the open Conversation for `(bot_user, current_tenant)`.

    Reads `current_tenant()` from ContextVar; raises `ValueError` when
    None (same loud-fail pattern as identity.resolve_or_create_bot_user
    — phantom rows are worse than a stack trace).

    Args:
      bot_user: identity.BotUser instance. The function does NOT
                validate `bot_user.tenant_id == current_tenant.id` —
                callers (channel handlers) are responsible for
                supplying a BotUser already resolved under the current
                tenant_scope. A cross-tenant bot_user here is a
                programmer error, not a runtime concern.
      create_if_missing: when False and no active Conversation exists,
                returns None instead of creating one. Used by
                read-only inspection paths.

    Returns:
      The Conversation instance, or None when missing and
      ``create_if_missing=False``.

    Raises:
      ValueError: ``current_tenant()`` is None.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "resolve_active_conversation requires a tenant in scope. "
            "Wrap the call in `tenant_scope(t)` before invocation."
        )

    existing = (
        Conversation.objects.filter(
            bot_user=bot_user,
            is_active=True,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing

    if not create_if_missing:
        return None

    conversation = Conversation.objects.create(tenant=tenant, bot_user=bot_user)
    emit(
        "conversations.conversation.created",
        payload={
            "conversation_id": str(conversation.id),
            "bot_user_id": str(bot_user.id),
        },
    )
    write_audit(
        "conversation.created",
        target="Conversation",
        target_id=conversation.id,
    )
    logger.info(
        "conversations.conversation.created id=%s bot_user=%s tenant=%s",
        conversation.id,
        bot_user.id,
        tenant.id,
    )
    return conversation


def record_message(
    conversation: Conversation,
    *,
    role: str,
    content: str,
    rendered_text: str = "",
    action_type: str = "",
    action_data: dict[str, Any] | None = None,
    tool_call: dict[str, Any] | None = None,
    tool_call_id: str = "",
    trace_id: uuid.UUID | str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int | None = None,
) -> Message:
    """Persist a single message turn under `conversation`.

    Enforces the cross-FK invariant `Message.tenant == Conversation.tenant
    == current_tenant`. Bumps `conversation.last_message_at` via atomic
    UPDATE — never via instance .save() — so concurrent turns from a
    misbehaving channel don't trample each other's writes.

    Args:
      conversation: the open Conversation to append to.
      role: one of Message.Role values.
      content: source-of-truth message body.
      rendered_text: channel-rendered body — what the user saw.
      action_type / action_data: UI action attached to assistant msgs.
      tool_call / tool_call_id: raw OpenAI tool_call for forensic.
      trace_id: explicit trace ID; when None, reads ``current_trace_id()``
                from ContextVar. UUID string is normalised to UUID.
      tokens_in / tokens_out / latency_ms: telemetry, Sprint 3+ AI track.

    Returns:
      The created Message.

    Raises:
      ValueError: ``current_tenant()`` is None.
      CrossTenantError: ``conversation.tenant_id != current_tenant.id``
                        — defends against handler bugs that resolve a
                        Conversation in one tenant_scope and then write
                        to it inside a different scope.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "record_message requires a tenant in scope. Wrap the call "
            "in `tenant_scope(t)` before invocation."
        )

    if conversation.tenant_id != tenant.id:
        raise CrossTenantError(
            f"record_message attempted on Conversation tenant="
            f"{conversation.tenant_id} while current_tenant()={tenant.id}. "
            "Handler bug — verify tenant_scope before resolving the "
            "Conversation."
        )

    if trace_id is None:
        ctx_trace = current_trace_id()
        trace_id = uuid.UUID(ctx_trace) if ctx_trace else None
    elif isinstance(trace_id, str):
        trace_id = uuid.UUID(trace_id)

    message = Message.objects.create(
        conversation=conversation,
        tenant=tenant,
        role=role,
        content=content,
        rendered_text=rendered_text,
        action_type=action_type,
        action_data=action_data,
        tool_call=tool_call,
        tool_call_id=tool_call_id,
        trace_id=trace_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
    )

    # Atomic UPDATE — never a model .save() — so concurrent appends
    # from racing webhook turns don't overwrite each other. Refresh the
    # passed-in instance so callers can read .last_message_at.
    now = timezone.now()
    Conversation.all_tenants.filter(pk=conversation.pk).update(last_message_at=now)
    conversation.last_message_at = now

    emit(
        "conversations.message.stored",
        payload={
            "message_id": str(message.id),
            "conversation_id": str(conversation.id),
            "role": role,
            "has_action": bool(action_type),
        },
    )
    logger.info(
        "conversations.message.stored id=%s conversation=%s role=%s",
        message.id,
        conversation.id,
        role,
    )
    return message


def close_conversation(
    conversation: Conversation,
    *,
    outcome: str,
) -> None:
    """Close the Conversation with an outcome verdict.

    The conditional unique constraint on Conversation lets a future
    `resolve_active_conversation` create a fresh active row for the
    same `(bot_user, tenant)` pair once this one is closed (because
    `is_active=False` excludes the row from the partial unique index).

    Args:
      conversation: the Conversation to close.
      outcome: one of Conversation.Outcome values.
    """

    if outcome not in Conversation.Outcome.values:
        raise ValueError(
            f"close_conversation: outcome={outcome!r} is not a valid "
            f"Conversation.Outcome value. Choose from "
            f"{list(Conversation.Outcome.values)}."
        )

    Conversation.all_tenants.filter(pk=conversation.pk).update(
        is_active=False,
        outcome=outcome,
        last_message_at=F("last_message_at"),  # touch nothing else
    )
    conversation.is_active = False
    conversation.outcome = outcome

    emit(
        "conversations.conversation.closed",
        payload={
            "conversation_id": str(conversation.id),
            "outcome": outcome,
        },
    )
    write_audit(
        "conversation.closed",
        target="Conversation",
        target_id=conversation.id,
        payload={"outcome": outcome},
    )
    logger.info(
        "conversations.conversation.closed id=%s outcome=%s",
        conversation.id,
        outcome,
    )
