"""Booking.* event consumers (#442).

Per `docs/architecture/event-contract.md` §3.1–§3.4. Handlers for the
booking lifecycle:

* ``booking.created`` — upsert :class:`RemoteBookingProxy`, schedule
  T-24h and T-2h reminders, set ``Conversation.last_booking_at``,
  emit internal analytics event, (DRF-1030) announce the booking
  to the salon in MAX after commit — see
  :mod:`apps.booking.master_notify` for the addressing cascade — and
  (DRF-1066) confirm the booking to the CLIENT in chat, see
  :mod:`apps.booking.client_notify` for the anti-duplicate guards.
* ``booking.cancelled`` — flip proxy status to ``cancelled``, cancel
  pending reminders.
* ``booking.rescheduled`` (:func:`handle_booking_rescheduled`) —
  **temporary repo-local legacy compatibility contract**. Payload:
  ``new_start_at``/``old_start_at``/``rescheduled_by``. Update proxy
  ``start_at``/``end_at`` (preserving duration), re-peg reminders.
  Ordering is delivery-order + ``last_synced_event_id`` only — this
  contract carries no version.
* ``appointment.rescheduled`` (:func:`handle_appointment_rescheduled_canonical`)
  — **canonical cross-repo contract** (AYLA-DEC-0022, AYLA-DEC-0036).
  Different wire shape: DER ``version``/``previous_version``/
  ``revision_id``/``changed_fields``/``actor`` (required), optional
  ``starts_at``/``previous_starts_at``. NO ``new_start_at``/
  ``old_start_at``/``rescheduled_by``. Ordering is version-aware
  (``RemoteBookingProxy.last_applied_appointment_version``), not
  delivery order — see the handler docstring for the state machine.
  This is a SEPARATE handler from the legacy one; the two payload
  contracts are not interchangeable and must not be conflated.
* ``booking.completed`` — flip proxy status to ``completed``.

All handlers follow the canonical shape (see
``apps/eventbus/consumers/__init__.py``):

1. ``assert_envelope_tenant_authorized`` first — A3 mandate from PR
   #524 (tenant-spoof defense).
2. Side-effects inside the dispatcher's ``transaction.atomic``.
3. Idempotency at two layers: ``IngestDedupe`` (event_id) at the
   dispatcher + upsert-shaped writes here (``update_or_create`` on
   ``BookingReminder`` keyed by ``ayla_appointment_id`` + ``kind``).
4. PII rule §7 — never store free-text names/phones/emails locally;
   ``RemoteBookingProxy`` keeps IDs only.

### Handler registration

Module-level :func:`register` calls at the bottom — imported by
:mod:`apps.eventbus.apps.EventBusConfig.ready` so registry shape is
deterministic at every Django start.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final, Literal
from uuid import UUID

from django.utils import timezone

from apps.booking.client_notify import (
    schedule_client_booking_confirmation,
    was_confirmed_in_chat,
)
from apps.booking.master_notify import (
    CHAT_ORIGIN_SOURCE,
    schedule_booking_created_notification,
)
from apps.booking.models import BookingReminder, RemoteBookingProxy
from apps.conversations.models import Conversation
from apps.events.services import emit as emit_internal_event
from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


logger = logging.getLogger(__name__)


# Reminder schedule per event-contract.md §3.1 step 2: «typically 24h
# before start_at, 2h before start_at». Pair locks BookingReminder.Kind
# vocabulary to its offset.
_REMINDER_OFFSETS: Final[tuple[tuple[str, timedelta], ...]] = (
    (BookingReminder.Kind.DAY_BEFORE, timedelta(hours=24)),
    (BookingReminder.Kind.TWO_HOURS, timedelta(hours=2)),
)


# Booking lifecycle states that are advanced beyond the creation/bootstrap
# event. A stale ``booking.created`` (e.g. redelivery with a fresh event_id
# after the proxy has already been confirmed or cancelled) must NOT roll
# the proxy back to an earlier state. See #1147.
_CREATED_ADVANCED_STATUSES: Final[frozenset[str]] = frozenset(
    {
        RemoteBookingProxy.Status.CONFIRMED,
        RemoteBookingProxy.Status.CANCELLED,
        RemoteBookingProxy.Status.COMPLETED,
        RemoteBookingProxy.Status.NO_SHOW,
    }
)


# Proxy states in which a ``booking.created`` must announce NOTHING.
# A stale creation event arriving after the appointment was cancelled,
# completed or marked no-show would otherwise page the salon with «🆕
# Новая запись» about a booking that is over. CONFIRMED is deliberately
# NOT here — it is the state the *dialog* booking path writes, and
# announcing that one is the whole point of DRF-1069.
_ANNOUNCEMENT_BLOCKED_STATUSES: Final[frozenset[str]] = frozenset(
    {
        RemoteBookingProxy.Status.CANCELLED,
        RemoteBookingProxy.Status.COMPLETED,
        RemoteBookingProxy.Status.NO_SHOW,
    }
)


class UnknownBookingStatusError(ValueError):
    """``booking.created`` carried a status outside the closed enum."""


class BookingConfirmedPendingProxyError(ValueError):
    """``booking.confirmed`` arrived before the proxy exists."""


class BookingCancelledPendingProxyError(ValueError):
    """``booking.cancelled`` arrived before the proxy exists."""


def normalize_booking_created_status(raw_status: object) -> str:
    """Map producer booking.created status values to the BOT enum.

    Raises UnknownBookingStatusError for any value that is not a string
    or not one of the four contracted strings.
    """
    if not isinstance(raw_status, str):
        raise UnknownBookingStatusError(
            f"Unknown booking.created status type: {type(raw_status).__name__}"
        )

    mapping = {
        "awaiting_payment": RemoteBookingProxy.Status.PENDING_PAYMENT,
        "pending_payment": RemoteBookingProxy.Status.PENDING_PAYMENT,
        "confirmed": RemoteBookingProxy.Status.CONFIRMED,
        "tentative": RemoteBookingProxy.Status.TENTATIVE,
    }
    try:
        return mapping[raw_status]
    except KeyError as exc:
        raise UnknownBookingStatusError(f"Unknown booking.created status: {raw_status!r}") from exc


def _is_reminder_eligible(status: str) -> bool:
    """Reminders are only created for confirmed bookings."""
    return status == RemoteBookingProxy.Status.CONFIRMED


# ─── helpers ───────────────────────────────────────────────────────────────


def _parse_iso(value: str) -> dt.datetime:
    """Parse the envelope's ISO8601 timestamp into a tz-aware datetime.

    The envelope ``occurred_at`` is already validated by
    :mod:`apps.eventbus.ingest_envelope`, but the ``data.start_at`` /
    ``data.end_at`` fields are JSON strings — parse here.
    """
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _resolve_bot_user(*, user_id: UUID, tenant: Tenant) -> BotUser | None:
    """Look up the channel-side ``BotUser`` for an Ayla canonical user.

    Returns ``None`` when no BotUser is linked yet — e.g. customer
    has a profile in Ayla mobile but never opened the bot. Caller
    handles the None branch (proxy still written, reminders skipped).

    ### Why ``.order_by("-last_seen")`` (Round-5 F-Adv3)

    ``(tenant, ayla_user_id)`` is NOT unique: a single Ayla user can
    have multiple ``BotUser`` rows under one tenant (one per channel —
    MAX, Telegram, etc.). Without an explicit ordering Postgres
    returns an arbitrary row, which makes reminder routing
    non-deterministic. Pin to the most-recently-active channel — that
    matches user expectation ("send the reminder where I last opened
    the bot") and aligns with the model's default ``ordering =
    ["-last_seen"]`` (apps/identity/models.py:169).
    """
    return (
        BotUser.all_tenants.filter(tenant=tenant, ayla_user_id=user_id)
        .order_by("-last_seen")
        .first()
    )


def _assert_proxy_tenant(
    *,
    proxy: RemoteBookingProxy | None,
    expected_tenant: Tenant,
    envelope: IngestEnvelope,
) -> None:
    """Raise if the proxy's tenant doesn't match the envelope's tenant.

    Per Round-5 F-Adv1 (cross-tenant takeover, MUST_FIX_PRE_MERGE):
    ``appointment_id`` is a global Ayla UUID. Without this guard a
    malicious or compromised publisher could spoof an event with
    ``tenant_id=B`` + ``appointment_id=X`` (where X legitimately
    belongs to tenant A) — ``assert_envelope_tenant_authorized``
    would pass (B↔user is a real relationship) and the handler
    would mutate A's cached row.

    The guard fires AFTER the envelope-level tenant assertion: it
    catches the legitimate-tenant-spoofing-another-tenant's-PK case
    that envelope auth can't see.

    Raises ``TenantAuthorizationError`` so the event dead-letters
    instead of marking-processed silently — operator must see this.
    """
    if proxy is None:
        return
    if proxy.tenant_id != expected_tenant.id:
        from apps.eventbus.ingest_tenancy import TenantAuthorizationError

        logger.error(
            "eventbus.consumer.booking.cross_tenant_spoof_blocked "
            "appointment_id=%s envelope_tenant=%s proxy_tenant=%s event_id=%s",
            proxy.appointment_id,
            expected_tenant.id,
            proxy.tenant_id,
            envelope.event_id,
        )
        raise TenantAuthorizationError(
            f"appointment_id {proxy.appointment_id} belongs to tenant "
            f"{proxy.tenant_id}; envelope claims tenant {expected_tenant.id}"
        )


def _resolve_tenant(tenant_id: str | None) -> Tenant | None:
    if not tenant_id:
        return None
    return Tenant.objects.filter(id=tenant_id).first()


def _schedule_reminders(
    *,
    tenant: Tenant,
    bot_user: BotUser,
    appointment_id: UUID,
    start_at: dt.datetime,
) -> None:
    """Upsert T-24h and T-2h reminders for an Ayla booking.

    Idempotent via the partial unique constraint
    ``unique_ayla_booking_reminder(ayla_appointment_id, kind)``:
    re-delivery of ``booking.created`` rewrites the same two rows.

    ``status`` and ``scheduled_at`` live in ``create_defaults`` so a
    late ``booking.confirmed`` (different ``event_id``) does not reset
    an already-sent reminder back to ``PENDING``.

    New reminders are only created when their computed ``scheduled_at``
    is in the future. A late confirmation (or a reminder offset already
    passed) must not insert a PENDING row that the dispatcher would send
    immediately — see #1146.
    """
    chat_id = getattr(bot_user, "chat_id", "") or ""
    if not chat_id:
        logger.info(
            "eventbus.consumer.booking.skip_reminders_no_chat appointment_id=%s",
            appointment_id,
        )
        return

    now = timezone.now()
    for kind, offset in _REMINDER_OFFSETS:
        scheduled_at = start_at - offset
        if scheduled_at <= now:
            # #1146: do not create (or resurrect) a reminder whose send
            # time has already passed.
            logger.info(
                "eventbus.consumer.booking.skip_backdated_reminder "
                "appointment_id=%s kind=%s scheduled_at=%s now=%s",
                appointment_id,
                kind,
                scheduled_at.isoformat(),
                now.isoformat(),
            )
            continue

        # ``defaults`` are applied on UPDATE; ``create_defaults`` are
        # applied on INSERT. Keeping ``status``/``scheduled_at`` out of
        # ``defaults`` prevents a late/redelivered event from resurrecting
        # an already-sent reminder back to PENDING.
        common_defaults = {
            "bot_user": bot_user,
            # F-Fri1: write NULL (not "") so the legacy
            # unique_together (yclients_record_id, kind) doesn't
            # collide across multiple Ayla appointments.
            "yclients_record_id": None,
            "chat_id": chat_id,
            "visit_at": start_at,
            # Names are looked up via the catalog mirror on send.
            "master_name": "",
            "service_name": "",
        }
        BookingReminder.all_tenants.update_or_create(
            ayla_appointment_id=appointment_id,
            tenant=tenant,  # F-Adv2: tenant scopes the partial unique key
            kind=kind,
            defaults=common_defaults,
            create_defaults={
                **common_defaults,
                "status": BookingReminder.Status.PENDING,
                "scheduled_at": scheduled_at,
            },
        )


def _cancel_reminders(*, appointment_id: UUID) -> None:
    """Cancel all PENDING reminders for the appointment. Idempotent.

    Per §3.2 step 2: «cancelling already-cancelled reminders MUST NOT
    error». ORM update on a filter is naturally idempotent — 0 rows
    matched is not an error.
    """
    BookingReminder.all_tenants.filter(
        ayla_appointment_id=appointment_id,
        status=BookingReminder.Status.PENDING,
    ).update(status=BookingReminder.Status.CANCELLED)


def _reschedule_reminders(
    *,
    appointment_id: UUID,
    new_start_at: dt.datetime,
) -> None:
    """Re-peg PENDING reminders to a new ``start_at``. Idempotent."""
    for kind, offset in _REMINDER_OFFSETS:
        BookingReminder.all_tenants.filter(
            ayla_appointment_id=appointment_id,
            kind=kind,
            status=BookingReminder.Status.PENDING,
        ).update(
            visit_at=new_start_at,
            scheduled_at=new_start_at - offset,
        )


def _touch_conversation_last_booking(
    *,
    bot_user: BotUser,
    tenant: Tenant,
    last_booking_at: dt.datetime,
) -> None:
    """Ensure a ``Conversation`` row exists for ``(bot_user, tenant)`` and
    set its ``last_booking_at``. Per event-contract.md §3.1 step 4.

    Idempotent via ``update_or_create``: re-delivery refreshes the
    timestamp to the latest event's value (always safe — same value
    on retry, newer value on legitimate reschedule).
    """
    Conversation.all_tenants.update_or_create(
        tenant=tenant,
        bot_user=bot_user,
        defaults={"last_booking_at": last_booking_at},
    )


# ─── announcements (DRF-1030 / DRF-1066 / DRF-1069) ────────────────────────


_AnnouncementSlot = Literal["salon_notified_at", "client_notified_at"]


def _claim_announcement(*, appointment_id: UUID, slot: _AnnouncementSlot) -> bool:
    """Take the one-shot announcement slot for this appointment.

    ``UPDATE … SET <slot> = now() WHERE appointment_id = … AND <slot>
    IS NULL`` — the claim and the test are one statement, so it is
    atomic against a concurrent delivery of the same appointment
    (the second transaction blocks on the row lock and then matches
    zero rows). Returns True to exactly one caller, ever.

    ### Why this replaces the ``created`` flag (DRF-1069)

    Until this landed, both announcements were gated on
    ``get_or_create``'s ``created`` — «we inserted the mirror row», read
    as «this appointment is new to us». The two are not the same
    statement, because this handler is **not** the only writer of
    :class:`~apps.booking.models.RemoteBookingProxy`: the conversational
    booking path writes the mirror itself
    (``apps.skills.booking.tools._upsert_remote_booking_proxy``, inside
    ``execute_confirm``) *before* Ayla's event reaches us. So every
    booking made in the dialog — the product's main path — arrived here
    as an update, and **the salon was never told about it** (verified on
    the pilot 14.08: bookings that hour, salon notifications zero).

    The claim says only what it means: «has anyone announced this
    appointment yet». It is insert-agnostic, so a row someone else
    wrote is announced exactly once; it is durable, so re-delivery under
    a fresh ``event_id`` finds it taken; and it is transactional, so a
    rolled-back ingest releases it and announces nothing.

    Separating *that* question from «did this booking come from our own
    chat» — the other meaning ``created`` was silently carrying — leaves
    the second one to an explicit origin marker,
    :func:`~apps.booking.client_notify.was_confirmed_in_chat`, which is
    what the client-facing side actually needs.
    """

    return (
        RemoteBookingProxy.all_tenants.filter(
            appointment_id=appointment_id,
            **{f"{slot}__isnull": True},
        ).update(**{slot: timezone.now()})
        == 1
    )


def _announce_booking_created(
    *,
    tenant: Tenant,
    bot_user: BotUser | None,
    appointment_id: UUID,
    start_at: dt.datetime,
    specialist_id: UUID | None,
    service_id: UUID | None,
    raw_source: str,
    status: str,
) -> None:
    """Queue the after-commit announcements for a newly-seen booking.

    Two audiences, two independent questions:

    * **The salon** (DRF-1030) is told about *every* new booking,
      whatever surface made it — that is the DRF-1069 fix. The only gate
      is the claim: one appointment, one announcement.
    * **The client** (DRF-1066) is told only when the booking is already
      ``confirmed`` (with prepayment «вы записаны» would be untrue until
      :func:`handle_booking_confirmed`) **and** the booking did not come
      from the dialog, where ``execute_confirm`` has already replied
      «Готово! Записала.». That second condition is now read from the
      explicit chat-origin marker instead of being inferred from the
      insert. :mod:`apps.booking.client_notify` re-checks it inside the
      callback as well — defence in depth, and it sees the latest
      committed state.

    Chat origin also fixes the salon message's own «Источник:» line: the
    event cannot carry it (the bot does not pass a ``source`` through
    ``provider.create_record``, and ``RemoteBookingProxy.Source`` has no
    bot value at all), so local knowledge substitutes it.

    Nothing here sends anything — both callees register
    ``transaction.on_commit`` callbacks.
    """

    if status in _ANNOUNCEMENT_BLOCKED_STATUSES:
        # A stale creation event for an appointment that is already
        # over. Announcing «новая запись» here would be worse than
        # silence, and the claim is left free on purpose: nothing was
        # announced, so nothing is recorded as announced.
        logger.info(
            "eventbus.consumer.booking.created.announcement_skipped_terminal "
            "appointment_id=%s status=%s",
            appointment_id,
            status,
        )
        return

    chat_origin = was_confirmed_in_chat(tenant=tenant, appointment_id=appointment_id)

    if _claim_announcement(appointment_id=appointment_id, slot="salon_notified_at"):
        schedule_booking_created_notification(
            tenant=tenant,
            appointment_id=appointment_id,
            start_at=start_at,
            specialist_id=specialist_id,
            service_id=service_id,
            raw_source=CHAT_ORIGIN_SOURCE if chat_origin else raw_source,
        )

    if chat_origin:
        logger.info(
            "eventbus.consumer.booking.created.client_confirmation_skipped_chat_origin "
            "appointment_id=%s — booked in the dialog, execute_confirm already replied there",
            appointment_id,
        )
        return

    if status != RemoteBookingProxy.Status.CONFIRMED:
        return

    if _claim_announcement(appointment_id=appointment_id, slot="client_notified_at"):
        schedule_client_booking_confirmation(
            tenant=tenant,
            bot_user=bot_user,
            appointment_id=appointment_id,
            start_at=start_at,
            specialist_id=specialist_id,
            service_id=service_id,
        )


# ─── handlers ──────────────────────────────────────────────────────────────


def handle_booking_created(envelope: IngestEnvelope) -> None:
    """``booking.created`` — event-contract.md §3.1.

    Steps:
      1. Verify tenant authorization (A3 mandate).
      2. Upsert RemoteBookingProxy keyed by appointment_id.
      3. Schedule T-24h + T-2h reminders (idempotent).
      4. Emit internal ``booking_created`` event for analytics fan-out.
      5. Set Conversation.last_booking_at = data.start_at.
      6. DRF-1030 / DRF-1069 — queue the salon-facing MAX notification
         for after commit, once per appointment, **whatever surface
         created it** — including the bot's own dialog, whose mirror
         write used to make this handler treat the booking as already
         known and stay silent.
      7. DRF-1066 — queue the client-facing «вы записаны» confirmation
         for after commit, for an appointment that is already
         ``confirmed`` and did NOT come from the dialog (there the
         customer has already been answered). Awaiting-payment bookings
         are announced on the transition instead
         (:func:`handle_booking_confirmed`).

    Steps 6–7 live in :func:`_announce_booking_created` and are reached
    from BOTH exits of the proxy upsert — including the
    advanced-state no-op, which is precisely where a dialog booking
    lands.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    appointment_id = UUID(data["appointment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.booking.created.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    start_at = _parse_iso(data["start_at"])
    end_at = _parse_iso(data["end_at"])

    # Validate/normalize the producer status before doing any lookup work.
    # An unknown status raises early, leaving no side-effects behind.
    raw_status = data.get("status")
    normalized_status = normalize_booking_created_status(raw_status)

    if raw_status != normalized_status:
        logger.info(
            "eventbus.consumer.booking.created.status_normalized "
            "event_id=%s appointment_id=%s tenant_id=%s raw_status=%s normalized_status=%s",
            envelope.event_id,
            data.get("appointment_id"),
            envelope.tenant_id,
            raw_status,
            normalized_status,
        )

    # Resolve channel-side BotUser so we know whether the proxy is
    # linked or orphan before the upsert fires.
    bot_user = _resolve_bot_user(user_id=UUID(envelope.user_id), tenant=tenant)

    service_uuid = UUID(data["service_id"]) if data.get("service_id") else None
    specialist_uuid = UUID(data["specialist_id"]) if data.get("specialist_id") else None
    raw_source = data.get("source", "")

    create_defaults = {
        "tenant": tenant,
        "bot_user": bot_user,  # may be None — orphan proxy
        "start_at": start_at,
        "end_at": end_at,
        "status": normalized_status,
        "source": raw_source,
        "service_id": service_uuid,
        "specialist_id": specialist_uuid,
        "last_synced_event_id": envelope.event_id,
    }

    # Round-6 Path B: delegate the race-safe INSERT-or-GET to Django.
    # ``get_or_create`` internally wraps the INSERT in a savepoint and
    # re-SELECTs on IntegrityError — closes N-Adv1 (TOCTOU race) and
    # N-Adv6-1 (broad except) by NOT writing manual transaction code.
    # Non-PK IntegrityErrors (FK/NOT-NULL/CHECK) propagate because
    # Django re-raises if re-SELECT finds nothing. See
    # ``django.db.models.query.QuerySet.get_or_create`` (Django ≥4).
    proxy, created = RemoteBookingProxy.all_tenants.get_or_create(
        appointment_id=appointment_id,
        defaults=create_defaults,
    )

    if not created:
        # Existing row. N-Adv2 tenant guard FIRST, then short-circuit,
        # then update. ``tenant`` is deliberately excluded from update
        # fields so the first-writer's tenant_id is immutable — a
        # spoofer with a stale event_id can't rewrite ownership.
        _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)
        if proxy.last_synced_event_id == envelope.event_id:
            logger.info(
                "eventbus.consumer.booking.created.replay_skipped appointment_id=%s event_id=%s",
                appointment_id,
                envelope.event_id,
            )
            return
        if proxy.status in _CREATED_ADVANCED_STATUSES:
            # #1147: a stale ``booking.created`` must not downgrade a
            # proxy that has already moved past creation state. Leave
            # ``last_synced_event_id`` untouched so the more advanced
            # event stays authoritative for forensic ordering.
            logger.info(
                "eventbus.consumer.booking.created.advanced_state_noop "
                "appointment_id=%s current_status=%s event_id=%s",
                appointment_id,
                proxy.status,
                envelope.event_id,
            )
            # DRF-1069: «do not roll the state back» is not «say
            # nothing». The overwhelmingly common way to reach this
            # branch is NOT a stale event at all — it is a booking made
            # in the dialog: ``execute_confirm`` writes the mirror row
            # CONFIRMED before Ayla's event arrives, so the appointment
            # is already in an «advanced» state the first time we ever
            # see an event for it. Returning here is why the salon
            # learned about none of them. The announcement decides for
            # itself (terminal states say nothing; the claim keeps it to
            # one) — everything else about this no-op is unchanged.
            _announce_booking_created(
                tenant=tenant,
                bot_user=bot_user,
                appointment_id=appointment_id,
                start_at=start_at,
                specialist_id=specialist_uuid,
                service_id=service_uuid,
                raw_source=raw_source,
                status=proxy.status,
            )
            return
        update_fields = {k: v for k, v in create_defaults.items() if k != "tenant"}
        RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).update(**update_fields)

    if bot_user is not None:
        if _is_reminder_eligible(normalized_status):
            _schedule_reminders(
                tenant=tenant,
                bot_user=bot_user,
                appointment_id=appointment_id,
                start_at=start_at,
            )
        _touch_conversation_last_booking(
            bot_user=bot_user,
            tenant=tenant,
            last_booking_at=start_at,
        )
    else:
        logger.info(
            "eventbus.consumer.booking.created.orphan_proxy appointment_id=%s status=%s",
            appointment_id,
            normalized_status,
        )

    # Analytics fan-out — apps/events/ snake_case bus (§3.1 step 3).
    emit_internal_event(
        "booking_created",
        properties={
            "appointment_id": str(appointment_id),
            "status": normalized_status,
            "source": raw_source,
            "start_at": data["start_at"],
        },
    )

    # DRF-1030 (salon) + DRF-1066 (client) — queue both announcements
    # for after commit. NOT gated on ``created``: that flag answers «did
    # WE insert the row», which stopped being the same question as «is
    # this appointment new to us» the moment the dialog path started
    # writing the mirror itself. See :func:`_claim_announcement`.
    _announce_booking_created(
        tenant=tenant,
        bot_user=bot_user,
        appointment_id=appointment_id,
        start_at=start_at,
        specialist_id=specialist_uuid,
        service_id=service_uuid,
        raw_source=raw_source,
        status=normalized_status,
    )


