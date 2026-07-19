"""Booking.* event consumers (#442).

Per `docs/architecture/event-contract.md` §3.1–§3.4. Four handlers
for the booking lifecycle:

* ``booking.created`` — upsert :class:`RemoteBookingProxy`, schedule
  T-24h and T-2h reminders, set ``Conversation.last_booking_at``,
  emit internal analytics event.
* ``booking.cancelled`` — flip proxy status to ``cancelled``, cancel
  pending reminders.
* ``booking.rescheduled`` — update proxy ``start_at``/``end_at``
  (preserving duration), re-peg reminders.
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
from datetime import timedelta
from typing import Any, Final
from uuid import UUID


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
    """
    chat_id = getattr(bot_user, "chat_id", "") or ""
    if not chat_id:
        logger.info(
            "eventbus.consumer.booking.skip_reminders_no_chat appointment_id=%s",
            appointment_id,
        )
        return

    for kind, offset in _REMINDER_OFFSETS:
        BookingReminder.all_tenants.update_or_create(
            ayla_appointment_id=appointment_id,
            tenant=tenant,  # F-Adv2: tenant scopes the partial unique key
            kind=kind,
            defaults={
                "bot_user": bot_user,
                # F-Fri1: write NULL (not "") so the legacy
                # unique_together (yclients_record_id, kind) doesn't
                # collide across multiple Ayla appointments.
                "yclients_record_id": None,
                "chat_id": chat_id,
                "visit_at": start_at,
                "status": BookingReminder.Status.PENDING,
                "scheduled_at": start_at - offset,
                # Names are looked up via the catalog mirror on send.
                "master_name": "",
                "service_name": "",
                "sent_at": None,
                "replied_at": None,
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


# ─── handlers ──────────────────────────────────────────────────────────────


def handle_booking_created(envelope: IngestEnvelope) -> None:
    """``booking.created`` — event-contract.md §3.1.

    Steps:
      1. Verify tenant authorization (A3 mandate).
      2. Upsert RemoteBookingProxy keyed by appointment_id.
      3. Schedule T-24h + T-2h reminders (idempotent).
      4. Emit internal ``booking_created`` event for analytics fan-out.
      5. Set Conversation.last_booking_at = data.start_at.
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

    # Resolve channel-side BotUser FIRST so we know whether the proxy
    # is linked or orphan before the upsert fires.
    bot_user = _resolve_bot_user(user_id=UUID(envelope.user_id), tenant=tenant)

    create_defaults = {
        "tenant": tenant,
        "bot_user": bot_user,  # may be None — orphan proxy
        "start_at": start_at,
        "end_at": end_at,
        "status": data["status"],
        "source": data.get("source", ""),
        "service_id": UUID(data["service_id"]) if data.get("service_id") else None,
        "specialist_id": (UUID(data["specialist_id"]) if data.get("specialist_id") else None),
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
        update_fields = {k: v for k, v in create_defaults.items() if k != "tenant"}
        RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).update(**update_fields)

    if bot_user is not None:
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
            "eventbus.consumer.booking.created.orphan_proxy "
            "user_id=%s appointment_id=%s — awaiting BotUser backfill signal",
            envelope.user_id,
            appointment_id,
        )

    # Analytics fan-out — apps/events/ snake_case bus (§3.1 step 3).
    emit_internal_event(
        "booking_created",
        properties={
            "appointment_id": str(appointment_id),
            "status": data["status"],
            "source": data.get("source", ""),
            "start_at": data["start_at"],
        },
    )


def handle_booking_cancelled(envelope: IngestEnvelope) -> None:
    """``booking.cancelled`` — event-contract.md §3.2.

    Steps:
      1. Verify tenant authorization.
      2. Update RemoteBookingProxy.status = cancelled.
      3. Cancel all PENDING reminders for this appointment.
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

    proxy = RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).first()

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

    # N-Adv1 Variant B: out-of-order cancelled-before-created is
    # DROPPED, not stubbed. Writing a stub would let a spoofer "claim"
    # an appointment_id under their tenant before the legitimate
    # ``booking.created`` arrives — the legitimate create would then
    # hit the cross-tenant guard and dead-letter, which is a denial
    # of service from B against A. §3.2 says cancelled is idempotent
    # and may arrive out-of-order; "idempotent" means same final
    # state on N deliveries, not that a stub must be written. Drop
    # + Ayla retry on (created → cancelled) sequence converges on
    # status=CANCELLED naturally.
    if proxy is None:
        logger.info(
            "eventbus.consumer.booking.cancelled.out_of_order_dropped "
            "appointment_id=%s event_id=%s — awaiting Ayla retry "
            "post-created (cancelled-before-created sequence)",
            appointment_id,
            envelope.event_id,
        )
        return

    RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).update(
        status=RemoteBookingProxy.Status.CANCELLED,
        last_synced_event_id=envelope.event_id,
    )

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
    payment is captured). Canonical ``data``: ``appointment_id`` +
    ``payment_id`` (the Ayla emitter currently sends ``booking_id`` —
    tracked for migration in issue #945). booking.confirmed is a v1
    contract extension beyond the original 12 events (issue #946).

    Side-effect: idempotently flip ``RemoteBookingProxy.status`` to
    ``confirmed``. Same canonical shape as the other booking handlers:
    tenant guard first, then idempotency short-circuit, then an
    upsert-shaped UPDATE. No reminder change — confirming doesn't move
    ``start_at``, so the T-24h/T-2h rows from booking.created stand.
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

    proxy = RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).first()

    # N-Adv2: tenant guard FIRST, BEFORE the idempotency short-circuit.
    _assert_proxy_tenant(proxy=proxy, expected_tenant=tenant, envelope=envelope)

    # Defence-in-depth idempotency short-circuit.
    if proxy is not None and proxy.last_synced_event_id == envelope.event_id:
        logger.info(
            "eventbus.consumer.booking.confirmed.replay_skipped appointment_id=%s event_id=%s",
            appointment_id,
            envelope.event_id,
        )
        return

    RemoteBookingProxy.all_tenants.filter(appointment_id=appointment_id).update(
        status=RemoteBookingProxy.Status.CONFIRMED,
        last_synced_event_id=envelope.event_id,
    )

    # C7.3: booking.confirmed carrying a payment_id is the pilot's HOLD
    # signal (no separate payment.authorized event) — stamp the read
    # model so customer BookingItem can show «зарезервировано». Without
    # a payment_id the booking is prepay-free: no payment row at all.
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


# ─── registration ──────────────────────────────────────────────────────────


def register_booking_handlers() -> None:
    """Register all four booking.* handlers with the ingest dispatcher.

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
        ("booking.rescheduled", 1, handle_booking_rescheduled),
        ("booking.completed", 1, handle_booking_completed),
    )
    for event_name, version, handler in pairs:
        try:
            register(event_name, version, handler)
        except ValueError:
            # Already registered — happens under test re-import or
            # Django runserver autoreload. Safe to ignore.
            pass
