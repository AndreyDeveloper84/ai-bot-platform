"""Privacy tools — data_export + data_delete (DRF-469 / Sprint 3 / D2).

Two callable tools the PrivacyConsentSkill (and any future admin
flow) uses to fulfil 152-ФЗ / GDPR data subject rights.

### data_export(bot_user) → dict

Returns a JSON-serialisable dict containing:
- bot_user shape (PII-minimal — phone hash, not raw)
- ConsentRecord rows for the user (full history)
- Conversation list with their Message rows

Sprint 3 ships JSON only. PDF / archive URL → Phase 1.

### data_delete(bot_user) → int

.. warning::

   **Not reachable from the chat since DRF-956 / T-05.** This helper
   hard-deletes the ``BotUser`` row, which is referenced
   ``on_delete=PROTECT`` by ``observability.AIRequestMetric``,
   ``handoff.AdminTask`` and ``tenancy.StaffAssignment`` — so it raises
   ``ProtectedError`` for any user who has ever triggered an AI turn or a
   human handoff. It survives only as the admin-facing "wipe a user with
   no protected references" helper. The customer-facing erasure path is
   :func:`apps.identity.services.privacy.delete_personal_data`, which
   erases in place and keeps the shell. Do not re-wire this into a
   customer surface without fixing the PROTECT references first.

Hard-deletes the BotUser via the existing
:func:`apps.identity.services.delete_bot_user_data` (Sprint 2.5 H1).
That helper already:
- soft-marks active Conversations
- hard-deletes Conversations → Messages cascade
- hard-deletes the BotUser → ConsentRecord cascade (FK CASCADE on
  ConsentRecord.bot_user)
- preserves AuditLog rows (forensic trail of the deletion itself)

We add an extra `privacy.data_delete.requested` audit row BEFORE
calling the helper — even though the helper writes its own
`identity.bot_user.delete_started/finished` rows, the skill-level audit
records *why* the delete happened (user typed "удалить мои данные").
That separation lets compliance distinguish admin-initiated wipes from
user-initiated ones.

### Audit-before-delete invariant

The audit row goes in **before** the actual delete so the operator
record persists even after the user's own AuditLog rows would (in a
future variant) be subject to deletion. Sprint 3 keeps AuditLog rows
forever; that order still matters for future-proofing the workflow.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from apps.audit.services import write_audit
from apps.events.services import emit
from apps.events.vocabulary import TOOL_CALLED

if TYPE_CHECKING:
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


def _hash_phone(phone: str) -> str:
    """sha256 the raw phone for export storage — never the raw string."""

    if not phone:
        return ""
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def data_export(bot_user: "BotUser") -> dict[str, Any]:
    """Return a JSON-serialisable dict of every record we hold on `bot_user`.

    Shape::

        {
          "bot_user": {
            "id": "...uuid...",
            "channel": "max",
            "channel_user_id": "12345",
            "display_name": "Ivan",
            "phone_hash": "<sha256 or empty>",
            "first_seen": "...iso...",
            "last_seen": "...iso...",
          },
          "consents": [
            {"consent_type": "personal_data", "granted": true,
             "source": "...", "document_version": "privacy-v1.0",
             "captured_at": "...", "withdrawn_at": null},
            ...
          ],
          "conversations": [
            {"id": "...", "state": "idle", "outcome": "",
             "is_active": true, "created_at": "...", "deleted_at": null,
             "messages": [
               {"role": "user", "content": "...",
                "action_type": "", "created_at": "..."},
               ...
             ]},
            ...
          ],
        }
    """

    from apps.consent.models import ConsentRecord
    from apps.conversations.models import Conversation, Message

    write_audit(
        "privacy.data_export.requested",
        target="BotUser",
        target_id=bot_user.id,
        payload={"channel": bot_user.channel},
    )

    consents = list(ConsentRecord.all_tenants.filter(bot_user=bot_user).order_by("-captured_at"))
    conversations_qs = Conversation.all_tenants.filter(bot_user=bot_user).order_by("-created_at")

    conversations_payload: list[dict[str, Any]] = []
    for conv in conversations_qs:
        messages = Message.all_tenants.filter(conversation=conv).order_by("created_at")
        conversations_payload.append(
            {
                "id": str(conv.id),
                "state": conv.state,
                "outcome": conv.outcome or "",
                "is_active": conv.is_active,
                "created_at": conv.created_at.isoformat(),
                "deleted_at": conv.deleted_at.isoformat() if conv.deleted_at else None,
                "messages": [
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "action_type": msg.action_type or "",
                        "created_at": msg.created_at.isoformat(),
                    }
                    for msg in messages
                ],
            }
        )

    payload: dict[str, Any] = {
        "bot_user": {
            "id": str(bot_user.id),
            "channel": bot_user.channel,
            "channel_user_id": bot_user.channel_user_id,
            "display_name": bot_user.display_name,
            "phone_hash": _hash_phone(bot_user.phone or ""),
            "first_seen": (bot_user.first_seen.isoformat() if bot_user.first_seen else None),
            "last_seen": (bot_user.last_seen.isoformat() if bot_user.last_seen else None),
        },
        "consents": [
            {
                "consent_type": c.consent_type,
                "granted": c.granted,
                "source": c.source,
                "document_version": c.document_version,
                "captured_at": c.captured_at.isoformat(),
                "withdrawn_at": c.withdrawn_at.isoformat() if c.withdrawn_at else None,
            }
            for c in consents
        ],
        "conversations": conversations_payload,
    }

    emit(
        TOOL_CALLED,
        distinct_id=str(bot_user.id),
        properties={
            "tool": "data_export",
            "conversations": len(conversations_payload),
            "consents": len(consents),
        },
    )

    write_audit(
        "privacy.data_export.completed",
        target="BotUser",
        target_id=bot_user.id,
        payload={
            "conversations": len(conversations_payload),
            "consents": len(consents),
        },
    )
    logger.info(
        "privacy.data_export bot_user=%s conversations=%s consents=%s",
        bot_user.id,
        len(conversations_payload),
        len(consents),
    )
    return payload


def data_delete(bot_user: "BotUser") -> int:
    """Hard-delete every record we hold on `bot_user`. Returns conv count.

    Writes a `privacy.data_delete.requested` audit row BEFORE the
    delete so the operator-facing trail survives even when the user's
    own audit rows would (in a future variant) be subject to wipe.

    Cascades:
      - Conversation soft-deleted then hard-deleted via
        `delete_bot_user_data` (Sprint 2.5 H1).
      - Message rows go with the Conversation CASCADE.
      - ConsentRecord rows go with the BotUser CASCADE.
      - AuditLog rows are kept (forensic).
    """

    from apps.identity.services import delete_bot_user_data

    bot_user_id = bot_user.id
    channel = bot_user.channel

    write_audit(
        "privacy.data_delete.requested",
        target="BotUser",
        target_id=bot_user_id,
        payload={"channel": channel, "reason": "user_self_service"},
    )
    emit(
        TOOL_CALLED,
        distinct_id=str(bot_user_id),
        properties={"tool": "data_delete", "reason": "user_self_service"},
    )

    count = delete_bot_user_data(bot_user)

    write_audit(
        "privacy.data_delete.completed",
        target="BotUser",
        target_id=bot_user_id,
        payload={"channel": channel, "conversations_deleted": count},
    )
    logger.info(
        "privacy.data_delete bot_user=%s channel=%s conversations=%s",
        bot_user_id,
        channel,
        count,
    )
    return count
