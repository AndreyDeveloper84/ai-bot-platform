"""Booking confirmation to the **client** in chat (DRF-1066).

### The incident this closes (14.08)

A new customer walked the booking funnel **in the Mini App**, tapped
«записаться», and the success screen never rendered. Nothing anywhere
told them the booking had happened, so they tapped again — and ended up
with **two confirmed appointments with two different masters**
(verified against the backend; both later cancelled). Two slots burned
and a person who did not know what they were booked for.

The fix deliberately does **not** touch the Mini App screen. The bot is
already in the conversation with this person; a message «вы записаны —
услуга, мастер, дата и время» removes the uncertainty on *every*
surface at once (chat, Mini App, button) and does not depend on a
frontend render succeeding. Repairing the success screen is separate
work — it makes the Mini App nicer, it does not make the confirmation
reliable.

### Shape — mirrors :mod:`apps.booking.master_notify` (DRF-1030)

That module is the salon-facing half of the same event, merged 14.08.
This one is its client-facing twin and reuses its resolution helpers
(:func:`~apps.booking.master_notify.resolve_master`,
:func:`~apps.booking.master_notify.resolve_service_name`) and the
DRF-1029 fan-out primitive
(:func:`~apps.handoff.notify.send_max_notification`) rather than
inventing a second mechanism. Contract, unchanged from DRF-1030 §3:

* **After commit, never inside the transaction.** Registered through
  ``transaction.on_commit``; a rolled-back ingest must never tell
  somebody they are booked.
* **Best-effort, hard.** Nothing here may break event ingest — a dead
  messenger must degrade to *no message*, never to a dead-lettered
  booking event.
* **Never block the consumer.** One synchronous send with the DRF-1029
  timeout, no retries (DRF-989: the ingest consumer is single-threaded).

### Who gets the message

The client, and only the client: ``BotUser.chat_id`` for the
``BotUser`` the consumer already resolved from ``envelope.user_id``.
There is no addressing cascade here — unlike the salon notification
there is no legitimate substitute recipient. An unlinked (orphan
proxy) or chat-less ``BotUser`` is logged at INFO and skipped: it is
the normal state for a customer who books in the Ayla mobile app and
has never opened the bot, not a configuration defect.

### Not sending twice (the hard requirement)

A booking made **in the dialog** is already confirmed in the dialog:
``apps.skills.booking.tools.execute_confirm`` replies «Готово!
Записала. Мастер… Услуга… Время…» on the ✅ tap. Ayla then emits
``booking.created`` for that very appointment, which would land here a
moment later. A second «вы записаны» for one booking is exactly the
kind of noise that teaches people to ignore the channel — and this
ticket exists because of a *duplicate*, so producing duplicates of our
own would be a poor joke.

Two independent guards, both erring towards silence (per brief:
idempotency beats completeness — skipping is cheaper than doubling).
They answer **different** questions, and DRF-1069 exists because they
used to be conflated:

1. **«Has this appointment's client confirmation already been
   claimed?»** — ``RemoteBookingProxy.client_notified_at``, taken at
   the call site by
   ``apps.eventbus.consumers.booking._claim_announcement`` inside the
   ingest transaction. One ``UPDATE … WHERE client_notified_at IS
   NULL``, so exactly one caller ever wins: re-delivery under a fresh
   ``event_id`` finds it taken, a rolled-back ingest releases it, and
   the two call sites (``handle_booking_created`` /
   ``handle_booking_confirmed``) share one durable fact instead of each
   reasoning from its own view of the state machine.

   This guard used to be ``get_or_create``'s ``created`` flag — «we
   inserted the mirror row». That was never the same question, because
   this consumer is not the only writer of the mirror: the chat path
   writes it itself (``tools._upsert_remote_booking_proxy``) before
   Ayla's event arrives. The flag therefore answered «not new» for
   every dialog booking — which suppressed this message correctly by
   accident, and suppressed the *salon* message wrongly for months
   (DRF-1069). Nothing here may go back to reading the insert.

2. **«Did this booking come from our own dialog?»** — the chat-origin
   marker, :func:`was_confirmed_in_chat`, and this is the guard that
   actually owns the no-duplicate rule. ``execute_confirm`` stamps
   every booking it creates with ``comment = "Bot booking |
   yclients_record_id=<appointment id>"`` on
   :class:`~apps.booking.models.BookingRequest`, and writes that row
   *before* the proxy mirror. It is checked twice: at the call site
   (where a chat booking is skipped without even claiming the slot —
   the claim records what *this* channel sent, and the dialog reply is
   not this channel's) and again here in the ``on_commit`` callback,
   which sees the latest committed state. The second check is what
   holds when the mirror write loses its race with the event or fails
   outright — it is best-effort and swallows exceptions.

The event's own ``source`` field is deliberately **not** used as the
discriminator: the bot does not send a source when it creates through
the Ayla REST bridge (``apps.skills.booking.provider.create_record``),
so Ayla labels bot bookings with whatever it likes and
``RemoteBookingProxy.Source`` has no bot-specific value at all.
Local state is the only honest signal for «did we already say this».

### Two call sites, one message

``booking.created`` alone is not enough. With prepayment the
appointment is created ``awaiting_payment`` and only becomes real on
``booking.confirmed`` — telling someone «вы записаны» while payment is
outstanding would be a lie. So:

* ``handle_booking_created`` — announces only a booking that is born
  ``confirmed`` (the pilot's no-prepayment configuration, and the
  incident's own path).
* ``handle_booking_confirmed`` — announces the *transition into*
  confirmed, i.e. only when the proxy was NOT already confirmed.

The two are mutually exclusive by construction: a booking created
confirmed is announced by the first and is already ``CONFIRMED`` when
the second runs, so the second no-ops. Since DRF-1069 that reasoning is
no longer load-bearing — both call sites take the same
``client_notified_at`` claim, so even if the state machine ever let
both run, only one of them would send.

### PII

The client's own booking, told to the client themself. Service, master,
date and time in the tenant's timezone. Nothing about any other person
— and no appointment UUID, which is support-desk noise to a customer
(the salon-facing message carries it).
"""

