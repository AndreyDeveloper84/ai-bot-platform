"""Send-time booking-state re-check (P0 PRE_PILOT, founder sequence #2).

Shared by the R1 dispatch beat (:mod:`apps.bookings.tasks`) and the R2
escalation beat (:mod:`apps.bookings.escalation`). Lives in this
dependency-neutral module so both beats share one canonical
implementation **without** importing each other — the tasks ↔
escalation import cycle (W0-B1A) was broken by moving this helper
here.

Between the moment a reminder is scheduled (T-24h or T-2h before visit)
and the moment the beat dispatcher claims it, the underlying booking
may have flipped state — customer cancelled via B5, admin rescheduled,
visit already happened. Sending in those states = «Ayla напомнила о
записи которую я отменила» = trust break.

This helper re-fetches the linked ``BookingRequest`` mirror at dispatch
time and classifies the action к take:

  * **send** — booking still CONFIRMED + not completed; proceed normally.
  * **drop** — booking moved к a terminal-invalid state. Reminder
               transitions к ``STALE_DROPPED`` (audit trail), no send.
  * **defer** — booking is in an interim reversible state
                (CANCEL_REQUESTED / RESCHEDULE_REQUESTED within undo
                window). Skip THIS tick без CAS; next 15-min tick
                re-checks. Avoids the «user un-cancels but reminder
                already dropped» edge case.

Action constants live as plain strings (no Enum) — minimum surface for a
3-state classifier; callers branch on equality directly.
"""

from __future__ import annotations

from apps.booking.models import BookingReminder

_ACTION_SEND = "send"
_ACTION_DROP = "drop"
_ACTION_DEFER = "defer"


def _recheck_booking_state(reminder: BookingReminder) -> tuple[str, str]:
    """Classify the dispatch action for ``reminder`` based on current
    booking state. See module-level rationale block above for verbatim
    state-mapping table.

    Returns:
      A ``(action, reason)`` pair. ``reason`` is a stable slug suitable
      для audit payload / log line correlation. Caller chooses what к
      do based on ``action``.

    ### NULL FK / Ayla-path handling

    ``BookingReminder.booking_request`` may be NULL для:

      * Legacy rows pre-B5 (B5 introduced ``BookingRequest.status``
        enum and the FK on the reminder).
      * Ayla-path rows that link via ``ayla_appointment_id`` instead.

    Both cases return ``(send, "null_fk_legacy_or_ayla_path")`` — known
    Phase 0 gap. Ayla-path stale detection lands в Phase 1 (Ayla emits
    ``appointment.cancelled`` event → bot-platform consumer pre-emptively
    drops the reminder). Documented prominently в PR description.
    """
    booking_request = reminder.booking_request
    if booking_request is None:
        # NULL FK = legacy row OR Ayla-path. Pilot-scope gap.
        return (_ACTION_SEND, "null_fk_legacy_or_ayla_path")

    # Completed visit — T-2h reminder for already-happened appointment
    # is absurd; T-24h race window technically impossible (visit_at < 24h
    # away can't be completed yet) but defensive check costs nothing.
    if booking_request.completed_at is not None:
        return (_ACTION_DROP, "booking_completed")

    # Import locally to avoid module-import cycle с apps.booking.models
    # (which may reach back into bookings via signals).
    from apps.booking.models import BookingRequest

    status = booking_request.status
    if status in (
        BookingRequest.Status.CANCELLED,
        BookingRequest.Status.RESCHEDULED,
    ):
        return (_ACTION_DROP, f"booking_status_{status}")

    if status in (
        BookingRequest.Status.CANCEL_REQUESTED,
        BookingRequest.Status.RESCHEDULE_REQUESTED,
    ):
        # Interim reversible state (~5s undo window per booking spec).
        # Defer без CAS so user can revert and still receive reminder.
        return (_ACTION_DEFER, f"booking_status_{status}")

    if status == BookingRequest.Status.CONFIRMED:
        return (_ACTION_SEND, "booking_confirmed")

    # Unknown / future status (mirror may receive new Ayla state slugs
    # before this code learns about them — e.g. ``provider_cancelled``,
    # ``no_show``, ``dispute`` arrive в Phase 1). Conservative default:
    # defer + log. Better к miss a few reminders one tick than send for
    # a state we don't understand.
    return (_ACTION_DEFER, f"booking_status_unknown_{status}")
