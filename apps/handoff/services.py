"""Handoff services — package_transcript / create / resolve (DRF-465 / Sprint 3 / C2).

Three entry points the rest of the platform uses to manage handoffs:

  * :func:`package_transcript` — pure function. Builds the JSON-serialisable
    snapshot dict that lands in :attr:`AdminTask.transcript_snapshot`.
    PII-aware: ``bot_user.phone`` is sha256-hashed, NEVER raw.
  * :func:`create_admin_task` — atomic write. Creates AdminTask + flips
    Conversation.state → HUMAN_HANDOFF + emits ``handoff_initiated``
    canonical event + writes audit row.
  * :func:`resolve_admin_task` — admin-side close. Stamps resolved_at +
    resolution_note + flips Conversation.state back to IDLE so the bot
    resumes (D4 dispatcher checks state != HUMAN_HANDOFF before skill
    execution).

### Why a service layer, not model methods

create_admin_task touches THREE tables in one logical action:
AdminTask, Conversation.state, Event + AuditLog. That's Unit-of-Work
territory — belongs in a service. Same for resolve, which inverts the
state change plus closes the task.

### PII rule (CRITICAL)

The transcript_snapshot is forensic data that **operators** read in
the admin. Operators are tenant users — they have a need-to-know
basis for the conversation transcript but should not casually browse
raw phone numbers. The snapshot therefore hashes phone via sha256;
when the operator legitimately needs the raw phone, they go through
the BotUser admin record. The snapshot itself is read-only and
PII-minimal.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.events.services import emit
from apps.events.vocabulary import HANDOFF_INITIATED
from apps.handoff.models import AdminTask
from apps.tenancy.context import current_tenant

if TYPE_CHECKING:
    from apps.conversations.models import Conversation

logger = logging.getLogger(__name__)

_MAX_MESSAGES_DEFAULT = 20


def _hash_phone(phone: str) -> str:
    """sha256 the raw phone for snapshot storage.

    Empty input returns empty string so the snapshot's
    `bot_user.phone_hash` is honestly "" when the user has no phone
    on file (versus a hash of the literal empty string, which would
    look like real data).
    """

    if not phone:
        return ""
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def package_transcript(
    conversation: "Conversation", *, max_messages: int = _MAX_MESSAGES_DEFAULT
) -> dict[str, Any]:
    """Build a forensic snapshot dict for a Conversation.

    The dict is JSON-serialisable end-to-end; callers store it in
    ``AdminTask.transcript_snapshot``.

    Shape::

      {
        "captured_at": "2026-05-11T12:30:00Z",
        "max_messages": 20,
        "conversation": {
          "id": "...uuid...",
          "state": "human_handoff",
          "outcome": "",                    # empty while open
          "is_active": true,
          "created_at": "...",
          "last_message_at": "...",
        },
        "bot_user": {
          "id": "...uuid...",
          "channel": "max",
          "channel_user_id": "12345",
          "display_name": "Ivan",
          "phone_hash": "<sha256 or empty>", # NEVER raw phone
        },
        "messages": [
          {"role": "user", "content": "...", "action_type": "",
           "created_at": "..."},
          ...
        ],
      }
    """

    from apps.conversations.models import Message

    bot_user = conversation.bot_user

    # Use `all_tenants` so packaging works inside admin actions / cleanup
    # tasks that legitimately span scopes. Tenant isolation is already
    # established by filtering on conversation=conversation.
    last_n_iterable = list(
        Message.all_tenants.filter(conversation=conversation).order_by("-created_at")[:max_messages]
    )
    # Reverse so the snapshot reads oldest → newest.
    messages_payload = [
        {
            "role": msg.role,
            "content": msg.content,
            "action_type": msg.action_type or "",
            "created_at": msg.created_at.isoformat(),
        }
        for msg in reversed(last_n_iterable)
    ]

    return {
        "captured_at": timezone.now().isoformat(),
        "max_messages": max_messages,
        "conversation": {
            "id": str(conversation.id),
            "state": conversation.state,
            "outcome": conversation.outcome or "",
            "is_active": conversation.is_active,
            "created_at": conversation.created_at.isoformat(),
            "last_message_at": (
                conversation.last_message_at.isoformat() if conversation.last_message_at else None
            ),
        },
        "bot_user": {
            "id": str(bot_user.id),
            "channel": bot_user.channel,
            "channel_user_id": bot_user.channel_user_id,
            "display_name": bot_user.display_name,
            "phone_hash": _hash_phone(bot_user.phone or ""),
        },
        "messages": messages_payload,
    }


def create_admin_task(
    conversation: "Conversation",
    *,
    task_type: str,
    priority: str = AdminTask.Priority.NORMAL.value,
    reason: str = "",
) -> AdminTask:
    """Atomic handoff: create task + flip Conversation state + emit + audit.

    Args:
      conversation: the conversation being handed off.
      task_type: one of :class:`AdminTask.TaskType` values.
      priority: operator-queue priority. Default NORMAL.
      reason: free-form operator-facing rationale.

    Returns:
      The created :class:`AdminTask`.

    Raises:
      ValueError: ``current_tenant()`` is None.
    """

    # Late import to dodge any chance of cycles at module load.
    from apps.conversations.models import Conversation as ConversationModel

    tenant = current_tenant()
    if tenant is None:
        raise ValueError(
            "handoff.create_admin_task requires a tenant in scope. Wrap the "
            "call in `tenant_scope(t)` before invocation."
        )

    snapshot = package_transcript(conversation)

    with transaction.atomic():
        task = AdminTask.objects.create(
            tenant=tenant,
            bot_user=conversation.bot_user,
            conversation=conversation,
            task_type=task_type,
            priority=priority,
            reason=reason,
            transcript_snapshot=snapshot,
        )
        # Flip conversation state — single UPDATE, in the same transaction
        # so an event subscriber can never see one without the other.
        ConversationModel.all_tenants.filter(pk=conversation.pk).update(
            state=ConversationModel.State.HUMAN_HANDOFF
        )
        # Refresh the in-memory instance so callers see the new state.
        conversation.state = ConversationModel.State.HUMAN_HANDOFF

    def _emit_handoff() -> None:
        emit(
            HANDOFF_INITIATED,
            distinct_id=str(conversation.bot_user_id),
            dialog_id=conversation.id,
            properties={
                "task_id": str(task.id),
                "task_type": task_type,
                "priority": priority,
            },
        )
        write_audit(
            "handoff.created",
            target="AdminTask",
            target_id=task.id,
            payload={
                "conversation_id": str(conversation.id),
                "bot_user_id": str(conversation.bot_user_id),
                "task_type": task_type,
                "priority": priority,
                "reason": reason[:200],
            },
        )

    transaction.on_commit(_emit_handoff)
    logger.info(
        "handoff.created task=%s type=%s conversation=%s tenant=%s",
        task.id,
        task_type,
        conversation.id,
        tenant.id,
    )
    return task


def resolve_admin_task(task: AdminTask, *, resolution_note: str = "") -> None:
    """Mark `task` RESOLVED, stamp metadata, return conversation to IDLE.

    Idempotent on already-RESOLVED tasks: returns silently (the
    transition already happened; second call would zero out the
    resolution_note and reset resolved_at, which is wrong).

    The bot resumes autonomy once Conversation.state flips back to
    IDLE — D4 dispatcher (Sprint 3) short-circuits skill execution
    while state == HUMAN_HANDOFF.
    """

    from apps.conversations.models import Conversation as ConversationModel

    if task.status == AdminTask.Status.RESOLVED:
        return

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("handoff.resolve_admin_task requires a tenant in scope.")

    now = timezone.now()
    with transaction.atomic():
        AdminTask.all_tenants.filter(pk=task.pk).update(
            status=AdminTask.Status.RESOLVED,
            resolved_at=now,
            resolution_note=resolution_note,
        )
        ConversationModel.all_tenants.filter(pk=task.conversation_id).update(
            state=ConversationModel.State.IDLE
        )

    # Refresh in-memory instance so callers reflect the new state.
    task.status = AdminTask.Status.RESOLVED
    task.resolved_at = now
    task.resolution_note = resolution_note

    def _emit_resolved() -> None:
        write_audit(
            "handoff.resolved",
            target="AdminTask",
            target_id=task.id,
            payload={
                "conversation_id": str(task.conversation_id),
                "resolution_note": (resolution_note or "")[:200],
            },
        )

    transaction.on_commit(_emit_resolved)
    logger.info(
        "handoff.resolved task=%s conversation=%s tenant=%s",
        task.id,
        task.conversation_id,
        tenant.id,
    )
