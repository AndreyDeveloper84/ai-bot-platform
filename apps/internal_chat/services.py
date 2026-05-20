"""Service layer for the master↔admin internal chat (PR 6).

Atomic, audited helpers that view code calls into. Following the
pattern in :mod:`apps.booking.services.transitions`:

* every public function runs inside ``transaction.atomic``;
* every mutation writes an :func:`apps.audit.services.write_audit` row
  AND emits an :func:`apps.events.services.emit` analytics event;
* the function returns the persisted row so the caller can serialize
  it without a re-query.

Each function is a single-purpose, side-effect helper — there's no
state machine wrapper here. Cross-cutting invariants (auto-True for
``is_sensitive`` on sensitive topics, ``sla_due_at`` default per
topic) live on the model itself so direct ORM writes (tests, future
batch jobs) can't bypass them.

Out of scope for PR 6 (referenced in handoff §16):

* SLA-breach scanner (Celery beat) — separate PR.
* Auto-close after 14d inactivity — separate PR.
* PII scan on attachments — pipeline stays unwired.
* Founder reply path (the master can ESCALATE; the founder responding
  is a future PR once the founder identity model exists).
* Notification dispatch (push / MAX DM / email) on new message —
  separate PR.
* AI topic-classification on the first message — separate PR.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Literal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster
from apps.events.services import emit
from apps.events.vocabulary import (
    INTERNAL_CHAT_ESCALATED_TO_FOUNDER,
    INTERNAL_CHAT_MARKED_READ,
    INTERNAL_CHAT_MESSAGE_SENT,
    INTERNAL_CHAT_THREAD_ASSIGNED,
    INTERNAL_CHAT_THREAD_CREATED,
    INTERNAL_CHAT_THREAD_STATUS_CHANGED,
)
from apps.identity.models import BotUser
from apps.internal_chat.models import (
    MasterAdminMessage,
    MasterAdminThread,
    SENSITIVE_TOPICS,
    SenderRoleChoices,
    StatusChoices,
    TopicChoices,
    sla_hours_for_topic,
)
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


ReaderRole = Literal["master", "admin", "founder"]
CloseStatus = Literal["resolved", "auto_closed_inactive"]


class InternalChatServiceError(Exception):
    """Service-layer error. Carries a stable ``slug`` for HTTP envelopes."""

    slug = "internal_chat_error"


class InvalidTopic(InternalChatServiceError):
    slug = "invalid_topic"


class InvalidStatus(InternalChatServiceError):
    slug = "invalid_status"


# --- thread creation ------------------------------------------------------


def create_thread(
    *,
    tenant: Tenant,
    master: CatalogMaster,
    topic: str,
    subject: str = "",
    actor: BotUser,
    first_message_body: str | None = None,
    sender_role: str = SenderRoleChoices.MASTER,
    sender_admin_signed_name: str = "",
    linked_artifact_type: str = "",
    linked_artifact_id: UUID | None = None,
) -> MasterAdminThread:
    """Create a thread and optionally append the first message atomically.

    The handoff §9.5 validation contract — «master must own the linked
    artifact» — is enforced at the view layer (the service receives
    pre-validated inputs) because the validation depends on which
    artifact type is linked and which apps exist. The service only
    persists the row.

    ``is_sensitive`` + ``sla_due_at`` are derived from ``topic`` at
    the model's ``save()`` (defence in depth) AND here so the audit
    payload carries the resolved values.

    Returns the persisted thread. Caller is responsible for serializing.
    """

    if topic not in TopicChoices.values:
        raise InvalidTopic(f"unknown topic {topic!r}")

    now = timezone.now()
    is_sensitive = topic in SENSITIVE_TOPICS
    sla_due_at = now + timedelta(hours=sla_hours_for_topic(topic))

    with transaction.atomic():
        thread = MasterAdminThread.all_tenants.create(
            tenant=tenant,
            master=master,
            topic=topic,
            subject=subject,
            status=StatusChoices.OPEN,
            linked_artifact_type=linked_artifact_type,
            linked_artifact_id=linked_artifact_id,
            is_sensitive=is_sensitive,
            sla_due_at=sla_due_at,
        )

        # Optional first message — the master-side create endpoint
        # always supplies one (handoff §3.3); the admin-initiated
        # «summon master» path (Q-IAC3) creates a thread without one.
        first_message: MasterAdminMessage | None = None
        if first_message_body:
            first_message = _persist_message(
                thread=thread,
                sender_role=sender_role,
                sender_user=actor,
                body=first_message_body,
                sender_admin_signed_name=sender_admin_signed_name,
                update_thread_status=True,
            )

        # Audit row + analytics event (after the message persistence so
        # the audit row reflects the final state).
        audit_payload = {
            "tenant_id": str(tenant.id),
            "thread_id": str(thread.id),
            "master_id": str(master.id),
            "topic": topic,
            "linked_artifact_type": linked_artifact_type,
            "linked_artifact_id": (
                str(linked_artifact_id) if linked_artifact_id is not None else None
            ),
            "actor_id": str(actor.id),
            "is_sensitive": is_sensitive,
            "first_message_id": (str(first_message.id) if first_message is not None else None),
        }
        write_audit(
            INTERNAL_CHAT_THREAD_CREATED,
            target="internal_chat.MasterAdminThread",
            target_id=thread.id,
            payload=audit_payload,
            actor_id=actor.id,
        )
        emit(
            INTERNAL_CHAT_THREAD_CREATED,
            properties=audit_payload,
            distinct_id=str(actor.id),
        )

    return thread


# --- message append -------------------------------------------------------


def send_message(
    *,
    thread: MasterAdminThread,
    sender_role: str,
    sender_user: BotUser | None,
    body: str,
    sender_admin_signed_name: str = "",
) -> MasterAdminMessage:
    """Append a message to ``thread`` and update its status atomically.

    Status transitions (handoff §6.1):

    * master message + status in {OPEN, ADMIN_RESPONDED} → MASTER_RESPONDED.
    * admin/founder message + status in {OPEN, MASTER_RESPONDED}
      → ADMIN_RESPONDED.
    * any other state → ACTIVE_DISCUSSION (the thread already has
      back-and-forth happening).
    * Terminal states (RESOLVED, AUTO_CLOSED_INACTIVE) are NOT
      transitioned by send_message — the view layer rejects sending
      to a closed thread before reaching this helper.
    """

    if sender_role not in SenderRoleChoices.values:
        raise InvalidStatus(f"unknown sender_role {sender_role!r}")

    with transaction.atomic():
        message = _persist_message(
            thread=thread,
            sender_role=sender_role,
            sender_user=sender_user,
            body=body,
            sender_admin_signed_name=sender_admin_signed_name,
            update_thread_status=True,
        )
        # `_persist_message` updated the thread row in-place; emit AFTER
        # the transaction's writes are reflected on `thread`.
        audit_payload = {
            "tenant_id": str(thread.tenant_id),
            "thread_id": str(thread.id),
            "message_id": str(message.id),
            "sender_role": sender_role,
            "sender_user_id": str(sender_user.id) if sender_user is not None else None,
            "has_attachments": False,
        }
        write_audit(
            INTERNAL_CHAT_MESSAGE_SENT,
            target="internal_chat.MasterAdminMessage",
            target_id=message.id,
            payload=audit_payload,
            actor_id=sender_user.id if sender_user is not None else None,
        )
        emit(
            INTERNAL_CHAT_MESSAGE_SENT,
            properties=audit_payload,
            distinct_id=str(sender_user.id) if sender_user is not None else "",
        )

    return message


def _persist_message(
    *,
    thread: MasterAdminThread,
    sender_role: str,
    sender_user: BotUser | None,
    body: str,
    sender_admin_signed_name: str = "",
    update_thread_status: bool,
) -> MasterAdminMessage:
    """Inner helper — persist a row + bump thread state, no audit/event.

    Used by both :func:`create_thread` (first-message embed) and
    :func:`send_message`. Keeps the audit emission in the public
    helpers, not duplicated in every code path.
    """

    is_system = sender_role == SenderRoleChoices.SYSTEM
    message = MasterAdminMessage.objects.create(
        thread=thread,
        sender_role=sender_role,
        sender_user=sender_user,
        body=body,
        sender_admin_signed_name=sender_admin_signed_name,
        is_system_message=is_system,
    )

    if update_thread_status:
        # Decide the new status based on who sent the message + the
        # thread's prior state. The transitions are advisory — the
        # admin-side PATCH endpoint can still flip status to anything
        # in the choices set (handoff §4.6 manual close).
        new_status = thread.status
        if sender_role == SenderRoleChoices.MASTER:
            if thread.status in (
                StatusChoices.OPEN,
                StatusChoices.ADMIN_RESPONDED,
            ):
                new_status = StatusChoices.MASTER_RESPONDED
            elif thread.status == StatusChoices.MASTER_RESPONDED:
                new_status = StatusChoices.ACTIVE_DISCUSSION
        elif sender_role in (SenderRoleChoices.ADMIN, SenderRoleChoices.FOUNDER):
            if thread.status in (
                StatusChoices.OPEN,
                StatusChoices.MASTER_RESPONDED,
            ):
                new_status = StatusChoices.ADMIN_RESPONDED
            elif thread.status == StatusChoices.ADMIN_RESPONDED:
                new_status = StatusChoices.ACTIVE_DISCUSSION

        # Always bump last_activity_at — even when status didn't move,
        # the row should be visible at the top of the list.
        thread.last_activity_at = timezone.now()
        if new_status != thread.status:
            thread.status = new_status
        thread.save(update_fields=["status", "last_activity_at"])

    return message


# --- mark-read ------------------------------------------------------------


_READ_FIELD_FOR_ROLE: dict[str, str] = {
    "master": "read_by_master_at",
    "admin": "read_by_admin_at",
    "founder": "read_by_founder_at",
}


def mark_read(
    *,
    thread: MasterAdminThread,
    reader_role: ReaderRole,
    reader_user: BotUser,
) -> int:
    """Mark every unread message in ``thread`` as read for ``reader_role``.

    Returns the number of rows updated (zero when the reader has
    already seen every message). Always emits exactly one audit row +
    one event per call — the handoff §10 explicitly debounces the
    «marked_read» analytics event to per-thread-per-reader-role rather
    than per-message (otherwise a reader catching up on 20 messages
    would spam 20 events).
    """

    if reader_role not in _READ_FIELD_FOR_ROLE:
        raise InvalidStatus(f"unknown reader_role {reader_role!r}")

    field_name = _READ_FIELD_FOR_ROLE[reader_role]
    now = timezone.now()

    with transaction.atomic():
        # Only mark rows that are currently unread for this audience.
        # Skipping already-read rows keeps idempotent re-calls cheap and
        # prevents an audit storm when the UI re-marks on every render.
        count = MasterAdminMessage.objects.filter(
            thread=thread,
            **{f"{field_name}__isnull": True},
        ).update(**{field_name: now})

        if count > 0:
            audit_payload = {
                "tenant_id": str(thread.tenant_id),
                "thread_id": str(thread.id),
                "reader_role": reader_role,
                "reader_user_id": str(reader_user.id),
                "count": count,
            }
            write_audit(
                INTERNAL_CHAT_MARKED_READ,
                target="internal_chat.MasterAdminThread",
                target_id=thread.id,
                payload=audit_payload,
                actor_id=reader_user.id,
            )
            emit(
                INTERNAL_CHAT_MARKED_READ,
                properties=audit_payload,
                distinct_id=str(reader_user.id),
            )

    return count


# --- close ---------------------------------------------------------------


def close_thread(
    *,
    thread: MasterAdminThread,
    actor: BotUser,
    status: CloseStatus = "resolved",
) -> MasterAdminThread:
    """Close the thread — `status` either RESOLVED (admin action) or
    AUTO_CLOSED_INACTIVE (future Celery beat).

    Stamps ``resolved_at`` for both close types so the «recent activity»
    UI section can sort closed-recently threads alongside open ones.
    """

    if status not in (StatusChoices.RESOLVED, StatusChoices.AUTO_CLOSED_INACTIVE):
        raise InvalidStatus(f"close status must be resolved/auto_closed_inactive, got {status!r}")

    with transaction.atomic():
        prior_status = thread.status
        thread.status = status
        thread.resolved_at = timezone.now()
        thread.save(update_fields=["status", "resolved_at", "last_activity_at"])

        audit_payload = {
            "tenant_id": str(thread.tenant_id),
            "thread_id": str(thread.id),
            "from_status": prior_status,
            "to_status": status,
            "actor_id": str(actor.id),
        }
        write_audit(
            INTERNAL_CHAT_THREAD_STATUS_CHANGED,
            target="internal_chat.MasterAdminThread",
            target_id=thread.id,
            payload=audit_payload,
            actor_id=actor.id,
        )
        emit(
            INTERNAL_CHAT_THREAD_STATUS_CHANGED,
            properties=audit_payload,
            distinct_id=str(actor.id),
        )

    return thread


# --- escalate to founder -------------------------------------------------


def escalate_to_founder(
    *,
    thread: MasterAdminThread,
    actor: BotUser,
    reason: str = "",
) -> MasterAdminThread:
    """Master-initiated escalation per handoff §3.6.

    Flips ``status=ESCALATED_TO_FOUNDER`` + stamps
    ``founder_added_at``. The founder is NOT actually added as a
    participant here — that needs a founder identity model which
    doesn't exist yet. This service captures the master's intent + the
    audit trail; the responder side lands in a follow-up PR.
    """

    with transaction.atomic():
        prior_status = thread.status
        thread.status = StatusChoices.ESCALATED_TO_FOUNDER
        thread.founder_added_at = timezone.now()
        thread.save(update_fields=["status", "founder_added_at", "last_activity_at"])

        audit_payload = {
            "tenant_id": str(thread.tenant_id),
            "thread_id": str(thread.id),
            "master_id": str(thread.master_id),
            "from_status": prior_status,
            "reason_class": _classify_reason(reason),
            "actor_id": str(actor.id),
        }
        write_audit(
            INTERNAL_CHAT_ESCALATED_TO_FOUNDER,
            target="internal_chat.MasterAdminThread",
            target_id=thread.id,
            payload=audit_payload,
            actor_id=actor.id,
        )
        emit(
            INTERNAL_CHAT_ESCALATED_TO_FOUNDER,
            properties=audit_payload,
            distinct_id=str(actor.id),
        )

    return thread


def _classify_reason(reason: str) -> str:
    """Reduce the master's free-form reason into a coarse class.

    Handoff §10 prescribes a ``reason_class`` field in the payload —
    not the raw reason — so the analytics bus doesn't carry potentially
    sensitive text. PR 6 emits a single «provided» / «empty» bucket;
    the AI-assisted classification lands in a future PR.
    """

    return "provided" if reason.strip() else "empty"


# --- assign --------------------------------------------------------------


def assign_admin(
    *,
    thread: MasterAdminThread,
    assigned_admin: BotUser | None,
    actor: BotUser,
) -> MasterAdminThread:
    """Update ``assigned_admin`` per handoff §4.3.

    Passing ``None`` unassigns. Always audits — the «pinned to me»
    contract benefits from a clear forensic trail.
    """

    with transaction.atomic():
        prior_admin_id = thread.assigned_admin_id
        thread.assigned_admin = assigned_admin
        thread.save(update_fields=["assigned_admin", "last_activity_at"])

        audit_payload = {
            "tenant_id": str(thread.tenant_id),
            "thread_id": str(thread.id),
            "assigned_admin_id": (str(assigned_admin.id) if assigned_admin is not None else None),
            "previous_admin_id": str(prior_admin_id) if prior_admin_id else None,
            "actor_id": str(actor.id),
        }
        write_audit(
            INTERNAL_CHAT_THREAD_ASSIGNED,
            target="internal_chat.MasterAdminThread",
            target_id=thread.id,
            payload=audit_payload,
            actor_id=actor.id,
        )
        emit(
            INTERNAL_CHAT_THREAD_ASSIGNED,
            properties=audit_payload,
            distinct_id=str(actor.id),
        )

    return thread


# --- status / topic patch ------------------------------------------------


def patch_thread_fields(
    *,
    thread: MasterAdminThread,
    actor: BotUser,
    topic: str | None = None,
    subject: str | None = None,
    status: str | None = None,
    is_sensitive: bool | None = None,
) -> MasterAdminThread:
    """Admin-side field patcher per handoff §4.4 + §4.6.

    Topic re-tagging audits a row of its own (handoff §4.4); status
    flips audit through the THREAD_STATUS_CHANGED event. ``subject``
    + ``is_sensitive`` are quiet edits (no event), because the spec
    doesn't enumerate dedicated event slugs for them and they don't
    affect downstream behaviour beyond rendering.
    """

    update_fields: list[str] = []
    audit_payload: dict[str, object] = {
        "tenant_id": str(thread.tenant_id),
        "thread_id": str(thread.id),
        "actor_id": str(actor.id),
    }

    with transaction.atomic():
        if topic is not None and topic != thread.topic:
            if topic not in TopicChoices.values:
                raise InvalidTopic(f"unknown topic {topic!r}")
            audit_payload["from_topic"] = thread.topic
            audit_payload["to_topic"] = topic
            thread.topic = topic
            # Sensitive flag follows topic if the new topic is auto-
            # sensitive; the model's save() also enforces this, but we
            # set it explicitly here so the resulting row matches the
            # audit payload caller saw.
            if topic in SENSITIVE_TOPICS:
                thread.is_sensitive = True
                update_fields.append("is_sensitive")
            update_fields.append("topic")

        if subject is not None and subject != thread.subject:
            thread.subject = subject
            update_fields.append("subject")

        if is_sensitive is not None and is_sensitive != thread.is_sensitive:
            # Don't allow turning OFF is_sensitive when the topic is
            # auto-sensitive — the CHECK constraint would reject and
            # we'd surface a confusing IntegrityError. The view layer
            # also blocks this for a stable error envelope.
            if not is_sensitive and thread.topic in SENSITIVE_TOPICS:
                raise InvalidStatus(
                    "cannot unset is_sensitive for an auto-sensitive topic",
                )
            thread.is_sensitive = is_sensitive
            if "is_sensitive" not in update_fields:
                update_fields.append("is_sensitive")

        status_changed = False
        prior_status: str = thread.status
        if status is not None and status != thread.status:
            if status not in StatusChoices.values:
                raise InvalidStatus(f"unknown status {status!r}")
            prior_status = thread.status
            thread.status = status
            update_fields.append("status")
            if status == StatusChoices.RESOLVED:
                thread.resolved_at = timezone.now()
                update_fields.append("resolved_at")
            status_changed = True

        if update_fields:
            thread.save(update_fields=list(set(update_fields + ["last_activity_at"])))
            write_audit(
                "internal_chat.thread_fields_patched",
                target="internal_chat.MasterAdminThread",
                target_id=thread.id,
                payload={**audit_payload, "fields_changed": update_fields},
                actor_id=actor.id,
            )

        if status_changed:
            payload = {
                **audit_payload,
                "from_status": prior_status,
                "to_status": thread.status,
            }
            write_audit(
                INTERNAL_CHAT_THREAD_STATUS_CHANGED,
                target="internal_chat.MasterAdminThread",
                target_id=thread.id,
                payload=payload,
                actor_id=actor.id,
            )
            emit(
                INTERNAL_CHAT_THREAD_STATUS_CHANGED,
                properties=payload,
                distinct_id=str(actor.id),
            )

    return thread


__all__ = [
    "CloseStatus",
    "InternalChatServiceError",
    "InvalidStatus",
    "InvalidTopic",
    "ReaderRole",
    "assign_admin",
    "close_thread",
    "create_thread",
    "escalate_to_founder",
    "mark_read",
    "patch_thread_fields",
    "send_message",
]
