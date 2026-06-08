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

from django.db import IntegrityError, transaction
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
      bot_user: identity.BotUser instance. Conversations retro B2: the
                function NOW validates ``bot_user.tenant_id ==
                current_tenant.id`` and raises ``CrossTenantError`` on
                mismatch. Callers (channel handlers) are still expected
                to supply a BotUser already resolved under the current
                tenant_scope; the in-function check is defence-in-depth
                that turns a silent cross-tenant Conversation-create
                into a loud error.
      create_if_missing: when False and no active Conversation exists,
                returns None instead of creating one. Used by
                read-only inspection paths.

    Returns:
      The Conversation instance, or None when missing and
      ``create_if_missing=False``.

    Raises:
      ValueError: ``current_tenant()`` is None.
      CrossTenantError: ``bot_user.tenant_id != current_tenant().id``.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "resolve_active_conversation requires a tenant in scope. "
            "Wrap the call in `tenant_scope(t)` before invocation."
        )

    # Conversations retro B2: defence-in-depth tenant check. The
    # ``Conversation.objects`` manager is ``TenantScopedManager`` and
    # already auto-filters by ``tenant=current_tenant()`` — but the
    # bot_user argument is the source of latent cross-tenant risk: a
    # caller (channel handler bug, misrouted webhook) handing us a
    # bot_user from another tenant would silently miss the existing
    # row + try to ``Conversation.objects.create(...)`` linking a
    # cross-tenant bot_user to the current tenant. The FK protection
    # would catch it eventually, but the audit row says nothing about
    # the mismatch. Fail loud at the gate.
    bot_user_tenant_id = getattr(bot_user, "tenant_id", None)
    if bot_user_tenant_id is not None and bot_user_tenant_id != tenant.id:
        raise CrossTenantError(
            f"resolve_active_conversation: bot_user belongs to tenant "
            f"{bot_user_tenant_id!r} but current_tenant is {tenant.id!r}"
        )

    # Explicit ``tenant=tenant`` makes the contract load-bearing —
    # a future refactor that swaps ``objects`` for ``all_tenants``
    # (e.g., for an admin pass) does NOT silently leak across tenants.
    existing = (
        Conversation.objects.filter(
            tenant=tenant,
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

    # Sprint 2.5 H3: two concurrent webhook turns from the same
    # bot_user both pass the existing-row check and both try to
    # create. The partial unique constraint
    # `(bot_user, tenant) WHERE is_active AND deleted_at IS NULL`
    # raises IntegrityError on the loser, which previously
    # propagated as an uncaught 500 and forced a PEL retry. Wrap
    # the create in atomic + IntegrityError handler so the loser
    # re-fetches the winner's row and returns it cleanly.
    try:
        with transaction.atomic():
            conversation = Conversation.objects.create(tenant=tenant, bot_user=bot_user)
    except IntegrityError:
        # Race winner already created the active conversation; re-fetch.
        # Same explicit ``tenant=tenant`` defence as the pre-check above.
        existing = (
            Conversation.objects.filter(
                tenant=tenant,
                bot_user=bot_user,
                is_active=True,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if existing is None:
            # Extremely unlikely — winner's row would have to be
            # rolled back between IntegrityError and re-fetch. Surface
            # the original IntegrityError context for debugging.
            raise
        logger.info(
            "conversations.conversation.race_lost bot_user=%s tenant=%s — returning winner %s",
            bot_user.id,
            tenant.id,
            existing.id,
        )
        return existing

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

    # Sprint 2.5 M1: wrap Message INSERT + Conversation UPDATE in a
    # single transaction so a failure between the two doesn't leave
    # last_message_at stale relative to the new Message row. Emit the
    # event via `transaction.on_commit` so Sprint 5 replay subscribers
    # never see Messages without the matching Conversation update.
    now = timezone.now()
    with transaction.atomic():
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
        # Atomic UPDATE (not .save()) — so concurrent appends from
        # racing webhook turns don't overwrite each other's
        # last_message_at value.
        Conversation.all_tenants.filter(pk=conversation.pk).update(last_message_at=now)
    conversation.last_message_at = now

    # `on_commit` defers the event emit until after the outer
    # transaction commits — guarantees subscribers see the Message row
    # in DB before the event arrives. If we're not inside a wider
    # transaction (typical Django autocommit), this fires immediately.
    transaction.on_commit(
        lambda: emit(
            "conversations.message.stored",
            payload={
                "message_id": str(message.id),
                "conversation_id": str(conversation.id),
                "role": role,
                "has_action": bool(action_type),
            },
        )
    )

    # M6 AI drafts auto-trigger (deferred follow-up from PR #535 / #540).
    # Spec §M6 line 660: «— помощник готовит ответ —» — every inbound
    # customer message kicks off a proactive draft generation in the
    # background.  The Celery task re-checks the feature flag, master
    # involvement, tier, staleness and per-conversation debounce inside
    # the worker; this hook only ENQUEUES on the role=USER fast path.
    # We import the task module lazily inside the if-block to avoid a
    # module-load circular: master_api imports conversations heavily.
    # `transaction.on_commit` defers the enqueue until after the DB
    # commit so the worker can never read a Message that hasn't
    # actually landed.
    if role == Message.Role.USER:
        captured_msg_id = message.id
        captured_conv_id = conversation.id
        captured_tenant_id = tenant.id

        def _enqueue_auto_draft() -> None:
            # Lazy import — keeps the producer-side import graph thin
            # and avoids the master_api → conversations circular at
            # app boot.
            from apps.master_api.tasks import auto_generate_draft_for_inbound

            auto_generate_draft_for_inbound.delay(
                conversation_id=str(captured_conv_id),
                trigger_message_id=str(captured_msg_id),
                tenant_id=str(captured_tenant_id),
            )

        transaction.on_commit(_enqueue_auto_draft)
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


def write_skill_state(
    conversation: Conversation,
    subkey: str,
    value: Any | None,
) -> None:
    """Atomically write ``conversation.skill_state[subkey] = value``.

    Conversations retro B1: skills used to do a read-modify-write on
    the JSONField — ``raw = conversation.skill_state; raw[key] = val;
    conversation.save(update_fields=["skill_state"])``. Two concurrent
    skills writing different sub-keys would silently clobber each
    other: each read the same pre-image, each wrote their own pre-
    image+delta, the second writer's ``update_fields=["skill_state"]``
    replaced the whole JSON column with its single-key view.

    Post-fix: wrap the read-modify-write in ``transaction.atomic`` +
    ``select_for_update`` on the Conversation row. Per-row lock
    serialises concurrent subkey writes; the in-memory ``conversation``
    parameter is refreshed from DB so the caller doesn't see stale
    state. Tenant invariant re-asserted defensively.

    ``value=None`` deletes the subkey (no-op when it doesn't exist).
    The full ``skill_state`` JSON is never replaced with ``None``.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "write_skill_state requires a tenant in scope. "
            "Wrap the call in `tenant_scope(t)` before invocation."
        )
    if getattr(conversation, "tenant_id", None) is not None and conversation.tenant_id != tenant.id:
        raise CrossTenantError(
            f"write_skill_state: conversation.tenant_id={conversation.tenant_id!r} "
            f"does not match current_tenant={tenant.id!r}"
        )

    with transaction.atomic():
        locked = (
            Conversation.all_tenants.select_for_update()
            .only("id", "skill_state")
            .get(pk=conversation.pk)
        )
        current = locked.skill_state if isinstance(locked.skill_state, dict) else {}
        if value is None:
            if subkey not in current:
                return
            new_state = {k: v for k, v in current.items() if k != subkey}
        else:
            new_state = dict(current)
            new_state[subkey] = value
        Conversation.all_tenants.filter(pk=conversation.pk).update(
            skill_state=new_state,
        )
        # Refresh the caller's in-memory instance so subsequent reads
        # see the new state without an extra DB round-trip.
        conversation.skill_state = new_state


# ─── Global (tenant-less) discovery persistence (#1026 / EPIC #1014) ──────
#
# Siblings of resolve_active_conversation / record_message for the nationwide
# discovery bot. The conversation runs at current_tenant()=None (discovery);
# its Conversation/Message rows are parked under the `global_bot` sentinel
# tenant — written via the non-scoped `all_tenants` manager with an explicit
# tenant=sentinel, so TenantScopedManager._enforce_write_tenant never fires and
# current_tenant() stays None throughout (the commercial-read invariant holds).
# The per-tenant functions above are intentionally left untouched.


def resolve_active_global_conversation(
    global_bot_user,
    *,
    create_if_missing: bool = True,
) -> Conversation | None:
    """Find-or-create the open global Conversation for ``global_bot_user``.

    Tenant-less sibling of :func:`resolve_active_conversation` for the
    nationwide discovery bot (#1026). Does NOT require/enter a ``tenant_scope``;
    the Conversation is parked under the ``global_bot`` sentinel tenant via
    ``Conversation.all_tenants`` + explicit ``tenant=sentinel``.

    Raises:
      ValueError: ``global_bot_user`` is not owned by the sentinel tenant
                  (defence-in-depth — pass a BotUser from
                  ``resolve_or_create_global_bot_user``).
    """

    from apps.identity.services.global_tenant import get_global_bot_tenant

    sentinel = get_global_bot_tenant()
    if global_bot_user.tenant_id != sentinel.id:
        raise ValueError(
            "resolve_active_global_conversation: bot_user.tenant_id="
            f"{global_bot_user.tenant_id!r} is not the global_bot sentinel "
            f"{sentinel.id!r}. Pass a BotUser from resolve_or_create_global_bot_user."
        )

    existing = (
        Conversation.all_tenants.filter(
            tenant=sentinel,
            bot_user=global_bot_user,
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

    try:
        with transaction.atomic():
            conversation = Conversation.all_tenants.create(
                tenant=sentinel,
                bot_user=global_bot_user,
            )
    except IntegrityError:
        # Race winner already created the single active row — re-fetch it.
        existing = (
            Conversation.all_tenants.filter(
                tenant=sentinel,
                bot_user=global_bot_user,
                is_active=True,
                deleted_at__isnull=True,
            )
            .order_by("-created_at")
            .first()
        )
        if existing is None:
            raise
        return existing

    emit(
        "conversations.conversation.created",
        payload={
            "conversation_id": str(conversation.id),
            "bot_user_id": str(global_bot_user.id),
            "is_global_bot": True,
        },
    )
    write_audit(
        "conversation.created",
        target="Conversation",
        target_id=conversation.id,
        payload={"is_global_bot": True},
    )
    logger.info(
        "conversations.conversation.created(global) id=%s bot_user=%s tenant=%s",
        conversation.id,
        global_bot_user.id,
        sentinel.id,
    )
    return conversation


def record_global_message(
    conversation: Conversation,
    *,
    role: str,
    content: str,
    rendered_text: str = "",
    trace_id: uuid.UUID | str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int | None = None,
) -> Message:
    """Persist a turn under a global (sentinel) Conversation (#1026).

    Tenant-less sibling of :func:`record_message`. Writes via
    ``Message.all_tenants`` / ``Conversation.all_tenants`` with explicit
    ``tenant=sentinel`` so ``current_tenant()`` stays None. Re-asserts the
    ``Message.tenant == Conversation.tenant == sentinel`` invariant.
    """

    from apps.identity.services.global_tenant import get_global_bot_tenant

    sentinel = get_global_bot_tenant()
    if conversation.tenant_id != sentinel.id:
        raise ValueError(
            "record_global_message: conversation.tenant_id="
            f"{conversation.tenant_id!r} is not the global_bot sentinel "
            f"{sentinel.id!r}. Pass a Conversation from "
            "resolve_active_global_conversation."
        )

    if trace_id is None:
        ctx_trace = current_trace_id()
        trace_id = uuid.UUID(ctx_trace) if ctx_trace else None
    elif isinstance(trace_id, str):
        trace_id = uuid.UUID(trace_id)

    now = timezone.now()
    with transaction.atomic():
        message = Message.all_tenants.create(
            conversation=conversation,
            tenant=sentinel,
            role=role,
            content=content,
            rendered_text=rendered_text,
            trace_id=trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
        Conversation.all_tenants.filter(pk=conversation.pk).update(last_message_at=now)
    conversation.last_message_at = now

    transaction.on_commit(
        lambda: emit(
            "conversations.message.stored",
            payload={
                "message_id": str(message.id),
                "conversation_id": str(conversation.id),
                "role": role,
                "is_global_bot": True,
            },
        )
    )
    logger.info(
        "conversations.message.stored(global) id=%s conversation=%s role=%s",
        message.id,
        conversation.id,
        role,
    )
    return message
