"""Periodic Celery task: dispatch due reminders.

DRF-844 / Phase 1 / R1. Runs every 15 minutes via Celery beat (see
``CELERY_BEAT_SCHEDULE`` in ``config/settings/base.py``). Picks
PENDING reminders whose ``scheduled_at`` has passed, transitions them
atomically, then dispatches via the channel outbound adapter.

### Idempotency / race-safety

Two workers can hit the same beat tick under load (especially with
Celery's ``acks_late`` retention semantics). The compare-and-set on
status is what guarantees "exactly one send" per reminder:

    rows = BookingReminder.all_tenants.filter(
        pk=row.pk, status=PENDING
    ).update(status=SENT_NO_REPLY)
    if rows == 0:
        # Another worker grabbed it; skip.
        continue

The :meth:`QuerySet.update` returns the rowcount; ``0`` means the
``WHERE status=PENDING`` predicate didn't match (because the other
worker already flipped it). The losing worker silently moves on. The
winning worker proceeds to send.

### Why no retry of FAILED rows

A reminder that failed to send is operationally interesting (the
operator inspects the audit log + the FAILED row), but auto-retrying
risks hammering a flaky channel or spamming the user when the channel
recovers hours later and our queue catches up. Beats fire every 15
minutes; if a transient failure clears, the operator manually flips
the row back to PENDING. Phase 2 may add a bounded retry with
exponential backoff; out of scope for R1.

### Batch limit

The 200-row batch is a soft cap on per-tick work. At the design
cadence (1 message → 2 reminders per booking, ~50 bookings/day across
all tenants combined for Phase 1), the system never fills the batch;
the cap is defensive against backlog-after-outage scenarios where a
single tick would otherwise process thousands of rows and block beat.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task  # type: ignore[import-untyped]
from django.utils import timezone

from apps.audit.services import write_audit
from apps.booking.models import BookingReminder
from apps.channels.max.outbound import MaxAPIError, send_message
from apps.bookings.keyboards import day_before_keyboard

# Re-export R2's escalation task so Celery's autodiscover_tasks() finds
# it. autodiscover walks installed apps for a top-level ``tasks`` module
# and registers every ``@shared_task`` decorator it encounters during
# import. The escalation task lives in a sibling module
# (``apps.bookings.escalation``) for readability, so we trigger the
# import here. Module import is idempotent — Celery doesn't double-
# register the task.
from apps.bookings.escalation import escalate_stale_reminders  # noqa: F401

# Re-export R3's post-visit follow-up task — same autodiscover rationale.
from apps.bookings.followups import send_post_visit_followups  # noqa: F401

logger = logging.getLogger(__name__)


# Tunables — kept at module scope so tests can monkeypatch.
BATCH_LIMIT = 200


def _format_day_before_text(reminder: BookingReminder) -> str:
    """Render the T-24h reminder body.

    Voice mirrors :mod:`apps.skills.booking.prompts` (Russian, salon
    brand voice — warm but compact). The ``visit_at`` is formatted in
    the project's configured timezone (Moscow per settings); the
    auto-tz cast happens at format time because ``visit_at`` is stored
    as a tz-aware datetime in UTC.
    """
    visit_local = timezone.localtime(reminder.visit_at)
    return (
        "Здравствуйте! Напоминаю о записи завтра:\n"
        f"{reminder.service_name or '—'} к мастеру "
        f"{reminder.master_name or '—'}\n"
        f"{visit_local.strftime('%d.%m в %H:%M')}\n\n"
        "Подтвердите, пожалуйста:"
    )


def _format_two_hours_text(reminder: BookingReminder) -> str:
    """Render the T-2h reminder body.

    Softer than T-24h — no confirmation ask, just a "see you soon"
    nudge. Matches the legacy mysite copy.
    """
    visit_local = timezone.localtime(reminder.visit_at)
    return (
        "Через 2 часа жду вас на приём:\n"
        f"{reminder.service_name or '—'} к мастеру "
        f"{reminder.master_name or '—'}\n"
        f"в {visit_local.strftime('%H:%M')}\n\n"
        "Если планы изменились — напишите, постараемся помочь."
    )


def _build_attachments(reminder: BookingReminder) -> list[dict[str, Any]] | None:
    """Build the channel-agnostic keyboard attachment for the reminder.

    Returns ``None`` for the T-2h kind (text-only nudge — see
    :func:`apps.bookings.keyboards.two_hours_keyboard`). Returns a
    single-element list wrapping the ``{label, callback}`` button row
    for T-24h; the channel adapter renders this per its native widget.

    The MAX outbound adapter accepts attachments as a pass-through
    ``list[dict]``; the channel adapter mapping from the
    platform-canonical button row to MAX's ``InlineKeyboard`` format
    is the channel adapter's responsibility (the platform contract,
    not this task's concern).
    """
    if reminder.kind == BookingReminder.Kind.DAY_BEFORE:
        buttons = day_before_keyboard(str(reminder.pk))
        # Wrap in the platform UI envelope expected by skill
        # action_data — keeps the shape uniform with the cb:food:*
        # family. Channel adapter unpacks ``payload.buttons``.
        return [{"type": "inline_keyboard", "payload": {"buttons": buttons}}]
    return None


def _target_status(kind: str) -> str:
    """Return the post-dispatch status for a given reminder kind.

    Per the spec:

    * ``DAY_BEFORE`` → ``SENT_NO_REPLY`` (waiting for confirm/cancel/
      reschedule callback within the 22h window before T-2h fires)
    * ``TWO_HOURS`` → ``SENT`` (terminal — no buttons, no expected
      reply)
    """
    if kind == BookingReminder.Kind.DAY_BEFORE:
        return BookingReminder.Status.SENT_NO_REPLY
    return BookingReminder.Status.SENT


@shared_task(name="bookings.send_due_reminders")
def send_due_reminders() -> dict[str, int]:
    """Dispatch every reminder whose ``scheduled_at`` has passed.

    Picks up to :data:`BATCH_LIMIT` rows where
    ``status=PENDING AND scheduled_at <= now()``, in scheduled_at
    order (earliest first — keeps the queue draining FIFO under load).

    Per-row flow:

    1. Compare-and-set ``status`` from PENDING → target via a single
       UPDATE. ``rowcount == 0`` → another worker won the race; skip.
    2. Build the message text + (T-24h only) keyboard attachment.
    3. Call :func:`apps.channels.max.outbound.send_message`.
    4. On success: stamp ``sent_at``. Audit + (canonical-vocab) emit
       are deferred to Phase 2 — Sprint 11 has no canonical event for
       reminders yet, and emitting non-canonical only adds a logger
       warning. Audit row IS written via ``write_audit`` which has no
       vocabulary constraint.
    5. On send failure: catch, flip to FAILED, audit. Don't requeue —
       see module docstring.

    Returns a dict ``{"sent": int, "failed": int, "skipped": int}``
    for telemetry / test visibility.
    """
    now = timezone.now()
    due_qs = (
        BookingReminder.all_tenants.filter(
            status=BookingReminder.Status.PENDING,
            scheduled_at__lte=now,
        )
        .select_related("tenant", "bot_user")
        .order_by("scheduled_at")[:BATCH_LIMIT]
    )

    sent = 0
    failed = 0
    skipped = 0
    for row in due_qs:
        target_status = _target_status(row.kind)
        # Compare-and-set: only proceed if we are the first to claim
        # this row. .update() returns the affected rowcount; 0 means
        # the WHERE didn't match (another worker beat us).
        rowcount = BookingReminder.all_tenants.filter(
            pk=row.pk,
            status=BookingReminder.Status.PENDING,
        ).update(status=target_status)
        if rowcount == 0:
            logger.info("bookings.dispatch.race_lost pk=%s kind=%s", row.pk, row.kind)
            skipped += 1
            continue

        # We own the row. Compose + send.
        if row.kind == BookingReminder.Kind.DAY_BEFORE:
            text = _format_day_before_text(row)
        else:
            text = _format_two_hours_text(row)
        attachments = _build_attachments(row)

        try:
            send_message(
                chat_id=row.chat_id,
                text=text,
                attachments=attachments,
            )
        except MaxAPIError as exc:
            logger.warning(
                "bookings.dispatch.send_failed pk=%s kind=%s status=%s err=%s",
                row.pk,
                row.kind,
                exc.status_code,
                exc.body[:200] if exc.body else "",
            )
            BookingReminder.all_tenants.filter(pk=row.pk).update(
                status=BookingReminder.Status.FAILED,
            )
            write_audit(
                action="bookings.reminder.send_failed",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "status_code": exc.status_code,
                },
            )
            failed += 1
            continue
        except Exception as exc:  # noqa: BLE001 — defensive belt-and-braces
            # The adapter only raises MaxAPIError per its contract, but
            # we wrap defensively: a regression that raised a different
            # exception type would leave the row stuck in
            # SENT_NO_REPLY/SENT with no actual send and nothing in the
            # audit log. FAIL LOUDLY in that case.
            logger.exception("bookings.dispatch.send_unexpected pk=%s", row.pk)
            BookingReminder.all_tenants.filter(pk=row.pk).update(
                status=BookingReminder.Status.FAILED,
            )
            write_audit(
                action="bookings.reminder.send_failed",
                target="BookingReminder",
                target_id=row.pk,
                payload={
                    "kind": row.kind,
                    "yclients_record_id": row.yclients_record_id,
                    "exception_type": type(exc).__name__,
                },
            )
            failed += 1
            continue

        # Success — stamp sent_at.
        BookingReminder.all_tenants.filter(pk=row.pk).update(sent_at=timezone.now())
        write_audit(
            action="bookings.reminder.sent",
            target="BookingReminder",
            target_id=row.pk,
            payload={
                "kind": row.kind,
                "yclients_record_id": row.yclients_record_id,
            },
        )
        sent += 1

    if sent or failed or skipped:
        logger.info(
            "bookings.dispatch.summary sent=%d failed=%d skipped=%d",
            sent,
            failed,
            skipped,
        )
    return {"sent": sent, "failed": failed, "skipped": skipped}


# ─── booking.completed producer ───────────────────────────────────────────
# Phase 2.3 — emits taxonomy §3.1 booking.completed when the periodic scan
# finds a confirmed booking whose visit time has passed. Without this
# producer LoyaltySubscriber (apps/loyalty, Phase 1.a) has nothing to
# subscribe to. YClients webhook port (Q-ATT-IMPL7) will be the more
# accurate signal later; this scan is the unblocking MVP until then.

# Buffer after visit_at + duration_min before we declare the visit done.
# 30 min covers «service overran» without delaying loyalty point credit
# unreasonably. Tunable via monkeypatch in tests.
COMPLETED_GRACE_MINUTES = 30
# Soft cap on rows per tick. Catches up over multiple ticks if backlog
# accumulates (post-outage). Same defensive pattern as BATCH_LIMIT above.
COMPLETED_BATCH_LIMIT = 200


@shared_task(name="bookings.detect_completed_bookings")
def detect_completed_bookings() -> dict[str, int]:
    """Scan CONFIRMED bookings whose visit time has passed; emit
    ``booking.completed`` and stamp ``completed_at`` exactly once each.

    Returns:
      Counters: ``{scanned, emitted, raced}``.
      - ``scanned``: rows matched the time predicate
      - ``emitted``: rows where this worker won the CAS and emitted
      - ``raced``: rows another worker had already stamped (lost the CAS)

    ### Race-safety

    The CAS on ``completed_at IS NULL`` is the «exactly-once emit»
    guarantee. Two workers hitting the same row do an UPDATE; only one
    rowcount comes back 1. The winning worker emits to eventbus; the
    losing worker's rowcount=0 path is silent.

    ### Tenant-less query intentional

    We use ``BookingRequest.all_tenants`` and rely on the eventbus emit
    to carry ``tenant`` from the row itself. The task itself runs from
    system context (Celery beat — no request, no current_tenant).

    ### Why a Celery task, not a model signal

    Visit completion is a *temporal* event («the clock ran out»), not a
    data-mutation event. Nothing in the booking record actually changes
    at completion time. Signal-based detection would require either a
    polling sentinel or a stored timer; the Celery beat IS the timer.
    """

    from apps.booking.models import BookingRequest
    from apps.eventbus import services as eventbus_services

    now = timezone.now()
    # Coalesce duration_min (NULL means «unknown, assume 60»). Apply the
    # buffer at query time so we don't pull millions of just-finished rows.
    from datetime import timedelta

    default_duration_min = 60

    candidates = list(
        BookingRequest.all_tenants.filter(
            status=BookingRequest.Status.CONFIRMED,
            completed_at__isnull=True,
            visit_at__isnull=False,
            visit_at__lte=now - timedelta(minutes=COMPLETED_GRACE_MINUTES),
        ).order_by("visit_at")[:COMPLETED_BATCH_LIMIT]
    )

    counters = {"scanned": 0, "emitted": 0, "raced": 0}

    for booking in candidates:
        duration = booking.duration_min or default_duration_min
        # Per-row recheck: visit_at + duration + grace ≤ now. The query-
        # level filter is `visit_at + grace`, conservative without
        # duration. Filter out rows where the duration pushes completion
        # into the future.
        #
        # ``visit_at`` is guaranteed non-NULL by the query filter
        # (visit_at__isnull=False) — assert for mypy and as a defensive
        # invariant against future filter drift.
        assert booking.visit_at is not None
        if booking.visit_at + timedelta(minutes=duration + COMPLETED_GRACE_MINUTES) > now:
            continue

        counters["scanned"] += 1

        # CAS: win the right to emit. WHERE completed_at IS NULL guards
        # against two workers stamping the same row twice.
        rowcount = BookingRequest.all_tenants.filter(
            pk=booking.pk,
            completed_at__isnull=True,
            status=BookingRequest.Status.CONFIRMED,
        ).update(completed_at=now)
        if rowcount == 0:
            counters["raced"] += 1
            continue

        try:
            eventbus_services.emit(
                "booking.completed",
                {
                    "booking_id": str(booking.pk),
                    "completed_at": now.isoformat(),
                    # «marked_by=system» because no human/yclients signal
                    # triggered completion — the Celery scanner detected it.
                    # When Q-ATT-IMPL7 lands, YClients webhook becomes the
                    # producer for status-driven completion with
                    # marked_by=external.
                    "marked_by": "system",
                },
                actor_type="system",
                tenant=booking.tenant,
            )
            counters["emitted"] += 1
        except Exception:  # noqa: BLE001 — emit failure should NOT block other rows
            logger.exception(
                "bookings.detect_completed.emit_failed booking=%s",
                booking.pk,
            )
            # Roll back the completed_at stamp so the next tick retries.
            BookingRequest.all_tenants.filter(pk=booking.pk).update(completed_at=None)
            counters["scanned"] -= 1

    if counters["emitted"] or counters["raced"]:
        logger.info(
            "bookings.detect_completed.summary scanned=%d emitted=%d raced=%d",
            counters["scanned"],
            counters["emitted"],
            counters["raced"],
        )
    return counters