def handle_booking_cancelled(envelope: IngestEnvelope) -> None:
    """``booking.cancelled`` — event-contract.md §3.2.

    Steps:
      1. Verify tenant authorization.
      2. Lock the :class:`RemoteBookingProxy` row and update
         ``status = cancelled`` plus ``last_synced_event_id`` via
         ``proxy.save()`` so ``synced_at`` (``auto_now``) advances.
      3. Cancel all PENDING reminders for this appointment.

    Idempotency: if the proxy's ``last_synced_event_id`` already matches
    ``envelope.event_id`` the handler returns early (no state change).

    Missing proxy: raises :class:`BookingCancelledPendingProxyError`, a
    retryable failure — the dispatcher rolls back and Ayla will redeliver
    until ``booking.created`` creates the proxy (or the retry threshold
    sends the event to the DLQ). This is the terminal handler path for a
    cancellation that arrives out-of-order; there is no silent no-op.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    appointment_id = UUID(data["appointment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.booking.cancelled.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    proxy = (
        RemoteBookingProxy.all_tenants.select_for_update()
        .filter(appointment_id=appointment_id)
        .first()
    )

    # N-Adv2: tenant guard FIRST, BEFORE the idempotency short-circuit.
    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    # Defence-in-depth idempotency short-circuit.
    if proxy is not None and proxy.last_synced_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.booking.cancelled.replay_skipped appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    if proxy is None:
        logger.warning(
            "eventbus.consumer.booking.cancelled.pending_proxy "
            "appointment_id=%s event_id=%s tenant_id=%s",
            appointment_id,
            envelope.event_id,
            tenant.id,
        )
        raise BookingCancelledPendingProxyError(
            f"appointment {appointment_id}: no RemoteBookingProxy yet for booking.cancelled "
            f"(event_id={envelope.event_id})"
        )

    proxy.status = RemoteBookingProxy.Status.CANCELLED
    proxy.last_synced_event_id = envelope.event_id
    proxy.save(update_fields=["status", "last_synced_event_id", "synced_at"])

    _cancel_reminders(appointment_id=appointment_id)

    emit_internal_event(
        "booking_cancelled",
        properties={
            "appointment_id": str(appointment_id),
            "cancelled_by": data.get("cancelled_by", ""),
            "reason_code": data.get("reason_code") or "",
        },
    )


def handle_booking_rescheduled(envelope: IngestEnvelope) -> None:
    """``booking.rescheduled`` — event-contract.md §3.3.

    Per §3.3 step 1: «Update RemoteBookingProxy.start_at to
    data.new_start_at. Update end_at by preserving the original
    duration (new_end_at = new_start_at + (old_end_at - old_start_at))».
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    appointment_id = UUID(data["appointment_id"])
    new_start_at = _parse_iso(data["new_start_at"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        return

    proxy = RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).first()

    # N-Adv2: tenant guard FIRST, BEFORE the idempotency short-circuit.
    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    # Defence-in-depth idempotency short-circuit.
    if proxy is not None and proxy.last_synced_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.booking.rescheduled.replay_skipped appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    if proxy is None:
        # Out-of-order: rescheduled before created. Do NOT write a
        # stub here — without start_at/end_at from a created event we
        # can't compute duration, and a placeholder would silently
        # corrupt the reschedule arithmetic when created arrives. The
        # later-arriving created event will write the schedule fresh.
        logger.info(
            "eventbus.consumer.booking.rescheduled.no_proxy appointment_id=%s",
            appointment_id,
        )
        return

    # F-Adv4: refuse to compute duration off corrupted state. The
    # out-of-order cancel-stub writes start_at == end_at as a
    # placeholder; rescheduling against that would yield a
    # zero-duration appointment + leak the bogus arithmetic into
    # reminder math. A negative duration (end_at < start_at) is
    # never legitimate.
    if proxy.end_at <= proxy.start_at:
        logger.error(
            "eventbus.consumer.booking.rescheduled.corrupted_proxy "
            "appointment_id=%s start_at=%s end_at=%s event_id=%s",
            appointment_id,
            proxy.start_at.isoformat(),
            proxy.end_at.isoformat(),
            envelope.event_id,
        )
        raise ValueError(
            f"RemoteBookingProxy {appointment_id} has non-positive duration "
            f"({proxy.start_at} → {proxy.end_at}); refusing reschedule"
        )

    # Preserve duration per §3.3.
    original_duration = proxy.end_at - proxy.start_at
    new_end_at = new_start_at + original_duration

    RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).update(
        start_at=new_start_at,
        end_at=new_end_at,
        last_synced_event_id=envelope.event_id,
    )

    _reschedule_reminders(appointment_id=appointment_id, new_start_at=new_start_at)

    # Conversation context update (§3.3 step 3) — refresh
    # last_booking_at to the new time so AI references the
    # rescheduled slot.
    bot_user = _resolve_bot_user(user_id=UUID(envelope.user_id), tenant=tenant)
    if bot_user is not None:
        _touch_conversation_last_booking(
            bot_user=bot_user,
            tenant=tenant,
            last_booking_at=new_start_at,
        )

    emit_internal_event(
        "booking_rescheduled",
        properties={
            "appointment_id": str(appointment_id),
            "old_start_at": data.get("old_start_at", ""),
            "new_start_at": data["new_start_at"],
            "rescheduled_by": data.get("rescheduled_by", ""),
        },
    )


