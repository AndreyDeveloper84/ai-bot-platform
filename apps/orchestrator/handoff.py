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
import re
import uuid

from django.conf import settings

from apps.events.services import emit
from apps.orchestrator.discovery import CALLBACK_DISCOVER_BOOK_PREFIX, DiscoveryReply

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
# tap). Booking cannot start without one, so this branch answers with THIS
# master's real services — they are one tenant-scoped queryset away.
#
# DRF-1070: those services are rendered as BUTTONS, not as text examples.
# Until now the reply named up to three of them and asked the user to type one
# back, betting that they would reproduce the exact name. Live dialog
# 2026-08-14 (owner, «Сазонова Инна») shows the bet losing: two typed attempts
# («Лимфодренажный массаж», «Биоэнергетический») looped back to the same card
# and the same question, and the funnel only moved on the third try, when the
# long name was reproduced verbatim. A button carries the service id, so
# nothing has to be spelled — and it reuses the ALREADY-WORKING contract
# ``cb:discover:book:<tenant>:<master>:<service>`` (verified end-to-end in the
# same dialog at 10:46: card button with a service → date → slot → booked).
# The text keeps the same names as a bulleted list, mirroring
# ``_render_master_cards``, so a channel that drops keyboards still shows a
# workable next step.
#
# Never dispatch a serviceless pick_master: the booking skill will only answer
# it with the stale-context text.
_ASK_SERVICE_PICK = "Выберите услугу мастера {name}:"
# The tap named a service this master does not offer (no MasterService edge).
# Saying so is the whole point: the old reply re-asked the same question, and
# the user had no way to learn that the name was fine but the master was wrong.
_ASK_SERVICE_NOT_OFFERED = "У мастера {name} нет услуги «{service}». Вот что можно выбрать:"
_ASK_SERVICE_NOT_OFFERED_BARE = (
    "У мастера {name} нет услуги «{service}», а других доступных услуг у него сейчас нет — "
    "попробуйте выбрать другого мастера."
)
_ASK_SERVICE_REPLY_BARE = (
    "Чтобы записаться к мастеру {name}, напишите, какая услуга вас интересует."
)
# Shown when the master offers more services than the keyboard carries. Typing
# stays available as the escape hatch — it is a worse path (that is this
# ticket), but for a long roster it is the only one left, so the message says
# plainly that the list is partial instead of pretending it is complete.
_ASK_SERVICE_TRUNCATED_NOTE = (
    "Показаны первые {shown} услуг — если нужной нет в списке, напишите её название."
)
# Keyboard budget for the service menu. MAX hard-caps an inline_keyboard at
# ``apps.channels.max.outbound.MAX_KEYBOARD_ROWS`` (29) and silently clamps
# past it, so any limit must sit below that; 10 also keeps the mirrored text
# list inside the ~600-char reply budget the discovery renderer works to and
# keeps the keyboard scannable. Ordered by name — a stable, explainable order
# (there is no popularity signal in the catalog mirror to rank by).
_ASK_SERVICE_BUTTON_LIMIT = 10


