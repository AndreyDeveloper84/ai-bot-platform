"""Staff assistant thread services (DRF-1061 step 0).

The employee-side sibling of :mod:`apps.conversations.services`. Same three
operations, same invariants, separate tables — see
:class:`~apps.conversations.models.StaffAssistantThread` for why the two
must not share a row.

A separate module rather than four more functions in ``services.py``: that
file opens with "three operations are surfaced; nothing else in the
platform should touch Conversation or Message directly", and staff threads
are neither. Keeping them apart is what stops a future caller from reaching
for ``record_message`` when they meant this one.

### What is deliberately NOT here

No ``short_term`` mirror. The customer path keeps its recent window in
Redis with a 24-hour TTL, which fits a conversation that happens in one
sitting. An employee's does not: it is interrupted between clients and
resumed across shifts, and «I asked for Tuesday off yesterday» has to
still be there. Postgres is the whole memory here.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.conversations.models import StaffAssistantMessage, StaffAssistantThread
from apps.tenancy.context import current_tenant, current_trace_id
from apps.tenancy.exceptions import CrossTenantError

logger = logging.getLogger(__name__)

#: How many turns the assistant gets to see. Ten matches the customer
#: concierge; beyond that the prompt grows faster than the answer improves.
DEFAULT_HISTORY_LIMIT = 10


def resolve_active_staff_thread(
    bot_user,
    *,
    role_at_open: str = "",
    create_if_missing: bool = True,
) -> StaffAssistantThread | None:
    """Find-or-create this employee's open thread in the current tenant.

    Mirrors :func:`apps.conversations.services.resolve_active_conversation`
    including its two defences: a loud failure when no tenant is in scope,
    and a cross-tenant check on ``bot_user`` — the argument is the one
    thing the tenant-scoped manager cannot validate for us.

    ``role_at_open`` is recorded only when the thread is created. Re-reading
    it on every turn would defeat its purpose: the point is what the person
    was when the conversation started, not what they are now.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "resolve_active_staff_thread requires a tenant in scope. "
            "Wrap the call in `tenant_scope(t)` before invocation."
        )

    bot_user_tenant_id = getattr(bot_user, "tenant_id", None)
    if bot_user_tenant_id is not None and bot_user_tenant_id != tenant.id:
        raise CrossTenantError(
            f"resolve_active_staff_thread: bot_user belongs to tenant "
            f"{bot_user_tenant_id!r} but current_tenant is {tenant.id!r}"
        )

    existing = _active_thread(tenant, bot_user)
    if existing is not None:
        return existing

    if not create_if_missing:
        return None

    # Two turns from the same person can arrive together (a tap plus a
    # typed line, a retried webhook). The partial unique constraint rejects
    # the loser; re-fetch the winner's row instead of surfacing a 500 that
    # would poison the consumer's PEL.
    try:
        with transaction.atomic():
            return StaffAssistantThread.objects.create(
                tenant=tenant,
                bot_user=bot_user,
                role_at_open=role_at_open or "",
            )
    except IntegrityError:
        thread = _active_thread(tenant, bot_user)
        if thread is None:
            # The constraint fired but no active row exists — that is not a
            # race, it is a bug worth seeing rather than swallowing.
            raise
        logger.info(
            "conversations.staff_thread.create_race tenant=%s bot_user=%s",
            tenant.slug,
            bot_user.id,
        )
        return thread


def _active_thread(tenant, bot_user) -> StaffAssistantThread | None:
    # Explicit ``tenant=tenant`` even though the manager is scoped: a future
    # swap to ``all_tenants`` for some admin pass must not silently widen
    # this read.
    return (
        StaffAssistantThread.objects.filter(
            tenant=tenant,
            bot_user=bot_user,
            is_active=True,
            deleted_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )


def record_staff_message(
    thread: StaffAssistantThread,
    *,
    role: str,
    content: str,
    tool_name: str = "",
    trace_id: uuid.UUID | str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int | None = None,
    llm_provider: str = "",
    llm_model: str = "",
    llm_cost_usd: Decimal | float | int = 0,
) -> StaffAssistantMessage:
    """Append one turn and bump ``last_message_at``.

    Enforces ``message.tenant == thread.tenant == current_tenant`` — the
    same invariant ``record_message`` holds, and for the same reason: a
    handler that resolves a thread in one scope and writes to it in another
    is a bug that must be loud.

    The insert and the timestamp update share one transaction, and the
    update is an atomic UPDATE rather than ``instance.save()``, so two
    concurrent turns cannot trample each other's clock.
    """

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "record_staff_message requires a tenant in scope. Wrap the "
            "call in `tenant_scope(t)` before invocation."
        )

    if thread.tenant_id != tenant.id:
        raise CrossTenantError(
            f"record_staff_message attempted on thread tenant="
            f"{thread.tenant_id} while current_tenant()={tenant.id}. "
            "Handler bug — verify tenant_scope before resolving the thread."
        )

    if trace_id is None:
        ctx_trace = current_trace_id()
        trace_id = uuid.UUID(ctx_trace) if ctx_trace else None
    elif isinstance(trace_id, str):
        trace_id = uuid.UUID(trace_id)

    now = timezone.now()
    with transaction.atomic():
        # Lock the thread before taking a position number. Two turns
        # written back to back — user, then the tool result, then the
        # answer — otherwise race for the same `seq`, and the unique
        # constraint would surface that as a 500 in the middle of a reply.
        #
        # No `select_related` under `select_for_update`: both FKs here are
        # NOT NULL, but the habit is what keeps DRF-1160 from recurring.
        StaffAssistantThread.all_tenants.select_for_update().filter(pk=thread.pk).first()
        seq = StaffAssistantMessage.all_tenants.filter(thread_id=thread.pk).count()

        message = StaffAssistantMessage.objects.create(
            thread=thread,
            tenant=tenant,
            seq=seq,
            role=role,
            content=content,
            tool_name=tool_name,
            trace_id=trace_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_cost_usd=llm_cost_usd,
        )
        StaffAssistantThread.all_tenants.filter(pk=thread.pk).update(last_message_at=now)

    return message


def recent_staff_history(
    thread: StaffAssistantThread,
    *,
    limit: int = DEFAULT_HISTORY_LIMIT,
    exclude_id: uuid.UUID | None = None,
) -> list[StaffAssistantMessage]:
    """The last ``limit`` turns, oldest first.

    ``exclude_id`` drops the turn just recorded — the caller persists the
    inbound message before composing the prompt (so every reply branch
    shares one record), and the model must not be handed the same line
    twice.
    """

    qs = StaffAssistantMessage.objects.filter(thread=thread)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    # Ordered by `seq`, not `created_at`: three turns of one tool round trip
    # can share a timestamp, and a shuffled history hands the model an
    # answer before its question.
    return list(reversed(list(qs.order_by("-seq")[:limit])))


def close_staff_thread(thread: StaffAssistantThread) -> None:
    """End the thread, keeping it as history.

    Nothing calls this yet; it exists so «start over» has an implementation
    to reach for rather than an invented one, and so the uniqueness
    constraint's escape hatch is exercised by tests from the start.
    """

    StaffAssistantThread.all_tenants.filter(pk=thread.pk, is_active=True).update(is_active=False)


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "close_staff_thread",
    "recent_staff_history",
    "record_staff_message",
    "resolve_active_staff_thread",
]
