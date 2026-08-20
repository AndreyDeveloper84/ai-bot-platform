"""Send-time booking-state re-check (P0 PRE_PILOT, founder sequence #2).

Shared classifier used by both the reminder dispatch loop
(:mod:`apps.bookings.tasks`) and the escalation pipeline
(:mod:`apps.bookings.escalation`). It lives in its own module because
``tasks`` re-exports ``escalation.escalate_stale_reminders`` for Celery
autodiscover while ``escalation`` needs this classifier — importing it
from ``tasks`` created a module-level import cycle (collection-time
ImportError in the test suite). Extracting the shared helper here breaks
the cycle without lazy imports.

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

Two sources of truth feed the classifier, one per booking path. The legacy
YClients path is re-checked against the local ``BookingRequest`` mirror; the
Ayla path is re-checked against ``RemoteBookingProxy`` — see
:func:`_recheck_ayla_mirror_state`. Before DRF-1144 only the first existed,
which made every Ayla-path reminder an unconditional send.

Action constants live as plain strings (no Enum) — minimum surface for a
3-state classifier; callers branch on equality directly.
"""

from __future__ import annotations

from apps.booking.mirror_status import LIVE_STATUSES, TERMINAL_STATUSES
from apps.booking.models import BookingReminder
from apps.booking.reminder_lookup import ayla_appointment_id_of

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
      * Ayla-path rows, which link via ``ayla_appointment_id`` — or, on the
        dialog booking path, via an appointment UUID parked in
        ``yclients_record_id`` (see :mod:`apps.booking.reminder_lookup`).

    Both are delegated to :func:`_recheck_ayla_mirror_state`, which classifies
    against the ``RemoteBookingProxy`` mirror and falls back to the legacy
    ``send`` only when no Ayla appointment identity can be recovered at all.
    Until DRF-1144 this whole branch was an unconditional ``send``.
    """
    booking_request = reminder.booking_request
    if booking_request is None:
        # NULL FK = legacy YClients row OR Ayla-path row. The Ayla path has
        # its own source of truth — the mirror — so route there (DRF-1144).
        return _recheck_ayla_mirror_state(reminder)

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


def _recheck_ayla_mirror_state(reminder: BookingReminder) -> tuple[str, str]:
    """Classify an Ayla-path reminder against the ``RemoteBookingProxy`` mirror.

    This is the branch DRF-1144 was filed against. It used to be an
    unconditional ``send``: :func:`_recheck_booking_state` only knew how to
    read ``BookingRequest.status``, and every Ayla-path reminder has a NULL
    ``booking_request`` FK. The reminder therefore trusted nothing at all —
    not the mirror, not the backend — and the ONLY thing that could stop it
    was ``booking.cancelled`` pre-emptively flipping the row to ``CANCELLED``.
    Anything that route missed (a dialog-created reminder the cancel sweep did
    not match, a cancellation that dead-lettered, a booking deleted in the
    backend where no event exists at all — DRF-1034) reached a live person as
    «завтра ваша запись» about a booking that was gone.

    Mapping:

    * mirror row missing → **drop**. We scheduled this reminder off a mirror
      row; if the row is not there we cannot vouch for the booking, and
      «не отправлять» is the whole point of DRF-1144. ``STALE_DROPPED`` plus
      its audit line is what an operator needs to see, which is why this is a
      drop and not an indefinite defer.
    * ``cancelled`` / ``completed`` / ``no_show`` → **drop**.
    * ``confirmed`` / ``pending_payment`` / ``awaiting_payment`` /
      ``tentative`` → **send**.
    * anything else → **defer**, matching the conservative default the
      ``BookingRequest`` branch already uses for unrecognised state slugs.

    Not covered here, and it must not be oversold: a booking hard-deleted in
    the backend leaves the mirror row untouched and ``confirmed``, so this
    returns **send**. Nothing inside this repository can observe that
    deletion — the backend emits no deletion event (DRF-1034). Closing it
    needs either a deletion contract on the Ayla side or the mirror
    reconciliation sweep (DRF-1111).
    """
    appointment_id = ayla_appointment_id_of(reminder)
    if appointment_id is None:
        # A genuine legacy YClients row: integer record id, no Ayla mirror to
        # consult. Behaviour unchanged from before DRF-1144.
        return (_ACTION_SEND, "null_fk_legacy_yclients_path")

    # Imported locally for the same reason the BookingRequest import above is:
    # apps.booking.models reaches back into apps.bookings via signals.
    from apps.booking.models import RemoteBookingProxy

    status = (
        RemoteBookingProxy.all_tenants.filter(
            appointment_id=appointment_id,
            tenant_id=reminder.tenant_id,
        )
        .values_list("status", flat=True)
        .first()
    )

    if status is None:
        # Either no mirror row was ever written, or it belongs to a different
        # tenant (the tenant_id predicate makes a cross-tenant row read as
        # absent — deliberate: we never classify off another tenant's data).
        return (_ACTION_DROP, "ayla_mirror_missing")

    if status in TERMINAL_STATUSES:
        return (_ACTION_DROP, f"ayla_mirror_status_{status}")

    if status in LIVE_STATUSES:
        return (_ACTION_SEND, f"ayla_mirror_status_{status}")

    return (_ACTION_DEFER, f"ayla_mirror_status_unknown_{status}")