def _ask_service_reply(
    *,
    tenant_id: uuid.UUID,
    master_id: uuid.UUID,
    master_name: str,
    rows: list[tuple[uuid.UUID, str]],
    truncated: bool,
    not_offered_name: str | None,
) -> DiscoveryReply:
    """Render the ask-the-service answer: buttons + a mirrored text list.

    ``rows`` are ``(CatalogService.pk, name)`` pairs the caller already
    filtered down to services this handoff can actually ground — the button
    must not lead back here. Each becomes a
    ``cb:discover:book:<tenant>:<master>:<service>`` tap, i.e. the SAME
    keyboard contract ``_render_master_cards`` emits and the same one the
    global MAX handler parses in ``_discovery_handoff_reply`` — this reply
    adds no new callback grammar, it just re-enters the handoff with the id
    the user could not be expected to spell.

    The text repeats the identical names as a bulleted list (the
    ``_render_master_cards`` shape). Two reasons: a channel that drops
    keyboards still gets a workable next step — typing the exact name IS the
    path that works today — and the bullets give the user that exact spelling
    to copy instead of reconstructing it from memory.

    Empty ``rows`` means there is nothing to offer, so no keyboard is built:
    an empty ``buttons`` list is dropped by ``_build_attachments`` anyway, and
    a header promising a list nobody can see would repeat this ticket's bug.
    """
    if not rows:
        text = (
            _ASK_SERVICE_NOT_OFFERED_BARE.format(name=master_name, service=not_offered_name)
            if not_offered_name is not None
            else _ASK_SERVICE_REPLY_BARE.format(name=master_name)
        )
        return DiscoveryReply(text=text)

    header = (
        _ASK_SERVICE_NOT_OFFERED.format(name=master_name, service=not_offered_name)
        if not_offered_name is not None
        else _ASK_SERVICE_PICK.format(name=master_name)
    )
    lines = [header]
    lines.extend(f"• {name}" for _, name in rows)
    if truncated:
        lines.append(_ASK_SERVICE_TRUNCATED_NOTE.format(shown=len(rows)))
    buttons = [
        {
            "label": name,
            "callback": f"{CALLBACK_DISCOVER_BOOK_PREFIX}{tenant_id}:{master_id}:{service_pk}",
        }
        for service_pk, name in rows
    ]
    return DiscoveryReply(
        text="\n".join(lines),
        action_data={"attachments": [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]},
    )


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
            # Why the tap failed, for the user-facing wording (DRF-1070). The
            # row is re-read WITHOUT ``is_active`` and WITHOUT the flag guard:
            # the question here is only "does this master offer it", and the
            # edge is a fact regardless of the pilot flag. Tenant-scoped, so a
            # foreign/forged id simply misses and we stay on the neutral text —
            # we never name a service we could not see inside T.
            not_offered_name: str | None = None
            if service_id is not None:
                tapped = (
                    CatalogService.objects.filter(id=service_id)
                    .values_list("name", flat=True)
                    .first()
                )
                if (
                    tapped is not None
                    and not MasterService.all_tenants.filter(
                        tenant=tenant, master_id=master.id, service_id=service_id
                    ).exists()
                ):
                    not_offered_name = tapped
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
                    # Separates "master doesn't do this" from every other miss
                    # (serviceless tap, forged id, legacy flag) — the two
                    # cohorts need different product fixes.
                    "not_offered_by_master": not_offered_name is not None,
                },
            )
            # Offer only services this very function could ground on the next
            # tap, because the button re-enters HERE: active, offered by this
            # master (the ``masters_offering`` edge — unique per
            # (master, service), so no duplicate buttons), and carrying a
            # non-NULL ``ayla_service_id``. A button that misses any of those
            # would land the user back on this same reply — the DRF-1070 loop
            # with a tap instead of a typo.
            #
            # Flag OFF ⇒ NO menu at all. The grounding gate above is
            # ``service_id is not None and flag_on``, so with the legacy
            # YClients flag NOTHING is deliverable (DRF-962: ``external_id``
            # is the mysite pk, not a verified YClients service id). Listing
            # services there — as buttons or as text — offers a path that
            # provably cannot complete; the bare "which service?" line is the
            # honest answer until the flag is on. Global-path booking under
            # the legacy flag is already a dead end and is not this ticket.
            rows: list[tuple[uuid.UUID, str]] = []
            truncated = False
            if flag_on:
                menu_qs = CatalogService.objects.filter(
                    masters_offering__master=master,
                    is_active=True,
                    ayla_service_id__isnull=False,
                )
                # +1 row to detect truncation without a second COUNT query.
                rows = list(
                    menu_qs.order_by("name").values_list("id", "name")[
                        : _ASK_SERVICE_BUTTON_LIMIT + 1
                    ]
                )
                truncated = len(rows) > _ASK_SERVICE_BUTTON_LIMIT
                rows = rows[:_ASK_SERVICE_BUTTON_LIMIT]
            return _ask_service_reply(
                tenant_id=tenant_id,
                master_id=master_id,
                master_name=master_name,
                rows=rows,
                truncated=truncated,
                not_offered_name=not_offered_name,
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

        carry_time_preference(global_bot_user, conversation)

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
    # DRF-1325 — the time chips. Both carry the master id first, exactly like
    # pick_date, so tenant resolution below needs no new shape.
    "cb:book:pick_part:",
    "cb:book:more_dates:",
    "cb:book:pick_slot:",
    "cb:book:confirm:",
    "cb:book:cancel:",
)

# Deterministic reply when the tap's tenant can no longer be resolved (stale
# keyboard after pending-row cleanup, forged id, flag-off int ids). Mirrors
# the booking skill's own stale-context reply — the user restarts selection.
_STALE_BOOKING_CALLBACK_REPLY = "Контекст записи устарел. Начните выбор услуги заново."


