"""Master M6 conversation detail service layer (PR M6.1).

Pure-function helpers powering four endpoints under
``/api/v1/master/conversations/<id>/``:

* ``GET ./``               — full detail + last 100 messages
* ``POST ./messages``      — master composes a reply
* ``POST ./mark-read``     — debounced unread-clear
* ``POST ./promote``       — escalate to HUMAN_LOCKED (admin takeover)

The master sees a strictly PII-gated view per master-mobile §M6 — see
:func:`_redact_for_master`. The four endpoints share an «involves
master» predicate matching :mod:`apps.master_api.services.conversations`
so cross-master visibility is impossible.

Spec quote (master-mobile §M6 lines 706-712):

    «When master taps «Отправить от себя» on a draft, the message
    renders to the customer as «Помощник: …». Same single assistant
    identity. Master's authorship is recorded in attribution metadata
    (``actor_type=master``, ``composed_by=master_id``) — visible only
    in audit log and to owner/admin in Conversations module»

Spec quote (master-mobile §M6 line 765):

    «Confirms → ``conversation.tier_changed`` to HUMAN_LOCKED,
    ``tier_override`` audit with ``actor_role=master``, push to admin
    via bot DM.»

### Scope cuts (PR M6.1 spec)

* Frontend (M6 layout, compose box, promote modal). Separate PR.
* Pre-send persona check engine — needs assistant-persona.md
  infrastructure not present yet.
* AI draft *generation* — wired in Bundle B / item 4 backend
  (apps.master_api.services.ai_drafts). This GET endpoint now
  populates ``ai_draft_*`` from the latest ACTIVE :class:`AiDraft`
  for the conversation when one exists; null otherwise.
* «Позвать Аню» mention / «Открыть чат с Аней» deeplink — separate.
* Editing or deleting previously-sent messages — never (forensic).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone as dj_timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import AiDraft, Conversation, Message
from apps.conversations.services import record_message
from apps.events.services import emit
from apps.events.vocabulary import (
    CONVERSATION_MARKED_READ_BY_MASTER,
    CONVERSATION_MASTER_REPLIED,
    CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED,
)

logger = logging.getLogger(__name__)


# --- constants ---------------------------------------------------------

MAX_MESSAGES_PER_DETAIL = 100
"""Per M6 spec — last 100 messages on first page. Older via cursor —
not implemented in this PR (frontend can request later via the
``before`` parameter once paginated chats land)."""

MAX_COMPOSE_LENGTH = 2000
"""Master compose box length cap. Mirrors admin compose to prevent a
4MB message from a fat-finger paste landing in the audit log."""

VALID_REASON_CLASSES: frozenset[str] = frozenset({"complaint", "financial", "medical", "other"})
"""Accepted ``reason_class`` values for the promote endpoint. Mirrors
the §M6 safety promote sheet's four buttons."""


# --- error class -------------------------------------------------------


class ConversationDetailError(Exception):
    """Validation / authz failure surfaced by the service layer.

    ``slug`` is the stable string the view layer maps to an HTTP status.
    """

    def __init__(self, slug: str, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.slug = slug
        self.detail = detail
        self.status = status


class ConversationNotInvolved(ConversationDetailError):
    """Raised when the master has no booking link to the conversation.

    Maps to 404 in the view layer — surfacing «exists but forbidden»
    would leak the existence of cross-master conversations.
    """

    def __init__(self, detail: str = "conversation not found") -> None:
        super().__init__("not_found", detail, status=404)


# --- response shapes ---------------------------------------------------


@dataclass(frozen=True)
class MessageItem:
    message_id: str
    role: str
    content: str
    sent_at: str
    composed_by_master: bool
    composed_by_master_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "sent_at": self.sent_at,
            "composed_by_master": self.composed_by_master,
            "composed_by_master_id": self.composed_by_master_id,
        }


@dataclass(frozen=True)
class ConversationDetailResponse:
    conversation_id: str
    tier: str
    tier_reason: str | None
    is_active: bool
    tier_locked_by_admin_name: str | None
    tier_locked_since: str | None
    client_first_name: str
    client_last_initial: str
    is_returning_customer: bool
    visit_count: int
    messages: list[MessageItem]
    ai_draft_id: str | None
    ai_draft_content: str | None
    ai_draft_created_at: str | None
    can_compose: bool
    can_promote: bool

    def to_dict(self) -> dict[str, Any]:
        # Explicit construction so the PII-gate is auditable — adding a
        # new master-facing field requires touching THIS line.
        return {
            "conversation_id": self.conversation_id,
            "tier": self.tier,
            "tier_reason": self.tier_reason,
            "is_active": self.is_active,
            "tier_locked_by_admin_name": self.tier_locked_by_admin_name,
            "tier_locked_since": self.tier_locked_since,
            "client_first_name": self.client_first_name,
            "client_last_initial": self.client_last_initial,
            "is_returning_customer": self.is_returning_customer,
            "visit_count": self.visit_count,
            "messages": [m.to_dict() for m in self.messages],
            "ai_draft": {
                "draft_id": self.ai_draft_id,
                "content": self.ai_draft_content,
                "created_at": self.ai_draft_created_at,
            },
            "permissions": {
                "can_compose": self.can_compose,
                "can_promote_to_human_locked": self.can_promote,
            },
        }