# ─── canonical appointment.rescheduled (AYLA-DEC-0022, AYLA-DEC-0036) ──────


class CanonicalReschedulePayloadError(ValueError):
    """``appointment.rescheduled`` DER payload fails required-field or
    shape validation.

    A *controlled* failure — raised deliberately instead of letting a
    missing/malformed field surface as a raw ``KeyError``/``TypeError``.
    Propagates to the dispatcher's handler-exception path like any
    other handler error (HANDLER_EXCEPTION → Ayla retry → DLQ on
    threshold, #433) — no new dispatcher outcome is introduced.
    """


class CanonicalRescheduleVersionGapError(RuntimeError):
    """A canonical ``appointment.rescheduled`` event's ``previous_version``
    does not chain from the locally-applied version.

    Raised — never silently dropped — so the existing HANDLER_EXCEPTION
    → Ayla-retry → DLQ-on-threshold mechanism (#433,
    ``HandlerFailureTracker``) makes the gap observable to operators
    instead of leaving the proxy silently un-updated.
    """


class CanonicalReschedulePendingProxyError(RuntimeError):
    """A canonical ``appointment.rescheduled`` event arrived before this
    appointment's :class:`RemoteBookingProxy` exists (``booking.created``
    hasn't landed yet).

    Raised — NEVER acknowledged as a silent no-op — per P1 review
    finding on the first cut of this handler (review of commit
    26bc616): a plain ``return`` here lets the dispatcher commit an
    ``IngestDedupe`` row for this ``event_id`` as processed. If
    ``booking.created`` later creates the proxy at ITS OWN (pre-
    reschedule) ``start_at``, the reschedule is lost forever — Ayla
    believes the event already succeeded and will never re-deliver it.
    Raising routes this through the existing HANDLER_EXCEPTION → Ayla
    retry (§6.3) → DLQ-on-threshold path instead: no ``IngestDedupe``
    row is committed (the whole handler transaction rolls back), so
    once ``booking.created`` lands, Ayla's retry of the SAME event_id
    is not a duplicate and applies cleanly. This reuses the existing
    retry/DLQ mechanism rather than adding a durable pending-projection
    store.
    """