def carry_time_preference(global_bot_user, conversation) -> None:
    """Copy the user's «завтра вечером» onto tenant T's conversation (DRF-1325).

    The person says WHEN on the global bot; the flow that has to honour it
    runs inside ``tenant_scope(T)`` against a different ``Conversation`` row.
    Without this copy the preference dies at the tenant boundary — a smaller
    version of the exact defect the ticket is about.

    Best-effort by contract: losing the preference costs the day chips, i.e.
    the ticket's own no-preference path, and must never cost the turn.
    """
    from apps.conversations.services import resolve_active_conversation
    from apps.orchestrator.time_preference import (
        load_time_preference,
        save_time_preference,
    )
    from apps.tenancy.context import tenant_scope

    if conversation is None:
        return
    try:
        # The global conversation lives at the tenant-less scope the caller
        # has already left, so read it back under that scope explicitly.
        with tenant_scope(None):
            source = resolve_active_conversation(global_bot_user)
        pref = load_time_preference(source)
        if pref is not None:
            save_time_preference(conversation, pref)
    except Exception:  # noqa: BLE001 — a hint must never break a booking turn
        logger.exception("marketplace.handoff.time_pref_carry_failed")


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

    for prefix in (
        "cb:book:pick_master:",
        "cb:book:pick_date:",
        "cb:book:pick_part:",
        "cb:book:more_dates:",
        "cb:book:pick_slot:",
    ):
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

        # Re-carry on every tap: the day / part chips are separate turns and
        # each of them has to know what the user asked for out loud.
        carry_time_preference(global_bot_user, conversation)

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


# ─── Global-path human handoff (DRF-1015) ─────────────────────────────────
#
# The tenant-less discovery path used to send a «нужен человек» turn straight
# to the concierge LLM — no AdminTask, no mute, no way to reach a human at
# all. This block adds the deterministic pre-LLM trigger (same pattern as the
# ``cb:book:*`` branch in the MAX global handler), the queue-addressing rule
# (brief §3) and the mute guard that keeps the bot silent while an operator
# drives ANY of the user's dialogs.

# A keyword occurrence is rejected when a standalone negation particle sits
# within this many characters before it («мне не нужен оператор»). Word-boundary
# matching: Cyrillic letters are word chars, so «ненужен» does not false-trip.
_NEGATION_WINDOW = 15
_NEGATION_RE = re.compile(r"\b(?:не|без)\b")


def matches_human_handoff_request(text: str) -> bool:
    """Deterministic «user asks for a human» check for the global path (DRF-1015).

    Reuses the tenant skill's ``_HANDOFF_KEYWORDS`` — imported, NEVER
    duplicated, so DRF-972's dictionary extension lands on both paths at once.
    Plain substring matching would fire on «мне не нужен оператор», so an
    occurrence is rejected when a standalone «не»/«без» appears in the short
    window before it; the text counts as a request when at least one
    occurrence is NOT negated. Deliberately a small deterministic filter, not
    a classifier — the pilot needs a working exit to a human, not perfect NLU.
    """
    from apps.skills.human_handoff.skill import _HANDOFF_KEYWORDS

    lower = text.lower()
    for keyword in _HANDOFF_KEYWORDS:
        start = 0
        while True:
            idx = lower.find(keyword, start)
            if idx < 0:
                break
            window = lower[max(0, idx - _NEGATION_WINDOW) : idx]
            if not _NEGATION_RE.search(window):
                return True
            start = idx + len(keyword)
    return False


def global_handoff_muted(*, conversation, channel: str, channel_user_id: str) -> bool:
    """True while a human operator drives ANY of this user's dialogs (DRF-1015).

    Two cases:
      * the GLOBAL conversation itself is in HUMAN_HANDOFF — the platform-queue
        task anchors it (``create_admin_task`` flipped the state);
      * an OPEN/IN_PROGRESS HANDOFF AdminTask exists for ANY BotUser of this
        channel identity — the task went to a salon's queue, and the global
        chat must not answer in parallel with the operator.

    Mute radius — conscious decision (REPLY_DRF-1015 №1): mute EVERYWHERE, not
    only for global-born tasks. A false escalation mutes wider than before, but
    a bot talking over a human operator is the worse failure; release needs no
    bookkeeping — closing the task (DRF-980) flips the tenant conversation back
    and empties this open-task query, so both dialogs resume on their own.

    Cost: one query — an indexed BotUser id subquery + an AdminTask EXISTS
    filtered by ``task_type``/``status`` (``status`` is db_indexed).
    """
    from apps.conversations.models import Conversation
    from apps.handoff.models import AdminTask
    from apps.identity.models import BotUser

    if conversation.state == Conversation.State.HUMAN_HANDOFF:
        return True
    return AdminTask.all_tenants.filter(
        bot_user_id__in=BotUser.all_tenants.filter(
            channel=channel, channel_user_id=channel_user_id
        ).values("id"),
        task_type=AdminTask.TaskType.HANDOFF,
        status__in=(AdminTask.Status.OPEN, AdminTask.Status.IN_PROGRESS),
    ).exists()


