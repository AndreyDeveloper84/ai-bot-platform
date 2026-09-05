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
  * :func:`cancel_admin_task` — admin-side cancel (DRF-980). Same
    conversation release, but ``resolved_at`` stays NULL (no completed
    work) and the audit action is ``handoff.cancelled``.
  * :func:`release_conversation_to_bot` — idempotent conversation
    release shared by both close paths; also heals a conversation stuck
    in HUMAN_HANDOFF after an out-of-band status flip.

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
from apps.handoff.assignment import resolve_addressee
from apps.handoff.models import AdminTask
from apps.handoff.notify import notify_admin_task_created
from apps.handoff.silence import release_notices_for
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
    # DRF-1488 — the task is addressed before it exists, not after somebody
    # notices it. Resolved OUTSIDE the atomic block: it is a read against the
    # user table and the settings, and it must not lengthen the transaction
    # that holds the conversation flip.
    operator, queue = resolve_addressee()

    with transaction.atomic():
        task = AdminTask.objects.create(
            tenant=tenant,
            bot_user=conversation.bot_user,
            conversation=conversation,
            task_type=task_type,
            priority=priority,
            reason=reason,
            transcript_snapshot=snapshot,
            assigned_to=operator,
            assigned_queue=queue,
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
    # DRF-1029 — operator notification fires only after the task row
    # actually commits; a rolled-back handoff must never notify.
    transaction.on_commit(lambda: notify_admin_task_created(task))
    # DRF-1488 — an un-addressed task is the defect this ticket exists for,
    # and `apps.handoff.checks` makes the configuration that produces one
    # refuse to boot. Reaching this line anyway means the boot check was
    # bypassed, so it is logged at ERROR and audited rather than passed over:
    # the row would otherwise look filed while reaching nobody.
    if not task.is_addressed:
        logger.error(
            "handoff.created_unaddressed task=%s conversation=%s tenant=%s",
            task.id,
            conversation.id,
            tenant.id,
        )
        transaction.on_commit(
            lambda: write_audit(
                "handoff.created_unaddressed",
                target="AdminTask",
                target_id=task.id,
                payload={"conversation_id": str(conversation.id)},
            )
        )
    logger.info(
        "handoff.created task=%s type=%s conversation=%s tenant=%s addressee=%s",
        task.id,
        task_type,
        conversation.id,
        tenant.id,
        task.addressee or "NOBODY",
    )
    return task


def release_conversation_to_bot(task: AdminTask) -> bool:
    """Flip the task's conversation HUMAN_HANDOFF → IDLE, idempotently.

    Returns True when the flip actually happened.

    Two guards make this safe to call from anywhere (DRF-980):

    * Another OPEN/IN_PROGRESS task on the same conversation still holds
      it — the bot must stay muted until the LAST open task closes.
    * The conditional ``state=HUMAN_HANDOFF`` update makes the call a
      no-op when the conversation is already back under bot control, so
      re-saving an already-closed task never clobbers a fresh state.

    Cross-tenant by design (``all_tenants``): the admin spans tenants,
    and the tenant identity is carried by the task itself.
    """

    from apps.conversations.models import Conversation as ConversationModel

    if task.conversation_id is None:
        return False
    another_open_task = (
        AdminTask.all_tenants.filter(
            conversation_id=task.conversation_id,
            status__in=(AdminTask.Status.OPEN, AdminTask.Status.IN_PROGRESS),
        )
        .exclude(pk=task.pk)
        .exists()
    )
    if another_open_task:
        logger.info(
            "handoff.conversation_release.deferred task=%s conversation=%s "
            "reason=another_open_task",
            task.id,
            task.conversation_id,
        )
        return False
    updated = ConversationModel.all_tenants.filter(
        pk=task.conversation_id,
        state=ConversationModel.State.HUMAN_HANDOFF,
    ).update(state=ConversationModel.State.IDLE)
    if updated:
        logger.info(
            "handoff.conversation_release.ok task=%s conversation=%s",
            task.id,
            task.conversation_id,
        )
    return bool(updated)


def _announce_bot_is_back(task: AdminTask) -> None:
    """Tell the muted dialogs the bot is back, after this close commits (DRF-1486).

    Registered on_commit for the same reason the creation notice is
    (DRF-1029 §3.2): a rolled-back close must not tell the client the bot
    has returned. Called from every exit of both close paths, including
    the idempotent ones — :func:`apps.handoff.silence.release_notices_for`
    re-asks the mute question itself, so a task closed while ANOTHER task
    still holds this person leaves the notice open and says nothing.
    """

    transaction.on_commit(lambda: release_notices_for(task))


def resolve_admin_task(task: AdminTask, *, resolution_note: str = "") -> None:
    """Mark `task` RESOLVED, stamp metadata, return conversation to IDLE.

    Idempotent on already-RESOLVED tasks: the note/timestamp from the
    first call survive (a second call must not clobber them) — but the
    conversation invariant is still healed (DRF-980): a RESOLVED task
    whose conversation is stuck in HUMAN_HANDOFF (e.g. the status was
    flipped directly via ORM/admin, bypassing this service) releases
    the dialog instead of returning silently.

    The bot resumes autonomy once Conversation.state flips back to
    IDLE — D4 dispatcher (Sprint 3) short-circuits skill execution
    while state == HUMAN_HANDOFF.
    """

    if task.status == AdminTask.Status.RESOLVED:
        release_conversation_to_bot(task)
        _announce_bot_is_back(task)
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
        release_conversation_to_bot(task)

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
    _announce_bot_is_back(task)
    logger.info(
        "handoff.resolved task=%s conversation=%s tenant=%s",
        task.id,
        task.conversation_id,
        tenant.id,
    )


def cancel_admin_task(task: AdminTask, *, resolution_note: str = "") -> None:
    """Mark `task` CANCELLED and return the conversation to the bot (DRF-980).

    Semantics differ from resolve on purpose: ``resolved_at`` stays NULL
    (no work was completed), but the conversation MUST still flip back to
    IDLE — the operator closed the question, so the bot resumes answering.

    Idempotent: re-cancelling an already-CANCELLED task keeps the first
    note and still heals a stuck conversation (same invariant as
    :func:`resolve_admin_task`). Cancelling an already-RESOLVED task is
    refused — completed work is not reclassified downwards; use the admin
    status field deliberately if a reclass is ever needed.
    """

    if task.status == AdminTask.Status.RESOLVED:
        logger.warning(
            "handoff.cancel.refused task=%s conversation=%s reason=already_resolved",
            task.id,
            task.conversation_id,
        )
        return
    if task.status == AdminTask.Status.CANCELLED:
        release_conversation_to_bot(task)
        _announce_bot_is_back(task)
        return

    tenant = current_tenant()
    if tenant is None:
        raise ValueError("handoff.cancel_admin_task requires a tenant in scope.")

    with transaction.atomic():
        AdminTask.all_tenants.filter(pk=task.pk).update(
            status=AdminTask.Status.CANCELLED,
            resolution_note=resolution_note,
        )
        release_conversation_to_bot(task)

    # Refresh in-memory instance so callers reflect the new state.
    task.status = AdminTask.Status.CANCELLED
    task.resolution_note = resolution_note

    def _emit_cancelled() -> None:
        write_audit(
            "handoff.cancelled",
            target="AdminTask",
            target_id=task.id,
            payload={
                "conversation_id": str(task.conversation_id),
                "resolution_note": (resolution_note or "")[:200],
            },
        )

    transaction.on_commit(_emit_cancelled)
    _announce_bot_is_back(task)
    logger.info(
        "handoff.cancelled task=%s conversation=%s tenant=%s",
        task.id,
        task.conversation_id,
        tenant.id,
    )
