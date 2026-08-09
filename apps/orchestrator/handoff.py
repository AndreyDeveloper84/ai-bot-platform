"""Discovery → booking handoff (#1020 / EPIC #1014, P3).

The global discovery bot recommends cross-tenant masters (public ``MasterCard``
DTOs from ``apps.marketplace``). When the user taps "book this master", the
conversation must transition from the global sentinel scope into the chosen
**tenant T's** booking flow.

Order-invariant (the heart of P3, locked by tests): the handoff carries only the
PUBLIC ``tenant_id`` + ``master_id`` from the DTO. The commercial / native-id
read (``CatalogMaster`` → ``yclients_staff_id``) happens **only after**
``tenant_scope(T)`` is entered — NEVER at ``current_tenant()=None`` — so the
``CrossTenantError`` invariant is preserved.

Scope (per #1020 decision "Initiation + invariant"): this layer performs the
single audited transition + delegates into the EXISTING per-tenant booking
entrypoint (``apps.skills.registry.dispatch``). It does not reimplement booking
and does not modify the booking skill. The full multi-turn booking session
routed back through the global bot is a follow-up (after the P0 Ayla reground).
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings

from apps.events.services import emit
from apps.orchestrator.discovery import DiscoveryReply

logger = logging.getLogger(__name__)

# Booking skill's stable master-pick callback contract (apps/skills/booking —
# S1 anti-touch). Format ``cb:book:pick_master:<master>:<service>`` — the
# service part is REQUIRED: without it the skill's stale-context guard
# (deliberately, RB1.1-D05) refuses the tap with «Контекст записи устарел»,
# which on this path was a guaranteed dead-end (DRF-962). Under the YClients
# path both ids are native ints; under Ayla REST both are canonical UUIDs.
_CALLBACK_BOOK_PICK_MASTER = "cb:book:pick_master:"

_UNAVAILABLE_REPLY = (
    "К сожалению, запись к этому мастеру сейчас недоступна — попробуйте выбрать другого."
)

# The tap carried no bookable service (pre-DRF-962 keyboard, an ambiguous
# query like bare «массаж», or a service that went inactive between render and
# tap). Booking cannot start without one — asking is honest and keeps the user
# in the discovery loop, where a service-specific query renders cards that DO
# carry the service. Never dispatch a serviceless pick_master: the booking
# skill will only answer it with the stale-context text.
_ASK_SERVICE_REPLY = (
    "Чтобы записаться к мастеру {name}, уточните услугу — напишите, например: «{example} у {name}»."
)
_ASK_SERVICE_EXAMPLE = "спортивный массаж"


def handoff_to_booking(
    *,
    global_bot_user,
    tenant_id: uuid.UUID,
    master_id: uuid.UUID,
    service_id: uuid.UUID | None = None,
    chat_id: str = "",
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Transition a discovery user into tenant T's booking flow for a master.

    Args:
      global_bot_user: the sentinel-scoped BotUser from the discovery turn.
      tenant_id / master_id: PUBLIC ids from the tapped ``MasterCard`` button.
      service_id: PUBLIC catalog id of the service discovery matched
        (DRF-962), or ``None`` for a serviceless card — answered with an
        ask-the-service reply, never a doomed booking dispatch.
      chat_id: MAX chat id (outbound is chat-based, tenant-free).

    Returns:
      A :class:`DiscoveryReply` (the booking entrypoint's reply, or a graceful
      "unavailable" message). Commercial reads happen only inside ``tenant_scope``.
    """
    # Local imports keep app-load order clean + the tenant-scoped models out of
    # module import time.
    from apps.catalog.models import CatalogMaster, CatalogService, MasterService
    from apps.conversations.services import resolve_active_conversation
    from apps.identity.services import resolve_or_create_bot_user
    from apps.skills.base import SkillContext
    from apps.skills.registry import dispatch as skill_dispatch
    from apps.tenancy.context import tenant_scope
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.filter(id=tenant_id).first()
    if tenant is None:
        logger.warning("marketplace.handoff.unknown_tenant tenant=%s trace=%s", tenant_id, trace_id)
        return DiscoveryReply(text=_UNAVAILABLE_REPLY)

    # ── Enter T's scope. Everything below is correctly scoped to T; this is the
    # ── ONLY place commercial state is read for this handoff. ───────────────
    with tenant_scope(tenant):
        per_tenant_bot_user = resolve_or_create_bot_user(
            channel=global_bot_user.channel,
            channel_user_id=global_bot_user.channel_user_id,
            chat_id=chat_id or global_bot_user.chat_id,
            display_name=global_bot_user.display_name,
            phone=global_bot_user.phone,
        )

        # Tenant-scoped (NOT all_tenants) commercial read — the master must
        # belong to T. A foreign/forged master_id → DoesNotExist → graceful.
        master = CatalogMaster.objects.filter(id=master_id).first()
        if master is None:
            logger.warning(
                "marketplace.handoff.master_not_in_tenant tenant=%s master=%s trace=%s",
                tenant_id,
                master_id,
                trace_id,
            )
            return DiscoveryReply(text=_UNAVAILABLE_REPLY)

        # Resolve the native master id the booking entrypoint expects, per the
        # BOOKING_VIA_AYLA_REST flag. yclients_staff_id is NULLABLE (master not
        # linked to YClients) — handle None gracefully, never crash.
        flag_on = bool(getattr(settings, "BOOKING_VIA_AYLA_REST", False))
        if flag_on:
            native_master_id = str(master_id)  # canonical Ayla UUID
        elif master.yclients_staff_id is None:
            logger.info(
                "marketplace.handoff.master_unlinked tenant=%s master=%s trace=%s",
                tenant_id,
                master_id,
                trace_id,
            )
            return DiscoveryReply(text=_UNAVAILABLE_REPLY)
        else:
            native_master_id = str(master.yclients_staff_id)

        # Capture the public display name inside the scope so the fallback reply
        # below never touches a tenant-scoped model at current_tenant()=None.
        master_name = master.name

        # ── Service context (DRF-962). Verify the tapped service inside the
        # ── same scope: it must exist in T, be active, and actually be offered
        # ── by THIS master (MasterService edge) — a forged/stale callback must
        # ── not smuggle a foreign service into the booking flow. Then resolve
        # ── the native id the booking entrypoint expects, mirroring the
        # ── master's flag handling above. Any miss → honest ask-the-service
        # ── reply; NEVER a serviceless dispatch (guaranteed stale-context
        # ── dead-end on the booking side).
        native_service_id: str | None = None
        if service_id is not None:
            service = CatalogService.objects.filter(id=service_id, is_active=True).first()
            edge_exists = service is not None and (
                MasterService.objects.filter(master=master, service=service).exists()
            )
            if service is not None and edge_exists:
                native_raw = service.ayla_service_id if flag_on else service.external_id
                if native_raw is not None:
                    native_service_id = str(native_raw)
        if native_service_id is None:
            logger.info(
                "marketplace.handoff.service_unresolved tenant=%s master=%s service=%s trace=%s",
                tenant_id,
                master_id,
                service_id,
                trace_id,
            )
            return DiscoveryReply(
                text=_ASK_SERVICE_REPLY.format(name=master_name, example=_ASK_SERVICE_EXAMPLE)
            )

        conversation = resolve_active_conversation(per_tenant_bot_user)
        if conversation is None:
            logger.warning(
                "marketplace.handoff.no_conversation tenant=%s master=%s trace=%s",
                tenant_id,
                master_id,
                trace_id,
            )
            return DiscoveryReply(text=_UNAVAILABLE_REPLY)

        emit(
            "marketplace.handoff.entered",
            payload={
                "tenant_id": str(tenant_id),
                "master_id": str(master_id),
                "service_id": str(service_id),
                "bot_user_id": str(per_tenant_bot_user.id),
                "is_global_bot": True,
            },
        )

        # Delegate into the EXISTING per-tenant booking entrypoint — do not
        # reimplement booking. Pass master + service in the form it expects
        # (both are required by the pick_master contract — see the prefix
        # comment above).
        result = skill_dispatch(
            SkillContext(
                conversation=conversation,
                bot_user=per_tenant_bot_user,
                message_text=(
                    f"{_CALLBACK_BOOK_PICK_MASTER}{native_master_id}:{native_service_id}"
                ),
                trace_id=str(trace_id) if trace_id else "",
            )
        )

        # Post-dispatch handoff (#1047): the booking entrypoint may escalate to a
        # human (should_handoff). We are inside tenant_scope(T) here, which
        # create_admin_task requires — so the operator is notified even on the
        # global path. NOTE: the user is on the GLOBAL bot, so MUTING their
        # subsequent turns is the multi-turn-session follow-up (see module
        # docstring); this only guarantees the escalation task is created, matching
        # the per-tenant fix in apps/channels/max/handler.py.
        if result is not None and getattr(result, "should_handoff", False):
            from apps.conversations.services import record_message
            from apps.handoff.models import AdminTask
            from apps.handoff.services import create_admin_task

            # Record the booking-pick context BEFORE packaging the transcript —
            # this per-tenant conversation is freshly resolved on the global path
            # and otherwise holds no messages, so the operator's AdminTask snapshot
            # would be empty. Give them the "what did the user want" line.
            record_message(
                conversation,
                role="user",
                content=f"[глобальный бот] Запрос записи к мастеру {master_name}.",
                trace_id=trace_id,
            )
            create_admin_task(
                conversation,
                task_type=AdminTask.TaskType.HANDOFF,
                reason=result.handoff_reason or "booking_handoff",
            )

    reply_text = (result.reply_text if result is not None else "") or (
        f"Отлично! Записываю вас к мастеру {master_name}. Какая услуга интересует?"
    )
    action_data = result.action_data if result is not None else None
    return DiscoveryReply(text=reply_text, action_data=action_data)