def route_global_human_handoff(
    *,
    global_bot_user,
    global_conversation,
    message_text: str,
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Escalate a tenant-less «нужен человек» turn to a human (DRF-1015).

    Queue addressing (brief §3): when the channel identity has active
    per-tenant conversation(s), the task lands on the MOST RECENT tenant's
    conversation — that salon is the side that can actually help, and
    ``create_admin_task`` mutes the tenant dialog for free. Without a tenant
    context the task lands on the GLOBAL conversation under the sentinel
    tenant — the platform queue. Either way the user gets the same
    confirmation line the tenant skill uses (``_HANDOFF_REPLY`` — reused, not
    reworded).
    """
    from apps.handoff.models import AdminTask
    from apps.handoff.services import create_admin_task
    from apps.identity.services.global_tenant import get_global_bot_tenant
    from apps.skills.human_handoff.skill import _HANDOFF_REPLY
    from apps.tenancy.context import tenant_scope

    reason = f"Global-path trigger phrase: {message_text[:80]}"
    target = _latest_tenant_conversation(global_bot_user)
    if target is not None:
        with tenant_scope(target.tenant):
            task = create_admin_task(
                target,
                task_type=AdminTask.TaskType.HANDOFF,
                reason=reason,
            )
    else:
        sentinel = get_global_bot_tenant()
        with tenant_scope(sentinel):
            task = create_admin_task(
                global_conversation,
                task_type=AdminTask.TaskType.HANDOFF,
                reason=reason,
            )
    logger.info(
        "marketplace.human_handoff.routed task=%s tenant=%s global_conversation=%s trace=%s",
        task.id,
        task.tenant_id,
        global_conversation.id,
        trace_id,
    )
    return DiscoveryReply(text=_HANDOFF_REPLY)


def _latest_tenant_conversation(global_bot_user):
    """Most recently active per-tenant Conversation for this channel identity.

    ``None`` when the user has never talked to a salon — the caller then falls
    back to the platform queue (sentinel). «Most recent» = latest
    ``last_message_at`` (tie-break ``created_at``): the salon the user spoke
    with last is the most plausible addressee, and asking «which salon?» would
    add a round-trip to the emergency path.
    """
    from apps.conversations.models import Conversation
    from apps.identity.services.global_tenant import get_global_bot_tenant

    sentinel = get_global_bot_tenant()
    return (
        Conversation.all_tenants.filter(
            bot_user__channel=global_bot_user.channel,
            bot_user__channel_user_id=global_bot_user.channel_user_id,
            is_active=True,
            deleted_at__isnull=True,
        )
        .exclude(tenant_id=sentinel.id)
        .order_by("-last_message_at", "-created_at")
        .select_related("tenant")
        .first()
    )


# ─── Global-path personal booking lookup — REMOVED (DRF-1032) ───────────────
#
# What stood here answered «покажи мои записи» on the global path by walking
# the caller's tenants and reading the local mirror: ``_booking_lookup_scopes``
# (its DISTINCT-ordering fix was DRF-1033), ``_lookup_in_tenant``,
# ``_compose_multi_tenant_text`` and ``route_global_booking_lookup``.
#
# All four are gone because the customer-facing answer now comes from the Ayla
# backend (OD-H1) via ``apps.booking.services.records`` and
# ``apps.orchestrator.visits``. Two of their reasons for existing disappeared
# with the source: the backend lists bookings across every tenant itself
# (``records_api.py:312-323``), so nothing needs to aggregate them here, and
# with one source there is no per-salon section that can fail on its own.
#
# DRF-1033 was accepted live on the pilot on 2026-08-14 before this removal;
# its regression is still pinned, now against the backend-sourced reply.