# DER-required fields per AGENT_BOT_FIX_CANONICAL_RESCHEDULE_CONSUMER
# review (P1 fix for commit 26bc616). ``starts_at``/``previous_starts_at``
# are optional — a canonical event MAY only touch non-schedule fields.
_CANONICAL_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "appointment_id",
    "version",
    "previous_version",
    "revision_id",
    "changed_fields",
    "actor",
)


@dataclass(frozen=True)
class _CanonicalRescheduleData:
    """Parsed + validated ``appointment.rescheduled`` DER payload."""

    appointment_id: UUID
    version: int
    previous_version: int
    revision_id: str
    changed_fields: tuple[str, ...]
    actor: Any
    starts_at: dt.datetime | None
    previous_starts_at: dt.datetime | None


def _parse_optional_iso(value: Any, *, field_name: str) -> dt.datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CanonicalReschedulePayloadError(
            f"{field_name} must be an ISO8601 string, got {type(value).__name__}"
        )
    try:
        return _parse_iso(value)
    except ValueError as exc:
        raise CanonicalReschedulePayloadError(f"invalid {field_name}: {value!r}") from exc


def _parse_canonical_reschedule_data(data: dict[str, Any]) -> _CanonicalRescheduleData:
    """Validate + parse the ``appointment.rescheduled`` DER payload.

    Distinct from the legacy ``booking.rescheduled`` shape — does NOT
    require (or read) ``new_start_at``/``old_start_at``/``rescheduled_by``.

    ``actor``'s wire shape is owned by the Ayla-side Domain Event
    Registry (not repo-local); only presence is validated here pending
    Phase 2 backend contract confirmation (see the Phase 2 dependency
    checklist in AGENT_BOT_PHASE1_FINAL_REVIEW_RESULT.md §5) — it is
    carried through for logging/analytics, not type-narrowed.
    """
    missing = [f for f in _CANONICAL_REQUIRED_FIELDS if data.get(f) in (None, "")]
    if missing:
        raise CanonicalReschedulePayloadError(
            f"appointment.rescheduled missing required DER field(s): {', '.join(missing)}"
        )

    try:
        appointment_id = UUID(str(data["appointment_id"]))
    except (ValueError, AttributeError, TypeError) as exc:
        raise CanonicalReschedulePayloadError(
            f"invalid appointment_id: {data['appointment_id']!r}"
        ) from exc

    version = data["version"]
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise CanonicalReschedulePayloadError(f"invalid version: {version!r}")

    previous_version = data["previous_version"]
    if (
        not isinstance(previous_version, int)
        or isinstance(previous_version, bool)
        or previous_version < 0
    ):
        raise CanonicalReschedulePayloadError(f"invalid previous_version: {previous_version!r}")

    revision_id = data["revision_id"]
    if not isinstance(revision_id, str):
        raise CanonicalReschedulePayloadError(f"invalid revision_id: {revision_id!r}")

    changed_fields = data["changed_fields"]
    if not isinstance(changed_fields, list) or not all(isinstance(f, str) for f in changed_fields):
        raise CanonicalReschedulePayloadError(
            f"changed_fields must be a list of strings, got {changed_fields!r}"
        )

    return _CanonicalRescheduleData(
        appointment_id=appointment_id,
        version=version,
        previous_version=previous_version,
        revision_id=revision_id,
        changed_fields=tuple(changed_fields),
        actor=data["actor"],
        starts_at=_parse_optional_iso(data.get("starts_at"), field_name="starts_at"),
        previous_starts_at=_parse_optional_iso(
            data.get("previous_starts_at"), field_name="previous_starts_at"
        ),
    )