from __future__ import annotations

import datetime as dt
import logging
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction

from apps.booking.master_notify import resolve_master, resolve_service_name
from apps.handoff.notify import send_max_notification
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Same placeholder the salon message uses: a booking is still worth
# confirming when one catalog row failed to mirror — the time alone
# already tells the customer the tap worked.
_UNKNOWN = "—"

_DEFAULT_TZ = "Europe/Moscow"

# Comment prefix stamped by ``apps.skills.booking.tools.execute_confirm``
# (and ``execute_reschedule``) on every ``BookingRequest`` the bot
# creates from the dialog. Under ``BOOKING_VIA_AYLA_REST`` the id in it
# is the canonical Ayla appointment UUID — the same value this module
# receives as ``appointment_id``.
_CHAT_BOOKING_COMMENT_PREFIX = "Bot booking | yclients_record_id="


def tenant_timezone(tenant: Tenant) -> ZoneInfo:
    """Tenant-local timezone, degrading to MSK and then UTC.

    A confirmation rendered in the wrong timezone is worse than none:
    the customer would arrive at the wrong hour, which is precisely the
    confusion this ticket is meant to end. An unusable tenant value
    therefore falls back to the pilot's real timezone, not to UTC.

    Deliberately a local copy of the salon message's private helper
    rather than an import of it: ``master_notify`` is merged and in
    production, and this ticket does not touch it.
    """

    for candidate in (getattr(tenant, "timezone", "") or "", _DEFAULT_TZ):
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return ZoneInfo("UTC")


def resolve_client_chat_id(bot_user: BotUser | None) -> str:
    """Channel chat id to answer in, or ``""`` when there is none."""

    return str(getattr(bot_user, "chat_id", "") or "").strip()


def was_confirmed_in_chat(*, tenant: Tenant, appointment_id: UUID) -> bool:
    """True when the bot itself booked this appointment in the dialog.

    Matched on the ``BookingRequest.comment`` marker that
    ``execute_confirm`` writes — the durable, appointment-keyed record
    that the customer has already been told «Готово! Записала.» in this
    very chat.

    Scoped by tenant only, NOT by ``bot_user``: ``(tenant,
    ayla_user_id)`` is not unique (one Ayla user may hold a ``BotUser``
    row per channel) and the consumer resolves the most recently active
    one, which need not be the row that made the chat booking. Matching
    on the appointment alone can only ever cause a *skip*, never a
    duplicate — the direction this guard is allowed to fail in.

    Case-insensitive because the UUID's hex casing is the producer's
    choice on both sides of the comparison.

    Any failure of the lookup itself answers «yes, already told»: with
    the DB refusing to confirm silence, staying silent is the cheaper
    mistake.
    """

    from apps.booking.models import BookingRequest

    prefix = f"{_CHAT_BOOKING_COMMENT_PREFIX}{appointment_id}"
    try:
        return BookingRequest.all_tenants.filter(
            tenant=tenant,
            comment__istartswith=prefix,
        ).exists()
    except Exception:  # noqa: BLE001 — fail towards silence, see docstring
        logger.exception(
            "booking.client_notify.chat_marker_lookup_failed appointment_id=%s",
            appointment_id,
        )
        return True


