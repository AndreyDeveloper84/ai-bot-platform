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
# in the discovery loop. The reply lists THIS master's real services (they are
# one tenant-scoped queryset away), so following the bot's own suggestion
# produces an exact-name query that discovery resolves unambiguously — a
# hardcoded example would send the user chasing a service the master may not
# even offer. Never dispatch a serviceless pick_master: the booking skill will
# only answer it with the stale-context text.
_ASK_SERVICE_REPLY_WITH_MENU = (
    "Чтобы записаться к мастеру {name}, напишите желаемую услугу. У мастера можно "
    "выбрать, например: {services}."
)
_ASK_SERVICE_REPLY_BARE = (
    "Чтобы записаться к мастеру {name}, напишите, какая услуга вас интересует."
)
_ASK_SERVICE_MENU_LIMIT = 3


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
        # ── by THIS master (MasterService edge — the same all_tenants +
        # ── explicit-ids shape the booking-side guards use) — a forged/stale
        # ── callback must not smuggle a foreign service into the booking
        # ── flow. Native grounding is Ayla-REST-only: ``ayla_service_id`` is
        # ── the one proven native family; the legacy mirror ``external_id``
        # ── is the mysite pk, NOT a verified YClients service id (the model
        # ── docstring and the booking skill disagree about it), so the
        # ── YClients-flag path never dispatches a service and falls back to
        # ── asking. Any miss → honest ask-the-service reply; NEVER a
        # ── serviceless dispatch (guaranteed stale-context dead-end on the
        # ── booking side).
        native_service_id: str | None = None
        if service_id is not None and flag_on:
            service = CatalogService.objects.filter(id=service_id, is_active=True).first()
            edge_exists = service is not None and (
                MasterService.all_tenants.filter(
                    tenant=tenant, master_id=master.id, service_id=service.id
                ).exists()
            )
            if service is not None and edge_exists and service.ayla_service_id is not None:
                native_service_id = str(service.ayla_service_id)
        if native_service_id is None:
            logger.info(
                "marketplace.handoff.service_unresolved tenant=%s master=%s service=%s trace=%s",
                tenant_id,
                master_id,
                service_id,
                trace_id,
            )
            # Funnel visibility (review): without an event, a cohort whose
            # every tap fails to resolve is indistinguishable from zero
            # traffic on the handoff.entered dashboard.
            emit(
                "marketplace.handoff.service_unresolved",
                payload={
                    "tenant_id": str(tenant_id),
                    "master_id": str(master_id),
                    # Real null for the serviceless-tap cohort — the literal
                    # string "None" would pollute the very stream this event
                    # exists to make queryable.
                    "service_id": str(service_id) if service_id is not None else None,
                    "booking_via_ayla_rest": flag_on,
                },
            )
            # Suggest only services the stamping gate can deliver: under the
            # Ayla flag that means a non-NULL ayla_service_id — naming a
            # service the user's next query still cannot resolve would keep
            # them in the loop this reply exists to break.
            menu_qs = CatalogService.objects.filter(masters_offering__master=master, is_active=True)
            if flag_on:
                menu_qs = menu_qs.filter(ayla_service_id__isnull=False)
            menu = list(
                menu_qs.order_by("name").values_list("name", flat=True)[:_ASK_SERVICE_MENU_LIMIT]
            )
            if menu:
                text = _ASK_SERVICE_REPLY_WITH_MENU.format(
                    name=master_name,
                    services=", ".join(f"«{name}»" for name in menu),
                )
            else:
                text = _ASK_SERVICE_REPLY_BARE.format(name=master_name)
            return DiscoveryReply(text=text)

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


# ---------------------------------------------------------------------------
# Post-handoff booking callbacks (DRF-988, Variant 1 — owner GO 2026-08-10)
# ---------------------------------------------------------------------------

# The booking callback namespace, routed back into tenant T's skill pipeline.
# Same S1 anti-touch contract as ``_CALLBACK_BOOK_PICK_MASTER`` above —
# literals mirroring apps/bookings/keyboards.py, never re-derived. The global
# MAX handler matches on this tuple before falling through to the concierge.
BOOKING_CALLBACK_PREFIXES = (
    "cb:book:pick_master:",
    "cb:book:pick_date:",
    "cb:book:pick_slot:",
    "cb:book:confirm:",
    "cb:book:cancel:",
)

# Deterministic reply when the tap's tenant can no longer be resolved (stale
# keyboard after pending-row cleanup, forged id, flag-off int ids). Mirrors
# the booking skill's own stale-context reply — the user restarts selection.
_STALE_BOOKING_CALLBACK_REPLY = "Контекст записи устарел. Начните выбор услуги заново."