@dataclass(frozen=True)
class MessageResponse:
    message_id: str
    content: str
    sent_at: str
    composed_by_master: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "content": self.content,
            "sent_at": self.sent_at,
            "composed_by_master": self.composed_by_master,
        }


@dataclass(frozen=True)
class PromoteResponse:
    conversation_id: str
    tier: str
    tier_locked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "tier": self.tier,
            "tier_locked_at": self.tier_locked_at,
        }


# --- helpers -----------------------------------------------------------


def _verify_master_involved(
    master: CatalogMaster, conversation_id: uuid.UUID | str
) -> Conversation:
    """Resolve the Conversation and assert master is involved.

    «Involves master» predicate matches
    :mod:`apps.master_api.services.conversations`: at least one
    :class:`BookingRequest` row joins the conversation's ``bot_user``
    to the current master in the same tenant.

    Cross-master + cross-tenant misses BOTH surface as
    :class:`ConversationNotInvolved` (→ 404). Surfacing the
    distinction would leak existence of cross-master conversations.
    """

    try:
        conv_uuid = uuid.UUID(str(conversation_id))
    except (TypeError, ValueError) as exc:
        raise ConversationNotInvolved("conversation id is not a valid UUID") from exc

    involves = BookingRequest.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
        bot_user_id=OuterRef("bot_user_id"),
    )
    conv = (
        Conversation.all_tenants.filter(
            id=conv_uuid,
            tenant_id=master.tenant_id,
            deleted_at__isnull=True,
            is_shadow=False,
            bot_user__isnull=False,
        )
        .annotate(_involves=Exists(involves))
        .filter(_involves=True)
        .select_related("bot_user", "tier_locked_by_master")
        .first()
    )
    if conv is None:
        raise ConversationNotInvolved()
    return conv


def _split_name(client_name: str) -> tuple[str, str]:
    """«Ксения Леонова» → («Ксения», «Л.»). Mirrors M5 helper."""

    parts = (client_name or "").strip().split()
    if not parts:
        return "", ""
    first = parts[0]
    if len(parts) == 1:
        return first, ""
    return first, parts[1][:1].upper() + "."


def _client_name_for(conversation: Conversation) -> str:
    """Customer's preferred display name. Mirrors M5 helper."""

    bot_user = conversation.bot_user
    if bot_user is None:
        return ""
    return (
        (bot_user.client_name or "").strip()
        or (bot_user.display_name or "").strip()
        or (bot_user.channel_user_id or "").strip()
    )


def _visit_count_and_returning(master: CatalogMaster, bot_user_id: Any) -> tuple[int, bool]:
    """Count CONFIRMED + COMPLETED bookings for this (master, customer).

    Returning customer = >1 such bookings (same definition as M5
    list endpoint's :func:`_build_returning_set`).
    """

    if bot_user_id is None:
        return 0, False
    count = (
        BookingRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            bot_user_id=bot_user_id,
        )
        .filter(Q(status=BookingRequest.Status.CONFIRMED) | Q(completed_at__isnull=False))
        .count()
    )
    return count, count > 1


def _attribution_from_message(msg: Message) -> tuple[bool, str | None]:
    """Decode a Message's master-attribution metadata.

    M6 stores `{"actor_type": "master", "composed_by": "<master_id>"}`
    in :attr:`Message.action_data` alongside :attr:`Message.action_type`
    set to ``"master_compose"``. Other roles / action_types are
    bot-authored from the customer's point of view.
    """

    if msg.action_type != "master_compose":
        return False, None
    data = msg.action_data or {}
    if not isinstance(data, dict):
        return False, None
    if data.get("actor_type") != "master":
        return False, None
    composed_by = data.get("composed_by")
    if isinstance(composed_by, str):
        return True, composed_by
    return True, None


