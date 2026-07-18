"""Periodic Celery task: escalate stale T-24h reminders to salon manager.

DRF-845 / Phase 1 / R2. Runs every hour via Celery beat (see
``CELERY_BEAT_SCHEDULE`` in ``config/settings/base.py``).

### Why a separate module from ``tasks.py``

``send_due_reminders`` (R1) and ``escalate_stale_reminders`` (this
module) share the same compare-and-set / per-tenant fan-out skeleton,
but their *predicates*, *transitions*, and *message content* are
disjoint. Coupling them in one file would force readers to mentally
filter "is this branch for dispatch or escalation?". Keeping the two
beats in sibling modules with shared idiom (CAS via
``.update().filter(status=expected)``) is the right amount of DRY for
Phase 1.

### Filter

Picks rows where:

* ``kind == DAY_BEFORE`` — T-2h reminders fire too late for a phone
  call (the manager wouldn't reach the client before the visit), so
  they are never escalated.
* ``status == SENT_NO_REPLY`` — client received the T-24h reminder but
  hasn't tapped any of the confirm/cancel/reschedule buttons (R1's
  three-button keyboard). ``CONFIRMED`` / ``CANCELLED`` /
  ``RESCHEDULE_REQUESTED`` / ``ESCALATED`` are all terminal-ish from
  the escalation perspective and are filtered out by the
  ``SENT_NO_REPLY`` predicate alone.
* ``visit_at <= now() + 12h`` — the visit is within the next 12 hours.
  Earlier than that, we wait (the client may still tap during work
  hours); later than that, the booking already happened or was missed
  and there's nothing for the manager to phone about. The 12-hour
  window is from the DRF-845 spec; matches the existing mysite
  behaviour.

### Race safety

Same compare-and-set pattern as ``send_due_reminders``: the SQL is

    UPDATE booking_reminder
       SET status = 'escalated'
     WHERE id = %s AND status = 'sent_no_reply'

The rowcount-zero branch handles the case where two beat workers
overlap (Celery's ``acks_late`` guarantees at-least-once delivery, so
two ticks on the same minute *can* both grab the same row). The losing
worker silently skips.

### ESCALATED is terminal

Once a row is ``ESCALATED`` it never re-escalates: the
``status == SENT_NO_REPLY`` filter naturally excludes it. We still
flip status to ``ESCALATED`` *even when* ``manager_chat_id`` is empty
on the tenant — the row's lifecycle is independent of whether the
message actually went out, and leaving the row in ``SENT_NO_REPLY``
would just cause the next hourly beat to retry forever. The empty-
``manager_chat_id`` case logs a WARN so operators notice the
mis-configured tenant.

### Send-failure semantics

If the MAX outbound raises, status stays ``SENT_NO_REPLY`` so the next
hourly run retries naturally. Contrast with R1's
``send_due_reminders`` which flips to ``FAILED`` on send error —
there, retrying would risk double-sending a *client-facing* message;
here, the message is *manager-facing* and a duplicate manager alert
is far less costly than a missed escalation. So R2 prefers
at-least-once delivery; R1 prefers at-most-once.

The retry behaves correctly because:

1. Status starts as ``SENT_NO_REPLY``.
2. CAS flips to ``ESCALATED``.
3. ``send_message`` raises.
4. We revert: another CAS ``ESCALATED → SENT_NO_REPLY``.
5. Next hourly beat tick re-picks the row.

The revert CAS predicate ``status=ESCALATED`` means a concurrent
worker that somehow already re-flipped the row can't be clobbered;
the revert simply no-ops in that case.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]
from django.utils import timezone
from datetime import timedelta

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder
from apps.channels.max.outbound import MaxAPIError, send_message

# E0 #6 — send-time booking-state recheck. The same helper governs
# the T-24h / T-2h dispatch loop in `tasks.py`; reusing it here keeps
# the canonical drop/defer rules aligned between dispatch and
# escalation. Per founder verdict 2026-06-01 the escalation pipeline
# was missing this recheck → a salon manager could receive "Клиент
# не подтвердил" DMs for bookings the client had ALREADY cancelled
# through Ayla. Especially relevant during pilot's A2 dual-source
# state window (memory: adr_0009_yclients_webhook_shrink_post_pilot).
# Imported from `recheck` (not `tasks`) — `tasks` re-exports this
# module's task for Celery autodiscover, so importing from `tasks`
# here closed a module-level import cycle.
from apps.bookings.recheck import (  # noqa: E501
    _ACTION_DEFER,
    _ACTION_DROP,
    _recheck_booking_state,
)

logger = logging.getLogger(__name__)


# Tunables — module-scope so tests can monkeypatch.
BATCH_LIMIT = 200
ESCALATION_WINDOW = timedelta(hours=12)


def _format_escalation_text(reminder: BookingReminder) -> str:
    """Render the manager-facing escalation body.

    Plain text — no buttons, no callbacks. The manager calls the client
    by phone, so the message must surface the phone number prominently
    and include enough context that the manager can identify the
    booking without opening YClients.

    Fields included (per DRF-845 spec):

    * client phone — primary actionable datum
    * client name — for warm intro on the call
    * service name — to set context ("about your massage tomorrow")
    * visit time — to confirm the slot is still wanted
    * master name — to confirm the right provider
    * booking_request UUID — for ops to cross-reference with audit logs
      ("which row in the system are we looking at?"). If the reminder
      has no ``booking_request`` link (webhook-created without a
      pre-existing booking row), we surface the reminder's own UUID
      instead so the row is still locatable.

    Phone / name are pulled from the linked ``bot_user`` (snapshot of
    client identity at booking time, stable across BotUser edits — same
    rationale as ``chat_id`` snapshotting on the reminder itself).
    """
    visit_local = timezone.localtime(reminder.visit_at)
    bu = reminder.bot_user
    phone = (bu.phone if bu else "") or "—"
    client_name = (bu.client_name if bu else "") or "—"
    if reminder.booking_request_id is not None:
        traceable_id = reminder.booking_request_id
    else:
        traceable_id = reminder.id
    return (
        "⚠️ Клиент не подтвердил запись.\n"
        f"Имя: {client_name}\n"
        f"Телефон: {phone}\n"
        f"Услуга: {reminder.service_name or '—'}\n"
        f"Мастер: {reminder.master_name or '—'}\n"
        f"Визит: {visit_local.strftime('%d.%m в %H:%M')}\n"
        f"ID записи: {traceable_id}\n\n"
        "Пожалуйста, позвоните клиенту и уточните."
    )


def _stale_rows(now) -> Any:  # noqa: ANN401 — Django QuerySet
    """Build the queryset of stale T-24h reminders.

    Single SQL query, ordered by ``visit_at`` (soonest visit first —
    those are the most time-critical for the manager to phone). The
    ``select_related`` joins are mandatory: we read tenant + bot_user
    inside the per-row loop, and N+1 here would mean ``2 * len(qs)``
    extra queries per beat tick.
    """
    cutoff = now + ESCALATION_WINDOW
    return (
        BookingReminder.all_tenants.filter(
            kind=BookingReminder.Kind.DAY_BEFORE,
            status=BookingReminder.Status.SENT_NO_REPLY,
            visit_at__lte=cutoff,
        )
        .select_related("tenant", "bot_user")
        .order_by("visit_at")[:BATCH_LIMIT]
    )


@shared_task(name="bookings.escalate_stale_reminders")
def escalate_stale_reminders() -> dict[str, int]:
    """Escalate every T-24h reminder the client never replied to.

    Per-row flow:

    1. CAS ``SENT_NO_REPLY → ESCALATED``. ``rowcount == 0`` → race
       lost (another worker already escalated); skip.
    2. If tenant ``manager_chat_id`` is empty → log WARN, audit, count
       as "escalated" (row is now terminal), continue. **Do not** send
       anything; the operator will discover the empty chat_id via the
       audit row.
    3. Compose plain-text escalation body.
    4. Call :func:`apps.channels.max.outbound.send_message` (no
       attachments — the manager isn't tapping anything).
    5. On send failure: revert status to ``SENT_NO_REPLY`` via a
       guarded CAS (``ESCALATED → SENT_NO_REPLY`` only). The next
       hourly tick retries. Log + audit so operators can see the
       transient errors accumulate if MAX is having a bad day.
    6. On success: audit the escalation.

    Returns ``{"escalated": int, "no_manager": int, "send_failed": int,
    "skipped": int}`` for telemetry / test visibility.
    """
    now = timezone.now()

    escalated = 0
    no_manager = 0
    send_failed = 0
    skipped = 0
    for row in _stale_rows(now):
        # E0 #6 — recheck booking state BEFORE the CAS claim. If the
        # client cancelled their booking through Ayla after the T-24h
        # reminder fired but before the 12h escalation window, the
        # row's BookingReminder.status is still SENT_NO_REPLY (the
        # cancellation never touched the reminder row) — without this
        # gate the manager would be DM'd "Клиент не подтвердил" for
        # a booking that no longer exists. The recheck reuses the
        # dispatch-side helper so drop/defer semantics stay aligned
        # between the two beats.
        action, reason = _recheck_booking_state(row)
        if action == _ACTION_DROP:
            # Booking cancelled / rescheduled / completed — escalation
            # is no longer appropriate. Mark the row terminal so the
            # next beat tick does not re-evaluate; audit the skip so
            # ops can verify the pipeline behaved correctly during
            # the A2 pilot dual-source window.
            BookingReminder.all_tenants.filter(
                pk=row.pk,
                status=BookingReminder.Status.SENT_NO_REPLY,
            ).update(status=BookingReminder.Status.ESCALATED)
            write_audit(
                action="bookings.reminder.escalation_skipped",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "reason": reason,
                },
            )
            logger.info(
                "bookings.escalate.skipped_dropped pk=%s reason=%s",
                row.pk,
                reason,
            )
            skipped += 1
            continue
        if action == _ACTION_DEFER:
            # Interim reversible state (cancel/reschedule requested ~5s
            # undo window). Leave the row in SENT_NO_REPLY so the next
            # hourly tick re-evaluates after the undo window closes.
            logger.info(
                "bookings.escalate.deferred pk=%s reason=%s",
                row.pk,
                reason,
            )
            skipped += 1
            continue

        # CAS: only proceed if we are the first to claim this row.
        rowcount = BookingReminder.all_tenants.filter(
            pk=row.pk,
            status=BookingReminder.Status.SENT_NO_REPLY,
        ).update(status=BookingReminder.Status.ESCALATED)
        if rowcount == 0:
            logger.info(
                "bookings.escalate.race_lost pk=%s yclients_record_id=%s",
                row.pk,
                row.yclients_record_id,
            )
            skipped += 1
            continue

        # We own the row.
        tenant = row.tenant
        manager_chat_id = (tenant.manager_chat_id or "").strip()
        if not manager_chat_id:
            logger.warning(
                "bookings.escalate.no_manager_chat tenant=%s pk=%s",
                tenant.slug,
                row.pk,
            )
            write_audit(
                action="bookings.reminder.escalated",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "delivered": False,
                    "reason": "no_manager_chat_id",
                },
            )
            no_manager += 1
            continue

        text = _format_escalation_text(row)
        try:
            send_message(
                chat_id=manager_chat_id,
                text=text,
                attachments=None,
            )
        except MaxAPIError as exc:
            logger.warning(
                "bookings.escalate.send_failed pk=%s status=%s err=%s",
                row.pk,
                exc.status_code,
                exc.body[:200] if exc.body else "",
            )
            # Revert so the next hourly tick retries. Guarded CAS
            # (ESCALATED → SENT_NO_REPLY) prevents clobbering a row a
            # concurrent worker may have re-claimed.
            BookingReminder.all_tenants.filter(
                pk=row.pk,
                status=BookingReminder.Status.ESCALATED,
            ).update(status=BookingReminder.Status.SENT_NO_REPLY)
            write_audit(
                action="bookings.reminder.escalation_failed",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "status_code": exc.status_code,
                },
            )
            send_failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 — defensive, see R1 tasks.py
            logger.exception("bookings.escalate.send_unexpected pk=%s", row.pk)
            BookingReminder.all_tenants.filter(
                pk=row.pk,
                status=BookingReminder.Status.ESCALATED,
            ).update(status=BookingReminder.Status.SENT_NO_REPLY)
            write_audit(
                action="bookings.reminder.escalation_failed",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "exception_type": type(exc).__name__,
                },
            )
            send_failed += 1
            continue

        # Success.
        write_audit(
            action="bookings.reminder.escalated",
            target="BookingReminder",
            target_id=row.pk,
            payload={
                "kind": row.kind,
                "yclients_record_id": row.yclients_record_id,
                "delivered": True,
            },
        )
        escalated += 1

    if escalated or no_manager or send_failed or skipped:
        logger.info(
            "bookings.escalate.summary escalated=%d no_manager=%d send_failed=%d skipped=%d",
            escalated,
            no_manager,
            send_failed,
            skipped,
        )
    return {
        "escalated": escalated,
        "no_manager": no_manager,
        "send_failed": send_failed,
        "skipped": skipped,
    }