def handle_appointment_rescheduled_canonical(envelope: IngestEnvelope) -> None:
    """``appointment.rescheduled`` — canonical cross-repo DER contract
    (AYLA-DEC-0022, AYLA-DEC-0036).

    SEPARATE from the legacy :func:`handle_booking_rescheduled` — different
    wire payload, different ordering discipline. Do not conflate the two.

    ### No proxy yet

    If :class:`RemoteBookingProxy` doesn't exist for this
    ``appointment_id`` (canonical reschedule arrived before
    ``booking.created``), this handler raises
    :class:`CanonicalReschedulePendingProxyError` rather than
    returning normally — a silent ``OK`` here would let the dispatcher
    commit the ``IngestDedupe`` row and permanently swallow the
    reschedule (P1 review finding on the first cut of this handler,
    commit 26bc616): ``booking.created`` would later create the proxy
    at its OWN pre-reschedule ``start_at``, and Ayla would never
    re-deliver an event it believes already succeeded. Raising keeps
    the event un-dedupe'd so Ayla's retry (§6.3) — same or later
    event_id — succeeds once the proxy exists, same
    retry/DLQ-on-threshold mechanism as the version-gap case above.

    ### Row locking (concurrent delivery safety)

    The proxy row is fetched with ``select_for_update()`` inside the
    dispatcher's ``transaction.atomic()`` block (P1 review finding:
    an unlocked read + a separate later UPDATE let two concurrent
    deliveries — different ``event_id``s, e.g. redelivery races or
    genuine concurrent transport — both read the same
    ``last_applied_appointment_version`` and both apply, defeating the
    version check). The lock serializes concurrent handler executions
    for the SAME ``appointment_id``: a second delivery blocks until
    the first commits, then re-reads the POST-commit
    ``last_applied_appointment_version`` before deciding skip / apply
    / gap. This is a no-op on the SQLite test backend (no row-level
    locking) — see ``TestConcurrentCanonicalDelivery`` for the
    Postgres-only concurrency assertion.

    ### Version-aware ordering state machine

    Compared against ``RemoteBookingProxy.last_applied_appointment_version``
    (``last_applied`` below):

    * ``last_applied is None`` — **bootstrap**. No canonical event has
      ever been applied to this proxy (e.g. it exists only from legacy
      ``booking.created``/``booking.rescheduled`` writes, which never
      set this field). There is no local canonical history to check
      continuity against, so the event is accepted unconditionally and
      seeds the baseline from its ``version`` — regardless of what
      ``previous_version`` claims.
    * ``version <= last_applied`` — idempotent skip. Covers exact
      duplicate delivery (``version == last_applied``) AND stale/
      out-of-order delivery of an older version. No state change.
    * ``previous_version == last_applied`` — contiguous chain, apply.
    * anything else (``previous_version`` does not equal
      ``last_applied`` — including the gap case
      ``previous_version > last_applied``) — NOT applied silently.
      Raises :class:`CanonicalRescheduleVersionGapError`, which the
      dispatcher turns into ``HANDLER_EXCEPTION``: Ayla retries per
      §6.3, and after ``EVENTBUS_HANDLER_EXCEPTION_DLQ_THRESHOLD``
      attempts the existing ``HandlerFailureTracker``/DLQ mechanism
      (#433) surfaces it for operator triage. This reuses the
      project's existing retry/reconciliation/DLQ path — no new
      infrastructure.

    ``event_id``-based dedupe (``IngestDedupe`` at the dispatcher) is
    NOT sufficient stale-ordering protection on its own — two
    DIFFERENT ``event_id``s can carry the same or an out-of-order
    ``version`` (redelivery with a fresh id, or genuine out-of-order
    transport) — hence this version check runs independently of it.

    ### Optional ``starts_at``

    A canonical event MAY only touch non-schedule fields (per
    ``changed_fields`` — e.g. a master reassignment). Repo policy:
    this is a legitimate DER event, not an error.

    * ``starts_at`` present — update proxy ``start_at``/``end_at``
      (preserving duration, same arithmetic as the legacy handler),
      re-peg reminders idempotently, refresh
      ``Conversation.last_booking_at``.
    * ``starts_at`` absent — proxy schedule, reminders, and
      ``Conversation.last_booking_at`` are left untouched. The version
      transition still applies (``last_applied_appointment_version``
      advances) — a schedule-less canonical event is marked processed,
      not routed to reconciliation; only version GAPS are.

    Duplicate and stale-version events never reach the state-update
    section — both skip paths ``return``/``raise`` before it.
    """
    assert_envelope_tenant_authorized(envelope)

    canonical = _parse_canonical_reschedule_data(envelope.data)

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.appointment_rescheduled.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    # P1 fix (review of commit 26bc616): select_for_update() so a
    # concurrent delivery for the SAME appointment_id blocks on this
    # row until we commit, then re-reads the POST-commit state instead
    # of racing us with a stale last_applied_appointment_version.
    proxy = (
        RemoteBookingProxy.all_tenants.select_for_update()
        .filter(appointment_id=canonical.appointment_id)
        .first()
    )

    # N-Adv2 pattern (see handle_booking_rescheduled): tenant guard
    # FIRST, before any idempotency/ordering short-circuit.
    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    if proxy is None:
        # Out-of-order: canonical reschedule before booking.created.
        # P1 fix (review of commit 26bc616): raise, do NOT return —
        # see CanonicalReschedulePendingProxyError docstring for why a
        # silent OK here would permanently lose the reschedule.
        logger.warning(
            "eventbus.consumer.appointment_rescheduled.pending_proxy "
            "appointment_id=%s event_id=%s — booking.created not seen "
            "yet, retrying via dispatcher",
            canonical.appointment_id,
            envelope.event_id,
        )
        raise CanonicalReschedulePendingProxyError(
            f"appointment {canonical.appointment_id}: no RemoteBookingProxy "
            f"yet for canonical reschedule version={canonical.version} "
            f"(event_id={envelope.event_id}); booking.created must land first"
        )

    last_applied = proxy.last_applied_appointment_version

    if last_applied is not None and canonical.version <= last_applied:
        logger.info(
            "eventbus.consumer.appointment_rescheduled.stale_or_duplicate_version "
            "appointment_id=%s incoming_version=%d last_applied_version=%d event_id=%s",
            canonical.appointment_id,
            canonical.version,
            last_applied,
            envelope.event_id,
        )
        return

    if last_applied is not None and canonical.previous_version != last_applied:
        logger.error(
            "eventbus.consumer.appointment_rescheduled.version_gap "
            "appointment_id=%s incoming_version=%d incoming_previous_version=%d "
            "last_applied_version=%d event_id=%s",
            canonical.appointment_id,
            canonical.version,
            canonical.previous_version,
            last_applied,
            envelope.event_id,
        )
        raise CanonicalRescheduleVersionGapError(
            f"appointment {canonical.appointment_id}: canonical version gap — "
            f"incoming previous_version={canonical.previous_version} does not "
            f"chain from last_applied_version={last_applied} "
            f"(incoming version={canonical.version})"
        )

    if last_applied is None:
        logger.info(
            "eventbus.consumer.appointment_rescheduled.version_bootstrap "
            "appointment_id=%s seed_version=%d event_id=%s",
            canonical.appointment_id,
            canonical.version,
            envelope.event_id,
        )

    update_fields: dict[str, Any] = {
        "last_applied_appointment_version": canonical.version,
        "last_synced_event_id": envelope.event_id,
    }

    new_start_at: dt.datetime | None = None
    if canonical.starts_at is not None:
        # F-Adv4 pattern (see handle_booking_rescheduled): refuse to
        # compute duration off a corrupted (non-positive-duration) proxy.
        if proxy.end_at <= proxy.start_at:
            logger.error(
                "eventbus.consumer.appointment_rescheduled.corrupted_proxy "
                "appointment_id=%s start_at=%s end_at=%s event_id=%s",
                canonical.appointment_id,
                proxy.start_at.isoformat(),
                proxy.end_at.isoformat(),
                envelope.event_id,
            )
            raise ValueError(
                f"RemoteBookingProxy {canonical.appointment_id} has non-positive "
                f"duration ({proxy.start_at} → {proxy.end_at}); refusing reschedule"
            )

        original_duration = proxy.end_at - proxy.start_at
        new_start_at = canonical.starts_at
        update_fields["start_at"] = new_start_at
        update_fields["end_at"] = new_start_at + original_duration

    RemoteBookingProxy.all_tenants.filter(appointment_id=canonical.appointment_id).update(
        **update_fields
    )

    if new_start_at is not None:
        _reschedule_reminders(appointment_id=canonical.appointment_id, new_start_at=new_start_at)

        bot_user = _resolve_bot_user(user_id=UUID(envelope.user_id), tenant=tenant)
        if bot_user is not None:
            _touch_conversation_last_booking(
                bot_user=bot_user,
                tenant=tenant,
                last_booking_at=new_start_at,
            )

    emit_internal_event(
        "appointment_rescheduled",
        properties={
            "appointment_id": str(canonical.appointment_id),
            "version": canonical.version,
            "previous_version": canonical.previous_version,
            "revision_id": canonical.revision_id,
            "changed_fields": list(canonical.changed_fields),
            "starts_at": envelope.data.get("starts_at") or "",
        },
    )