def _redact_for_master(
    *,
    master: CatalogMaster,
    conversation: Conversation,
    messages: list[Message],
    now: datetime,
) -> ConversationDetailResponse:
    """Assemble the master-facing detail response — PII-gated by construction.

    The returned :class:`ConversationDetailResponse` exposes ONLY:
      * First name + 1-letter initial
      * Returning-customer flag + visit count
      * Message bodies (rendered_text fallback to content)
      * Tier + tier_reason_class + tier_locked_at (no admin assignee
        name unless we have one)

    Explicitly NEVER serialised: customer phone, full last name, email,
    LTV, raw assignee row, channel ids of the customer.
    """

    client_name = _client_name_for(conversation)
    first, last_initial = _split_name(client_name)

    visit_count, is_returning = _visit_count_and_returning(master, conversation.bot_user_id)

    # Tier-locked admin name: nullable. The model stores a master who
    # LOCKED the conversation; the spec also mentions an admin assignee
    # name banner («Аня отвечает с 14:12»), but admin assignment is out
    # of scope for this PR. We surface the locking master's display
    # name when the conversation is locked, mirroring §M6 «Аня отвечает».
    locked_master = conversation.tier_locked_by_master
    locked_name = locked_master.name if locked_master is not None else None
    if conversation.tier != Conversation.Tier.HUMAN_LOCKED:
        # Never surface lock metadata for a non-locked tier — keeps
        # the response shape tight + avoids leaking «who tried to
        # lock» if we ever store proposed locks.
        locked_name = None

    msg_items = [
        MessageItem(
            message_id=str(m.id),
            role=m.role,
            content=(m.rendered_text or m.content or ""),
            sent_at=m.created_at.isoformat() if m.created_at else "",
            composed_by_master=_attribution_from_message(m)[0],
            composed_by_master_id=_attribution_from_message(m)[1],
        )
        for m in messages
    ]

    is_locked = conversation.tier == Conversation.Tier.HUMAN_LOCKED
    can_compose = conversation.is_active and not is_locked and conversation.deleted_at is None
    # Master can ALWAYS promote (until already locked) — even on a
    # closed conversation, the spec's safety button is the last
    # resort.
    can_promote = not is_locked

    # Bundle B / item 4 backend: surface the latest ACTIVE AI draft for
    # this master. Tenant-scoped manager + explicit master filter so a
    # cross-master draft (theoretically impossible — only one ACTIVE per
    # conversation, the partial unique constraint enforces it — but
    # defensive against a future drift) can never bleed through. Null
    # whenever no ACTIVE row exists OR the conversation is HUMAN_LOCKED
    # (draft is moot — master can't send anyway).
    active_draft: AiDraft | None = None
    if not is_locked:
        active_draft = (
            AiDraft.all_tenants.filter(
                tenant_id=master.tenant_id,
                conversation_id=conversation.id,
                master_id=master.id,
                status=AiDraft.Status.ACTIVE,
            )
            .order_by("-created_at")
            .first()
        )

    return ConversationDetailResponse(
        conversation_id=str(conversation.id),
        tier=conversation.tier,
        tier_reason=(conversation.tier_reason_class or None) if is_locked else None,
        is_active=conversation.is_active,
        tier_locked_by_admin_name=locked_name,
        tier_locked_since=(
            conversation.tier_locked_at.isoformat()
            if conversation.tier_locked_at and is_locked
            else None
        ),
        client_first_name=first,
        client_last_initial=last_initial,
        is_returning_customer=is_returning,
        visit_count=visit_count,
        messages=msg_items,
        ai_draft_id=str(active_draft.id) if active_draft is not None else None,
        ai_draft_content=active_draft.content if active_draft is not None else None,
        ai_draft_created_at=(
            active_draft.created_at.isoformat()
            if active_draft is not None and active_draft.created_at
            else None
        ),
        can_compose=can_compose,
        can_promote=can_promote,
    )


# --- main entrypoints --------------------------------------------------


def get_conversation_detail(
    master: CatalogMaster,
    conversation_id: uuid.UUID | str,
    *,
    now: datetime | None = None,
) -> ConversationDetailResponse:
    """GET handler — assemble the master detail response.

    Raises:
      ConversationNotInvolved — 404. Either no conversation, or master
        has no booking link to its bot_user.
    """

    if now is None:
        now = dj_timezone.now()

    conv = _verify_master_involved(master, conversation_id)

    messages = list(
        Message.all_tenants.filter(conversation_id=conv.id).order_by("-created_at")[
            :MAX_MESSAGES_PER_DETAIL
        ]
    )
    # Spec: chronological. Pull desc + reverse so the SQL uses the
    # ``(conversation, created_at)`` index AND the cap is honoured.
    messages.reverse()

    return _redact_for_master(
        master=master,
        conversation=conv,
        messages=messages,
        now=now,
    )