def _resolve_booking_callback_tenant(callback_text: str):
    """Resolve tenant T for a post-handoff ``cb:book:*`` tap, or None.

    pick_master / pick_date / pick_slot carry the master id as the first
    payload segment (canonical Ayla UUID under the pilot flag — the same id
    family the handoff dispatch stamps); confirm / cancel carry the
    :class:`apps.booking.models.PendingBookingAction` token. The master
    lookup goes through :func:`apps.marketplace.discovery.get_master` — the
    SOLE sanctioned cross-tenant catalog carve-out (MKT1); the token lookup
    uses the same ``all_tenants`` + explicit-id shape the gate skill uses.
    Flag-off native int ids are deliberately NOT resolved: the pilot runs
    ``BOOKING_VIA_AYLA_REST``, and an int staff id is not unique across
    tenants.
    """
    from apps.booking.models import PendingBookingAction
    from apps.marketplace.discovery import get_master
    from apps.tenancy.models import Tenant

    if callback_text.startswith(("cb:book:confirm:", "cb:book:cancel:")):
        raw = callback_text.rsplit(":", 1)[-1].strip()
        try:
            token = uuid.UUID(raw)
        except (ValueError, AttributeError):
            return None
        row = PendingBookingAction.all_tenants.filter(pk=token).only("tenant").first()
        return row.tenant if row is not None else None

    for prefix in ("cb:book:pick_master:", "cb:book:pick_date:", "cb:book:pick_slot:"):
        if callback_text.startswith(prefix):
            raw_master = callback_text[len(prefix) :].split(":", 1)[0].strip()
            try:
                master_id = uuid.UUID(raw_master)
            except (ValueError, AttributeError):
                return None
            card = get_master(master_id)
            if card is None:  # unknown or no longer bookable — never dispatch
                return None
            return Tenant.objects.filter(id=card.tenant_id).first()
    return None


def route_booking_callback(
    *,
    global_bot_user,
    callback_text: str,
    chat_id: str = "",
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Route a post-handoff ``cb:book:*`` tap back into tenant T's skill pipeline.

    DRF-988: after the handoff, the booking skill renders its date / slot /
    confirm keyboards INTO the global chat, but the global MAX handler only
    routed ``cb:discover:book:`` — every booking tap fell through to the
    concierge as raw text, where the model produced free-text answers (the
    «2026 год» refusal) instead of the next booking step. This is the
    multi-turn counterpart of :func:`handoff_to_booking` (the follow-up its
    docstring named): resolve T from the callback, then delegate into the
    SAME per-tenant entrypoint (``apps.skills.registry.dispatch``) with the
    raw payload — the booking skill's deterministic pick_date / pick_slot
    short-circuits and the gate skill's confirm / cancel do the rest.
    """
    from apps.conversations.services import resolve_active_conversation
    from apps.identity.services import resolve_or_create_bot_user
    from apps.skills.base import SkillContext
    from apps.skills.registry import dispatch as skill_dispatch
    from apps.tenancy.context import tenant_scope

    tenant = _resolve_booking_callback_tenant(callback_text)
    if tenant is None:
        logger.info(
            "marketplace.booking_callback.unresolved callback=%r trace=%s",
            callback_text[:60],
            trace_id,
        )
        return DiscoveryReply(text=_STALE_BOOKING_CALLBACK_REPLY)

    with tenant_scope(tenant):
        per_tenant_bot_user = resolve_or_create_bot_user(
            channel=global_bot_user.channel,
            channel_user_id=global_bot_user.channel_user_id,
            chat_id=chat_id or global_bot_user.chat_id,
            display_name=global_bot_user.display_name,
            phone=global_bot_user.phone,
        )
        conversation = resolve_active_conversation(per_tenant_bot_user)
        if conversation is None:
            logger.warning(
                "marketplace.booking_callback.no_conversation tenant=%s trace=%s",
                tenant.id,
                trace_id,
            )
            return DiscoveryReply(text=_STALE_BOOKING_CALLBACK_REPLY)

        # Funnel visibility, symmetric with marketplace.handoff.entered.
        emit(
            "marketplace.booking_callback.routed",
            payload={
                "tenant_id": str(tenant.id),
                "bot_user_id": str(per_tenant_bot_user.id),
                "callback": ":".join(callback_text.split(":")[:3]),
            },
        )

        result = skill_dispatch(
            SkillContext(
                conversation=conversation,
                bot_user=per_tenant_bot_user,
                message_text=callback_text,
                trace_id=str(trace_id) if trace_id else "",
            )
        )

        # Post-dispatch handoff (#1047), symmetric with handoff_to_booking:
        # the booking/gate skill may escalate to a human; create_admin_task
        # requires tenant_scope(T), which we are inside. The conversation
        # already carries the booking turns here (unlike the freshly-resolved
        # handoff conversation), so no extra context line is recorded.
        if result is not None and getattr(result, "should_handoff", False):
            from apps.handoff.models import AdminTask
            from apps.handoff.services import create_admin_task

            create_admin_task(
                conversation,
                task_type=AdminTask.TaskType.HANDOFF,
                reason=result.handoff_reason or "booking_handoff",
            )

    reply_text = (result.reply_text if result is not None else "") or _STALE_BOOKING_CALLBACK_REPLY
    action_data = result.action_data if result is not None else None
    return DiscoveryReply(text=reply_text, action_data=action_data)