def build_booking_confirmation(
    *,
    tenant: Tenant,
    start_at: dt.datetime,
    service_name: str,
    master_name: str,
) -> str:
    """Format the client-facing «вы записаны» text.

    Answers the one question the broken success screen left open: *did
    it go through, and to what*. Service, master, date and time in the
    tenant's timezone — no other person's data, no internal ids.
    """

    when = start_at.astimezone(tenant_timezone(tenant)).strftime("%d.%m.%Y в %H:%M")
    lines = [
        "✅ Вы записаны",
        f"Услуга: {service_name or _UNKNOWN}",
        f"Мастер: {master_name or _UNKNOWN}",
        f"Когда: {when}",
    ]
    salon = (getattr(tenant, "name", "") or "").strip()
    if salon:
        lines.append(f"Салон: {salon}")
    return "\n".join(lines)


def notify_client_booking_confirmed(
    *,
    tenant: Tenant,
    bot_user: BotUser | None,
    appointment_id: UUID,
    start_at: dt.datetime,
    specialist_id: UUID | None,
    service_id: UUID | None,
) -> None:
    """``on_commit`` entry point. NEVER raises.

    Register through :func:`schedule_client_booking_confirmation`; do
    not call from inside the ingest transaction.
    """

    try:
        chat_id = resolve_client_chat_id(bot_user)
        if not chat_id:
            # Normal, not a defect: the customer books in the Ayla
            # mobile app / Mini App and has never opened the bot, so
            # there is no conversation to answer in. INFO, because a
            # WARNING here would fire on ordinary traffic and drown the
            # salon-side no_recipients warning that does mean something.
            logger.info(
                "booking.client_notify.no_chat tenant=%s appointment_id=%s",
                tenant.slug,
                appointment_id,
            )
            return

        if was_confirmed_in_chat(tenant=tenant, appointment_id=appointment_id):
            logger.info(
                "booking.client_notify.skipped_chat_origin tenant=%s appointment_id=%s "
                "— booked in the dialog, the customer already saw the confirmation there",
                tenant.slug,
                appointment_id,
            )
            return

        master = resolve_master(tenant=tenant, specialist_id=specialist_id)
        text = build_booking_confirmation(
            tenant=tenant,
            start_at=start_at,
            service_name=resolve_service_name(tenant=tenant, service_id=service_id),
            master_name=(getattr(master, "name", "") or "").strip() or _UNKNOWN,
        )

        failures = send_max_notification(text=text, chat_ids=(chat_id,))
        if failures == 0:
            logger.info(
                "booking.client_notify.sent tenant=%s appointment_id=%s",
                tenant.slug,
                appointment_id,
            )
        else:
            logger.warning(
                "booking.client_notify.send_failed tenant=%s appointment_id=%s",
                tenant.slug,
                appointment_id,
            )
    except Exception:  # noqa: BLE001 — hard containment; ingest must not break
        logger.exception(
            "booking.client_notify.unexpected appointment_id=%s",
            appointment_id,
        )


def schedule_client_booking_confirmation(
    *,
    tenant: Tenant,
    bot_user: BotUser | None,
    appointment_id: UUID,
    start_at: dt.datetime,
    specialist_id: UUID | None,
    service_id: UUID | None,
) -> None:
    """Queue the confirmation for after the ingest transaction commits.

    Every catalog read, every dedupe read and the send itself happen in
    the callback, so a rolled-back or re-delivered event costs the
    handler nothing and can never announce a booking that does not
    exist.
    """

    transaction.on_commit(
        lambda: notify_client_booking_confirmed(
            tenant=tenant,
            bot_user=bot_user,
            appointment_id=appointment_id,
            start_at=start_at,
            specialist_id=specialist_id,
            service_id=service_id,
        )
    )