def handle_booking_completed(envelope: IngestEnvelope) -> None:
    """``booking.completed`` — event-contract.md §3.4.

    Step 1: flip proxy status to completed. Step 2 (post-visit review
    skill trigger) and step 3 (RFM/sentiment update) live in
    follow-up tickets; this handler only owns the proxy state flip.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    appointment_id = UUID(data["appointment_id"])

    # ``completed`` doesn't need a Tenant object for the no-op-on-
    # missing UPDATE path, but it still needs the spoof-block:
    # resolve the envelope's tenant just for the cross-tenant guard.
    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.booking.completed.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    proxy = RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).first()

    # N-Adv2: tenant guard FIRST, BEFORE the idempotency short-circuit.
    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    # Defence-in-depth idempotency short-circuit.
    if proxy is not None and proxy.last_synced_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.booking.completed.replay_skipped appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).update(
        status=RemoteBookingProxy.Status.COMPLETED,
        last_synced_event_id=envelope.event_id,
    )

    emit_internal_event(
        "booking_completed",
        properties={
            "appointment_id": str(appointment_id),
            "completed_at": data.get("completed_at", ""),
        },
    )


def handle_booking_confirmed(envelope: IngestEnvelope) -> None:
    """``booking.confirmed`` — appointment moved to ``confirmed`` (B1).

    Emitted by Ayla when an appointment is confirmed (typically once
    payment is captured). Idempotently flips the proxy to ``confirmed``
    and ensures reminders exist. Missing proxy is a retryable failure;
    a late confirm after cancellation is a no-op because ``cancelled``
    is a terminal state for the pilot.

    Side-effect: if ``data.payment_id`` is present, the handler also
    upserts the payment mirror via ``upsert_payment_mirror`` so the
    local copy stays in sync with the Ayla payment state.

    DRF-1066 side-effect: on a genuine transition into ``confirmed``
    (the proxy was not already confirmed) the client is told «вы
    записаны» in chat after commit. A booking that was born confirmed
    was announced by :func:`handle_booking_created`; this call site
    exists for the prepayment flow, where the appointment is created
    ``awaiting_payment`` and only becomes real here.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    appointment_id = UUID(data["appointment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.booking.confirmed.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    proxy = (
        RemoteBookingProxy.all_tenants.select_for_update()
        .filter(appointment_id=appointment_id)
        .first()
    )

    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    if proxy is None:
        logger.warning(
            "eventbus.consumer.booking.confirmed.pending_proxy "
            "appointment_id=%s event_id=%s tenant_id=%s",
            appointment_id,
            envelope.event_id,
            tenant.id,
        )
        raise BookingConfirmedPendingProxyError(
            f"appointment {appointment_id}: no RemoteBookingProxy yet for booking.confirmed "
            f"(event_id={envelope.event_id})"
        )

    if proxy.last_synced_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.booking.confirmed.replay_skipped appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    if proxy.status == RemoteBookingProxy.Status.CANCELLED:
        logger.info(
            "eventbus.consumer.booking.confirmed.after_cancelled_noop "
            "appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    # DRF-1066: read the pre-flip state — the client confirmation below
    # announces the *transition into* confirmed, not the state.
    was_confirmed = proxy.status == RemoteBookingProxy.Status.CONFIRMED

    proxy.status = RemoteBookingProxy.Status.CONFIRMED
    proxy.last_synced_event_id = envelope.event_id
    proxy.save(update_fields=["status", "last_synced_event_id", "synced_at"])

    bot_user = _resolve_bot_user(user_id=UUID(envelope.user_id), tenant=tenant)
    if bot_user is not None:
        _schedule_reminders(
            tenant=tenant,
            bot_user=bot_user,
            appointment_id=appointment_id,
            start_at=proxy.start_at,
        )

    # DRF-1066 — «вы записаны» to the client, for the prepayment flow:
    # the appointment was created ``awaiting_payment`` (nothing was said
    # then, because nothing was true yet) and only becomes real here.
    # Mutually exclusive with the ``handle_booking_created`` call site by
    # construction: a booking born confirmed was announced there and is
    # already CONFIRMED when this handler runs, so ``was_confirmed``
    # short-circuits it. The chat path likewise writes its mirror row
    # CONFIRMED, so a dialog booking never reaches the send here either
    # — and the chat-origin guard inside the callback backs that up.
    #
    # DRF-1069: the same claim the creation path takes, so «the client
    # has been told about this appointment» is one durable fact shared
    # by both call sites instead of two handlers each reasoning from
    # their own local view of the state machine.
    if not was_confirmed and _claim_announcement(
        appointment_id=appointment_id, slot="client_notified_at"
    ):
        schedule_client_booking_confirmation(
            tenant=tenant,
            bot_user=bot_user,
            appointment_id=appointment_id,
            start_at=proxy.start_at,
            specialist_id=proxy.specialist_id,
            service_id=proxy.service_id,
        )

    if data.get("payment_id"):
        from apps.eventbus.consumers.payment import upsert_payment_mirror

        upsert_payment_mirror(
            tenant=tenant,
            appointment_id=appointment_id,
            payment_id=data.get("payment_id"),
            capture_state="authorized",
            amount=data.get("amount"),
            event_id=envelope.event_id,
        )

    emit_internal_event(
        "booking_confirmed",
        properties={
            "appointment_id": str(appointment_id),
            "payment_id": data.get("payment_id", ""),
        },
    )


def handle_booking_no_show(envelope: IngestEnvelope) -> None:
    """``booking.no_show`` — client didn't show up (AMD-018, event #13 v1).

    Standalone cross-service event emitted by Ayla's state machine
    (``mark_no_show``) — NOT remodelled as ``booking.cancelled`` and the
    cancelled handler is NOT invoked. Side-effects, mirroring the other
    booking handlers' canonical shape:

      1. Flip ``RemoteBookingProxy.status`` to ``NO_SHOW`` (faithful
         mirror of Ayla's terminal state).
      2. Cancel the appointment's reminders ONLY in ``PENDING`` state —
         sent / already-cancelled / completed reminders are untouched.

    No monetary action: no capture / cancel / refund / BookingFee —
    no-show money policy is a separate W1 decision (AMD-018 §scope).

    Idempotent: proxy ``last_synced_event_id`` short-circuit; the
    reminders UPDATE is naturally idempotent. Unknown booking follows
    the standard proxy-missing path (no new policy); unknown
    event_version dead-letters at the dispatcher as usual.
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data
    appointment_id = UUID(data["appointment_id"])

    tenant = _resolve_tenant(envelope.tenant_id)
    if tenant is None:
        logger.warning(
            "eventbus.consumer.booking.no_show.unknown_tenant tenant_id=%s",
            envelope.tenant_id,
        )
        return

    proxy = RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).first()

    # N-Adv2: tenant guard FIRST, BEFORE the idempotency short-circuit.
    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    # Defence-in-depth idempotency short-circuit.
    if proxy is not None and proxy.last_synced_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.booking.no_show.replay_skipped appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).update(
        status=RemoteBookingProxy.Status.NO_SHOW,
        last_synced_event_id=envelope.event_id,
    )
    _cancel_reminders(appointment_id=appointment_id)


# ─── registration ──────────────────────────────────────────────────────────


def register_booking_handlers() -> None:
    """Register the booking.* handlers with the ingest dispatcher.

    Called from :meth:`apps.eventbus.apps.EventBusConfig.ready` so the
    registry is populated deterministically at every Django start.
    Idempotent: re-registering the same key raises (the dispatcher
    enforces «no silent shadow»), so we wrap each call in a
    try/except that swallows ValueError on already-registered — the
    duplicate-import path during tests + Django's app-reload need
    that tolerance.
    """
    pairs: tuple[tuple[str, int, Any], ...] = (
        ("booking.created", 1, handle_booking_created),
        ("booking.confirmed", 1, handle_booking_confirmed),
        ("booking.cancelled", 1, handle_booking_cancelled),
        # Legacy compatibility contract — see module docstring.
        ("booking.rescheduled", 1, handle_booking_rescheduled),
        # Canonical DER contract — SEPARATE handler, see module docstring.
        ("appointment.rescheduled", 1, handle_appointment_rescheduled_canonical),
        ("booking.completed", 1, handle_booking_completed),
        ("booking.no_show", 1, handle_booking_no_show),
    )
    for event_name, version, handler in pairs:
        try:
            register(event_name, version, handler)
        except ValueError:
            # Already registered — happens under test re-import or
            # Django runserver autoreload. Safe to ignore.
            pass