def send_master_message(
    master: CatalogMaster,
    conversation_id: uuid.UUID | str,
    *,
    content: str,
    actor_bot_user: Any,
) -> MessageResponse:
    """POST /messages — master composes a reply.

    Steps:
      1. Verify ownership (involves master) — else 404.
      2. Reject HUMAN_LOCKED — 403 ``tier_locked``.
      3. Validate content (non-empty, ≤ 2000 chars).
      4. ``record_message`` with ``role='assistant'`` + attribution
         metadata in ``action_data`` per §M6 line 706.
      5. Emit ``conversation.master_replied`` + audit row.

    Raises:
      ConversationDetailError with appropriate slug/status.
    """

    conv = _verify_master_involved(master, conversation_id)

    if conv.tier == Conversation.Tier.HUMAN_LOCKED:
        raise ConversationDetailError(
            "tier_locked",
            "conversation is human-locked; only admin/owner can reply",
            status=403,
        )

    body = (content or "").strip()
    if not body:
        raise ConversationDetailError("bad_request", "content must be non-empty", status=400)
    if len(body) > MAX_COMPOSE_LENGTH:
        raise ConversationDetailError(
            "bad_request",
            f"content exceeds {MAX_COMPOSE_LENGTH} characters",
            status=400,
        )

    payload = {
        "tenant_id": str(master.tenant_id),
        "conversation_id": str(conv.id),
        "master_id": str(master.id),
    }

    with transaction.atomic():
        # role=assistant per §M6 line 706 — the customer sees
        # «Помощник: …», single assistant identity.
        msg = record_message(
            conv,
            role=Message.Role.ASSISTANT,
            content=body,
            rendered_text=body,
            action_type="master_compose",
            action_data={
                "actor_type": "master",
                "composed_by": str(master.id),
            },
        )
        full_payload = {**payload, "message_id": str(msg.id)}
        write_audit(
            CONVERSATION_MASTER_REPLIED,
            target="Conversation",
            target_id=conv.id,
            payload=full_payload,
            actor_id=actor_bot_user.id if actor_bot_user is not None else None,
        )
        emit(CONVERSATION_MASTER_REPLIED, properties=full_payload)

    return MessageResponse(
        message_id=str(msg.id),
        content=body,
        sent_at=msg.created_at.isoformat() if msg.created_at else "",
        composed_by_master=True,
    )


def mark_conversation_read(
    master: CatalogMaster,
    conversation_id: uuid.UUID | str,
    *,
    actor_bot_user: Any,
    now: datetime | None = None,
) -> int:
    """POST /mark-read — stamp ``last_read_by_master_at`` + count cleared.

    «Cleared» = messages with role IN (user, system) created after the
    previous ``last_read_by_master_at`` and at or before ``now``.

    Idempotent — calling twice in a row returns 0 the second time.
    Always writes exactly one audit row per call (debounced — spec §M6
    «one per session»).
    """

    if now is None:
        now = dj_timezone.now()

    conv = _verify_master_involved(master, conversation_id)

    last_read = conv.last_read_by_master_at
    unread_qs = Message.all_tenants.filter(
        conversation_id=conv.id,
        role__in=[Message.Role.USER, Message.Role.SYSTEM],
        created_at__lte=now,
    )
    if last_read is not None:
        unread_qs = unread_qs.filter(created_at__gt=last_read)
    marked_count = unread_qs.count()

    with transaction.atomic():
        Conversation.all_tenants.filter(pk=conv.pk).update(last_read_by_master_at=now)
        payload = {
            "tenant_id": str(master.tenant_id),
            "conversation_id": str(conv.id),
            "master_id": str(master.id),
            "marked_count": marked_count,
        }
        write_audit(
            CONVERSATION_MARKED_READ_BY_MASTER,
            target="Conversation",
            target_id=conv.id,
            payload=payload,
            actor_id=actor_bot_user.id if actor_bot_user is not None else None,
        )
        emit(CONVERSATION_MARKED_READ_BY_MASTER, properties=payload)

    return marked_count


