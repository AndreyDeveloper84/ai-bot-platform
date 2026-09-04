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
from apps.marketplace.discovery import (
    parse_stems,
    query_stems,
    service_rows_match_q,
    service_rows_score,
)
from apps.orchestrator.discovery import (
    CALLBACK_DISCOVER_BOOK_PREFIX,
    DiscoveryReply,
    decode_query_ref,
    encode_query_ref,
)

logger = logging.getLogger(__name__)

# Booking skill's stable master-pick callback contract (apps/skills/booking —
# S1 anti-touch). Format ``cb:book:pick_master:<master>:<service>`` — the
# service part is REQUIRED: without it the skill's incomplete-callback guard
# (deliberately, RB1.1-D05) refuses the tap, which on this path was a
# guaranteed dead-end (DRF-962). Under the YClients
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
# DRF-1324 — the menu was narrowed by the request that surfaced this master,
# so it is NOT the master's whole roster and must not read as one. Live pilot
# 23.08: «запиши на лимфодренаж» → ten of Сазонова's nineteen services in
# alphabetical order, «Биоэнергетический массаж детский» second, lymphatic
# drainage sixth — and the booking that came out of that tap was the
# children's massage. The list is now the услуги of the request; this line is
# the escape hatch for a person who wanted something else after all, and it
# replaces the truncation note rather than joining it (the count of a filtered
# list says nothing about a roster).
_ASK_SERVICE_FILTERED_NOTE = (
    "Показаны услуги по вашему запросу — если нужно другое, напишите название."
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
    filtered: bool = False,
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
    if filtered:
        # Said whether or not the list was also truncated: «показаны первые N»
        # under a filtered list would claim the master has N services, which
        # is a different and false statement.
        lines.append(_ASK_SERVICE_FILTERED_NOTE)
    elif truncated:
        lines.append(_ASK_SERVICE_TRUNCATED_NOTE.format(shown=len(rows)))
    buttons = [
        {
            "label": name,
            "callback": f"{CALLBACK_DISCOVER_BOOK_PREFIX}{tenant_id}:{master_id}:{service_pk}",
        }
        for service_pk, name in rows
    ]
    # No query ref on these: each button already names ONE service, so the tap
    # re-enters with a resolved ``service_id`` and never reaches the menu
    # branch that would use it. Carrying it anyway would only lengthen the
    # payload of the path where it can have no effect.
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
    query_ref: str = "",
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
      query_ref: the encoded request that surfaced this master (DRF-1324),
        used to narrow that ask-the-service reply to the services the person
        actually asked about. Empty — or undecodable — means «do not narrow»,
        and the reply falls back to the master's whole roster, which is what
        this surface did before DRF-1324.
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
            filtered = False
            if flag_on:
                menu_qs = CatalogService.objects.filter(
                    masters_offering__master=master,
                    is_active=True,
                    ayla_service_id__isnull=False,
                )
                # DRF-1324 — narrow by the request that surfaced this master.
                # The SAME predicate the search used (imported, not
                # re-implemented), so the menu can never disagree with the
                # list the person tapped from.
                #
                # Applied only when it leaves something: an empty result means
                # the request no longer matches anything this master can
                # ground — the service went inactive, the mirror moved, the
                # callback is from an old render — and answering with an empty
                # menu would strand the tap. Falling back to the full roster is
                # the pre-DRF-1324 answer, which is worse but never a dead end.
                # ``parse_stems`` is the catalog-aware half of the search's
                # own parse — city split, then goal recognition — run HERE
                # rather than at render time, because it reads the catalog and
                # rendering must not. Same functions, same order, so a request
                # read off a button means what it meant when it produced the
                # card.
                parsed = parse_stems(decode_query_ref(query_ref))
                if not parsed.is_empty:
                    narrowed = menu_qs.filter(service_rows_match_q(parsed))
                    score = service_rows_score(parsed)
                    if score is not None:
                        # Best match first — «Лимфодренажный массаж» above a
                        # service that merely shares one stem. A goal query
                        # has no score (carrying a goal is not a matter of
                        # degree) and keeps the name order.
                        narrowed = narrowed.annotate(menu_score=score).order_by(
                            "-menu_score", "name"
                        )
                    else:
                        narrowed = narrowed.order_by("name")
                    narrowed_rows = list(
                        narrowed.values_list("id", "name")[: _ASK_SERVICE_BUTTON_LIMIT + 1]
                    )
                    if narrowed_rows:
                        rows, filtered = narrowed_rows, True
                if not filtered:
                    # +1 row to detect truncation without a second COUNT query
                    # (the narrowed read above takes the same +1 for the same
                    # reason).
                    rows = list(
                        menu_qs.order_by("name").values_list("id", "name")[
                            : _ASK_SERVICE_BUTTON_LIMIT + 1
                        ]
                    )
                truncated = len(rows) > _ASK_SERVICE_BUTTON_LIMIT
                rows = rows[:_ASK_SERVICE_BUTTON_LIMIT]
            logger.info(
                "marketplace.handoff.ask_service master=%s filtered=%s rows=%d trace=%s",
                master_id,
                filtered,
                len(rows),
                trace_id,
            )
            # DRF-968 — remember WHO the question is about, so the answer to
            # it can be an answer instead of a fresh search. Only under the
            # pilot flag: with it off nothing this master offers can be
            # grounded (see the block above), so there is no continuation to
            # park and a typed name is better served by the concierge.
            if flag_on:
                remember_pending_service(
                    global_bot_user,
                    tenant_id=tenant_id,
                    master_id=master_id,
                    master_name=master_name,
                    query_ref=query_ref,
                )
            return _ask_service_reply(
                tenant_id=tenant_id,
                master_id=master_id,
                master_name=master_name,
                rows=rows,
                truncated=truncated,
                not_offered_name=not_offered_name,
                filtered=filtered,
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

        # DRF-1101 — from here on the person is INSIDE the booking flow, and
        # the next thing they say may well be «16 августа 2026» rather than a
        # tap. Park who and what, so a typed answer reaches the same picker
        # the chip would have.
        remember_pending_schedule(
            global_bot_user,
            tenant_id=tenant_id,
            master_id=master_id,
            master_name=master_name,
            native_master_id=native_master_id,
            native_service_id=native_service_id,
        )

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

# Deterministic replies when a routed ``cb:book:*`` tap cannot reach tenant T
# at all. Mirrors the booking skill's split (DRF-1473): none of the three
# branches below is about time, so none of them says «устарел» any more. The
# tenant of a tap is resolved from the master id it carries, so «не нахожу
# мастера» is the literal truth in the first two, and each branch names itself
# in the journal.
_UNRESOLVED_BOOKING_CALLBACK_REPLY = (
    "Не нахожу этого мастера в каталоге — записаться по этой кнопке не получится. "
    "Выберите услугу заново."
)

# The skill ran but produced nothing to say. Never observed in the pilot; it
# exists so an empty reply can never reach the user as a blank message, and it
# is logged (it used to be the one silent branch on this path).
_EMPTY_BOOKING_CALLBACK_REPLY = (
    "Не получилось продолжить запись по этой кнопке. Выберите услугу заново."
)


def carry_time_preference(global_bot_user, conversation) -> None:
    """Copy the user's «завтра вечером» onto tenant T's conversation (DRF-1325).

    The person says WHEN on the global bot; the flow that has to honour it
    runs inside ``tenant_scope(T)`` against a different ``Conversation`` row.
    Without this copy the preference dies at the tenant boundary — a smaller
    version of the exact defect the ticket is about.

    Best-effort by contract: losing the preference costs the day chips, i.e.
    the ticket's own no-preference path, and must never cost the turn.
    """
    from apps.conversations.services import resolve_active_global_conversation
    from apps.orchestrator.time_preference import (
        load_time_preference,
        save_time_preference,
    )

    if conversation is None:
        return
    try:
        # The global conversation is parked under the ``global_bot`` sentinel
        # and is read through its own tenant-less resolver — NOT through
        # ``resolve_active_conversation``, which would look inside
        # ``tenant_scope(T)`` we are currently in and find nothing.
        # ``create_if_missing=False``: reading a hint must never create a row.
        source = resolve_active_global_conversation(global_bot_user, create_if_missing=False)
        pref = load_time_preference(source)
        if pref is not None:
            save_time_preference(conversation, pref)
    except Exception:  # noqa: BLE001 — a hint must never break a booking turn
        logger.exception("marketplace.handoff.time_pref_carry_failed")


# ─── Typed continuation of a live booking (DRF-968 / DRF-1101) ─────────────
#
# Everything above continues a booking on a TAP. This block is the other
# half: the person types instead, and until now that always started over.
# See :mod:`apps.orchestrator.booking_context` for the measurement.
#
# ### The rule both branches obey
#
# A turn is claimed only when this layer can account for it COMPLETELY — the
# same default DRF-1328 inverted for the deterministic fast path. A pending
# booking is not a licence to swallow whatever the person says next: «а
# сколько это стоит?» типed under a service question must still reach the
# concierge and its tools. So «service» claims a turn only when the text
# matches a service THIS master can actually deliver, and «schedule» only
# when it parses as a date or a part of the day. Everything else falls
# through, byte-for-byte as today.


def _global_conversation(global_bot_user):
    """The sentinel-scoped conversation this user's booking context lives on.

    Same resolver and the same ``create_if_missing=False`` as
    :func:`carry_time_preference`, and for the same reason: the caller may be
    inside ``tenant_scope(T)``, where the per-tenant resolver would look in
    the wrong place, and reading a hint must never create a row.
    """
    from apps.conversations.services import resolve_active_global_conversation

    try:
        return resolve_active_global_conversation(global_bot_user, create_if_missing=False)
    except Exception:  # noqa: BLE001 — a hint must never break a booking turn
        logger.exception("marketplace.handoff.global_conversation_failed")
        return None


def remember_pending_service(
    global_bot_user,
    *,
    tenant_id: uuid.UUID,
    master_id: uuid.UUID,
    master_name: str,
    query_ref: str,
) -> None:
    """Park «we asked THIS master's service question» (DRF-968)."""
    from apps.orchestrator.booking_context import (
        AWAITING_SERVICE,
        BookingContext,
        save_booking_context,
    )

    save_booking_context(
        _global_conversation(global_bot_user),
        BookingContext(
            awaiting=AWAITING_SERVICE,
            tenant_id=str(tenant_id),
            master_id=str(master_id),
            master_name=master_name,
            query_ref=query_ref,
        ),
    )


def remember_pending_schedule(
    global_bot_user,
    *,
    tenant_id: uuid.UUID,
    master_id: uuid.UUID,
    master_name: str,
    native_master_id: str,
    native_service_id: str,
) -> None:
    """Park «master and service are settled, we are on the WHEN» (DRF-1101)."""
    from apps.orchestrator.booking_context import (
        AWAITING_SCHEDULE,
        BookingContext,
        save_booking_context,
    )

    save_booking_context(
        _global_conversation(global_bot_user),
        BookingContext(
            awaiting=AWAITING_SCHEDULE,
            tenant_id=str(tenant_id),
            master_id=str(master_id),
            master_name=master_name,
            native_master_id=native_master_id,
            native_service_id=native_service_id,
        ),
    )


def forget_pending_booking(global_bot_user) -> None:
    """Drop the context — the booking reached its own end (confirm / cancel)."""
    from apps.orchestrator.booking_context import save_booking_context

    save_booking_context(_global_conversation(global_bot_user), None)


# How many matching rows are read before the menu is cut to the keyboard
# budget. The extra rows are never rendered — they exist so the residue check
# below sees every service the words could have meant, instead of deciding
# «this word means nothing here» from a truncated page.
_SERVICE_MATCH_SCAN_LIMIT = 50


def _groundable_service_rows(master, parsed) -> list[tuple[uuid.UUID, str, int]]:
    """This master's deliverable services matching ``parsed``, best first.

    The SAME three conditions and the SAME predicate the ask-the-service menu
    is built from (``handoff_to_booking``): active, offered by this master,
    non-NULL ``ayla_service_id``. Written once as a helper because the two
    lists must never disagree — a row this function returns is a row the menu
    would have shown, so a typed name and a tapped chip resolve identically.

    Third element is how many of the query's stems the name carries. A goal
    query reports ``0`` for every row, and that is the design rather than a
    gap: ``service_rows_score`` returns ``None`` for goals because carrying a
    goal is not a matter of degree, and the caller reads a score of nought as
    «offer the choice» — which is the right answer to «хочу расслабиться».

    ### Why the counting happens in Python

    ``name__icontains`` case-folds ASCII only on SQLite. «класси» therefore
    does NOT match «Классический массаж» locally, while on PostgreSQL — CI
    and the pilot — it does. A decision as sharp as «exactly one service
    carries every word, book it» must not depend on which database is
    underneath, so the SQL side keeps only the job it can do identically
    (selecting candidate rows through the SAME predicate the menu uses) and
    the counting is done over the names already in hand, with Python's
    Unicode-aware fold. No extra query, and no dialect in the verdict.

    Caller must already be inside ``tenant_scope(tenant)``.
    """
    from apps.catalog.models import CatalogService

    if parsed.is_empty:
        return []
    rows = (
        CatalogService.objects.filter(
            masters_offering__master=master,
            is_active=True,
            ayla_service_id__isnull=False,
        )
        .filter(service_rows_match_q(parsed))
        .order_by("name")
        .values_list("id", "name")[:_SERVICE_MATCH_SCAN_LIMIT]
    )
    scored = [
        (pk, name, sum(1 for stem in parsed.stems if stem in name.lower())) for pk, name in rows
    ]
    # Best first, ties by the stable name order the query already produced.
    scored.sort(key=lambda row: (-row[2], row[1]))
    return scored


def _unexplained_stems(stems, rows) -> list[str]:
    """Words the person typed that NO service of this master could explain.

    The same shape of test ``apps.orchestrator.fast_path`` applies before it
    claims a turn: a word this layer cannot account for is not noise, it is a
    refusal. «сколько», «стоит», «давай» carry the turn out of here and into
    the concierge, which is the layer that owns them.

    Case-folded against the names already read, so no extra query.
    """
    if not stems:
        return []
    names = [name.lower() for _, name, _ in rows]
    return [stem for stem in stems if not any(stem in name for name in names)]


def _continue_pending_service(
    *,
    global_bot_user,
    ctx,
    text: str,
    chat_id: str,
    trace_id: str | uuid.UUID | None,
) -> DiscoveryReply | None:
    """The answer to «напишите название услуги», consumed (DRF-968).

    ``None`` means «not mine», and it is the load-bearing half of this
    function. A pending question is not a licence to read every following
    turn as its answer:

    * **A word this master's roster cannot explain gives the turn back.**
      «а сколько это стоит?» shares a stem with nothing bookable, so it
      reaches the concierge and its tools — the same residue rule
      ``apps.orchestrator.fast_path`` uses to decide it is not the one to
      answer. Claiming it would trade the DRF-968 loop for a stickier one.
    * **A service this master does not offer gives the turn back too.** That
      is the live «Кавитация» turn from the DRF-962 acceptance, and the
      concierge answers it well (the masters who DO offer it). «У мастера X
      такого нет» plus a menu of what he does instead would be a narrower,
      worse reply.

    What is left is the answer the question asked for, and it resolves the
    way the catalog says: exactly one service carrying every word the person
    typed goes straight to booking; several — «классический массаж» is two
    rows in the pilot catalog (DRF-970) — come back as a choice narrowed to
    those, never as the roster and never as the master list.
    """
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.context import tenant_scope
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.filter(id=ctx.tenant_id).first()
    if tenant is None:
        return None

    parsed = parse_stems(query_stems(text))
    with tenant_scope(tenant):
        master = CatalogMaster.objects.filter(id=ctx.master_id).first()
        if master is None:
            return None
        rows = _groundable_service_rows(master, parsed)

    if not rows:
        logger.info(
            "marketplace.handoff.pending_service_no_match master=%s trace=%s",
            ctx.master_id,
            trace_id,
        )
        return None

    residue = _unexplained_stems(parsed.stems, rows)
    if residue:
        logger.info(
            "marketplace.handoff.pending_service_residue master=%s residue=%s trace=%s",
            ctx.master_id,
            ",".join(residue),
            trace_id,
        )
        return None

    exact = [row for row in rows if parsed.stems and row[2] == len(parsed.stems)]
    if len(exact) == 1:
        service_pk = exact[0][0]
        logger.info(
            "marketplace.handoff.pending_service_resolved master=%s service=%s trace=%s",
            ctx.master_id,
            service_pk,
            trace_id,
        )
        emit(
            "marketplace.handoff.typed_service_resolved",
            payload={
                "tenant_id": str(ctx.tenant_id),
                "master_id": str(ctx.master_id),
                "service_id": str(service_pk),
            },
        )
        # Re-enter the ONE entrypoint. Not a private shortcut into booking:
        # the service still has to pass the same tenant-scoped existence /
        # edge / deliverability checks a tapped chip passes, and a typed name
        # that survives them earns exactly the tap's outcome.
        return handoff_to_booking(
            global_bot_user=global_bot_user,
            tenant_id=uuid.UUID(ctx.tenant_id),
            master_id=uuid.UUID(ctx.master_id),
            service_id=service_pk,
            query_ref=ctx.query_ref,
            chat_id=chat_id,
            trace_id=trace_id,
        )

    # Not one. The person named a family, not a service. Ask again, with the
    # choice narrowed to what they just said instead of the roster — and with
    # the pending state re-armed under the NEW words, so the next answer is
    # read against the question that was actually asked.
    menu = exact or rows
    truncated = len(menu) > _ASK_SERVICE_BUTTON_LIMIT
    shown: list[tuple[uuid.UUID, str]] = [
        (pk, name) for pk, name, _ in menu[:_ASK_SERVICE_BUTTON_LIMIT]
    ]
    logger.info(
        "marketplace.handoff.pending_service_ambiguous master=%s rows=%d trace=%s",
        ctx.master_id,
        len(shown),
        trace_id,
    )
    remember_pending_service(
        global_bot_user,
        tenant_id=uuid.UUID(ctx.tenant_id),
        master_id=uuid.UUID(ctx.master_id),
        master_name=ctx.master_name,
        query_ref=encode_query_ref(text),
    )
    return _ask_service_reply(
        tenant_id=uuid.UUID(ctx.tenant_id),
        master_id=uuid.UUID(ctx.master_id),
        master_name=ctx.master_name,
        rows=shown,
        truncated=truncated,
        not_offered_name=None,
        filtered=True,
    )


# Cap on the verbatim fragment stored as «what you asked for». The spoken
# path stores matched words, so nothing upstream bounds a typed sentence.
_MAX_SAID_CHARS = 60

# The typo the DRF-1101 dialogue opens with, answered deterministically
# instead of by whatever the model feels like saying. It names the date back
# so the person can see WHICH date was read out of what they typed.
_PAST_DATE_LINE = "Дата {date} уже прошла."


def _continue_pending_schedule(
    *,
    global_bot_user,
    ctx,
    text: str,
    chat_id: str,
    trace_id: str | uuid.UUID | None,
) -> DiscoveryReply | None:
    """A typed day / time mid-booking, routed to the picker (DRF-1101).

    The whole continuation is: store the request the way a spoken one is
    stored (``save_time_preference``) and re-enter the master pick. From
    there nothing is new — ``_render_date_picker`` already honours a stored
    preference (DRF-1325), either jumping straight to that day's parts or
    saying in one line that the master has nothing on it and putting the
    chips back. No date is asserted free anywhere: the day list still comes
    from the schedule read, and ``create``'s 409 still owns the last word.

    ``None`` for a turn that names no time — it belongs to the concierge.
    """
    from apps.orchestrator.time_preference import (
        TimePreference,
        local_today,
        parse_explicit_date,
        parse_time_preference,
        save_time_preference,
    )
    from apps.tenancy.models import Tenant

    if not ctx.native_master_id or not ctx.native_service_id:
        return None
    tenant = Tenant.objects.filter(id=ctx.tenant_id).first()
    if tenant is None:
        return None

    today = local_today(tenant)
    spoken = parse_time_preference(text, weekday_today=today.weekday())
    explicit = parse_explicit_date(text, today=today)
    if explicit is None and spoken is None:
        return None

    prefix = ""
    if explicit is not None:
        if explicit < today:
            # A year typo, not a request. Say so and put the days back — the
            # ONE thing that must not happen is the turn ending up in
            # discovery, which is how this ticket's dialogue reached a reset.
            prefix = _PAST_DATE_LINE.format(date=explicit.strftime("%d.%m.%Y"))
            pref = spoken if (spoken is not None and spoken.day_offset is None) else None
        else:
            pref = TimePreference(
                day_offset=(explicit - today).days,
                part=spoken.part if spoken is not None else None,
                said=text.strip()[:_MAX_SAID_CHARS],
            )
    else:
        pref = spoken

    if pref is not None:
        save_time_preference(_global_conversation(global_bot_user), pref)

    logger.info(
        "marketplace.handoff.pending_schedule_continued master=%s past=%s trace=%s",
        ctx.master_id,
        bool(prefix),
        trace_id,
    )
    emit(
        "marketplace.handoff.typed_schedule_continued",
        payload={
            "tenant_id": str(ctx.tenant_id),
            "master_id": str(ctx.master_id),
            "past_date": bool(prefix),
        },
    )
    reply = route_booking_callback(
        global_bot_user=global_bot_user,
        callback_text=(
            f"{_CALLBACK_BOOK_PICK_MASTER}{ctx.native_master_id}:{ctx.native_service_id}"
        ),
        chat_id=chat_id,
        trace_id=trace_id,
    )
    if not prefix:
        return reply
    return DiscoveryReply(
        text=f"{prefix}\n\n{reply.text}",
        action_data=reply.action_data,
        persisted=reply.persisted,
    )


def try_continue_booking(
    *,
    global_bot_user,
    conversation,
    text: str,
    chat_id: str = "",
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply | None:
    """Continue a live booking from a TYPED turn, or ``None`` to stand aside.

    The single entrypoint the global MAX handler calls. Best-effort: a
    failure here degrades to the turn the pilot has today, which is the
    defect — bad, but never worse than losing the reply outright.
    """
    from apps.orchestrator.booking_context import (
        AWAITING_SCHEDULE,
        AWAITING_SERVICE,
        load_booking_context,
    )

    try:
        ctx = load_booking_context(conversation)
        if ctx is None:
            return None
        if ctx.awaiting == AWAITING_SERVICE:
            return _continue_pending_service(
                global_bot_user=global_bot_user,
                ctx=ctx,
                text=text,
                chat_id=chat_id,
                trace_id=trace_id,
            )
        if ctx.awaiting == AWAITING_SCHEDULE:
            return _continue_pending_schedule(
                global_bot_user=global_bot_user,
                ctx=ctx,
                text=text,
                chat_id=chat_id,
                trace_id=trace_id,
            )
    except Exception:  # noqa: BLE001 — a continuation must never cost the turn
        logger.exception("marketplace.handoff.continue_failed")
    return None


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

    # DRF-1101 — the typed-continuation window is measured from the last sign
    # of life, and a tap is one. confirm / cancel are the funnel's own ends:
    # past them a typed date means a NEW request, and continuing the finished
    # booking would be the stale-context dead-end wearing this fix's clothes.
    #
    # Both run BEFORE the tenant is resolved, because neither depends on it
    # and a confirm tap whose token no longer resolves is still a funnel that
    # ended.
    from apps.orchestrator.booking_context import touch_booking_context

    if callback_text.startswith(("cb:book:confirm:", "cb:book:cancel:")):
        forget_pending_booking(global_bot_user)
    else:
        touch_booking_context(_global_conversation(global_bot_user))

    tenant = _resolve_booking_callback_tenant(callback_text)
    if tenant is None:
        logger.info(
            "marketplace.booking_callback.refused reason=tenant_unresolved callback=%r trace=%s",
            callback_text[:60],
            trace_id,
        )
        return DiscoveryReply(text=_UNRESOLVED_BOOKING_CALLBACK_REPLY)

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
                "marketplace.booking_callback.refused reason=no_conversation tenant=%s trace=%s",
                tenant.id,
                trace_id,
            )
            return DiscoveryReply(text=_UNRESOLVED_BOOKING_CALLBACK_REPLY)

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

    reply_text = result.reply_text if result is not None else ""
    if not reply_text:
        logger.warning(
            "marketplace.booking_callback.refused reason=empty_skill_reply tenant=%s trace=%s",
            tenant.id,
            trace_id,
        )
        reply_text = _EMPTY_BOOKING_CALLBACK_REPLY
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