def promote_to_human_locked(
    master: CatalogMaster,
    conversation_id: uuid.UUID | str,
    *,
    reason_class: str,
    reason_text: str,
    actor_bot_user: Any,
    now: datetime | None = None,
) -> PromoteResponse:
    """POST /promote — escalate to HUMAN_LOCKED.

    Spec quote (§M6 line 765):
        «Confirms → ``conversation.tier_changed`` to HUMAN_LOCKED,
        ``tier_override`` audit with ``actor_role=master``, push to
        admin via bot DM.»

    Steps:
      1. Verify ownership.
      2. Reject already-locked (409).
      3. Validate reason_class ∈ {complaint, financial, medical, other}.
      4. Update tier + tier_locked_at + tier_locked_by_master +
         reason_class + reason_text atomically.
      5. Audit + emit ``conversation.tier_promoted_to_human_locked``.
      6. After commit: MAX DM to ``tenant.manager_chat_id`` (no-op if
         empty, mirroring availability_request pattern).
    """

    if now is None:
        now = dj_timezone.now()

    conv = _verify_master_involved(master, conversation_id)

    if conv.tier == Conversation.Tier.HUMAN_LOCKED:
        raise ConversationDetailError(
            "already_locked",
            "conversation is already human-locked",
            status=409,
        )

    rc = (reason_class or "").strip().lower()
    if rc not in VALID_REASON_CLASSES:
        raise ConversationDetailError(
            "bad_request",
            f"reason_class must be one of {sorted(VALID_REASON_CLASSES)}",
            status=400,
        )

    rt = (reason_text or "").strip()
    if len(rt) > 500:
        raise ConversationDetailError(
            "bad_request",
            "reason_text exceeds 500 characters",
            status=400,
        )

    payload = {
        "tenant_id": str(master.tenant_id),
        "conversation_id": str(conv.id),
        "master_id": str(master.id),
        "reason_class": rc,
        "reason_text": rt,
    }

    with transaction.atomic():
        Conversation.all_tenants.filter(pk=conv.pk).update(
            tier=Conversation.Tier.HUMAN_LOCKED,
            tier_reason_class=rc,
            tier_locked_at=now,
            tier_locked_by_master=master,
            tier_locked_reason_text=rt,
        )
        conv.tier = Conversation.Tier.HUMAN_LOCKED
        conv.tier_reason_class = rc
        conv.tier_locked_at = now
        conv.tier_locked_by_master = master
        conv.tier_locked_reason_text = rt

        write_audit(
            CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED,
            target="Conversation",
            target_id=conv.id,
            payload=payload,
            actor_id=actor_bot_user.id if actor_bot_user is not None else None,
        )
        emit(CONVERSATION_TIER_PROMOTED_TO_HUMAN_LOCKED, properties=payload)

        # MAX DM to the manager — best-effort, post-commit. Bound the
        # captured arguments so the on_commit closure is safe even if
        # the outer scope is mutated.
        bound_tenant = master.tenant
        bound_master = master
        client_first, client_initial = _split_name(_client_name_for(conv))
        client_label = f"{client_first} {client_initial}".strip() if client_first else "клиента"
        transaction.on_commit(
            lambda: _maybe_send_manager_dm(
                tenant=bound_tenant,
                master=bound_master,
                client_label=client_label,
                reason_class=rc,
            )
        )

    return PromoteResponse(
        conversation_id=str(conv.id),
        tier=conv.tier,
        tier_locked_at=now.isoformat(),
    )


def _maybe_send_manager_dm(
    *,
    tenant: Any,
    master: CatalogMaster,
    client_label: str,
    reason_class: str,
) -> None:
    """Dispatch the «Анна передала диалог в админ-канал» DM.

    No-op when ``manager_chat_id`` is empty — degraded mode aligned
    with :func:`apps.master_api.views._maybe_send_manager_dm` and the
    reminder-escalation pattern.

    Local imports so the channels module isn't loaded by tests that
    don't need it.
    """

    chat_id = (tenant.manager_chat_id or "").strip()
    if not chat_id:
        logger.info(
            "master_api.conversations.promote.no_manager_chat_id tenant=%s master=%s",
            tenant.id,
            master.id,
        )
        return

    from apps.channels.max.outbound import MaxAPIError, send_message

    text = f"{master.name} передала диалог с {client_label} в админ-канал. Причина: {reason_class}"
    try:
        send_message(chat_id=chat_id, text=text)
    except MaxAPIError:
        logger.warning(
            "master_api.conversations.promote.manager_dm_failed tenant=%s master=%s",
            tenant.id,
            master.id,
            exc_info=True,
        )


__all__ = [
    "ConversationDetailError",
    "ConversationDetailResponse",
    "ConversationNotInvolved",
    "MAX_COMPOSE_LENGTH",
    "MAX_MESSAGES_PER_DETAIL",
    "MessageItem",
    "MessageResponse",
    "PromoteResponse",
    "VALID_REASON_CLASSES",
    "_attribution_from_message",
    "_redact_for_master",
    "_verify_master_involved",
    "get_conversation_detail",
    "mark_conversation_read",
    "promote_to_human_locked",
    "send_master_message",
]
