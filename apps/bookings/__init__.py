"""Booking reminder system (DRF-844 / Phase 1 / R1).

This app owns the **reminder logic** for confirmed YClients bookings:

* :mod:`reminders_factory` — single source of truth for scheduling the
  pair of reminders (DAY_BEFORE + TWO_HOURS) attached to a confirmed
  booking. Called from both the B3 skill confirm path and the B2 admin
  webhook so re-invocation is idempotent at every site.
* :mod:`tasks` — :func:`send_due_reminders` periodic Celery task. Picks
  PENDING reminders whose scheduled_at has passed, atomically transitions
  them to SENT_NO_REPLY (T-24h) / SENT (T-2h), then dispatches via the
  channel outbound adapter.
* :mod:`callbacks` — :class:`BookingReminderCallbackSkill` handles the
  three ``cb:rem:{confirm,cancel,reschedule}:{pk}`` callbacks the T-24h
  inline keyboard produces.
* :mod:`keyboards` — the T-24h button row (CONFIRM / RESCHEDULE / CANCEL)
  rendered through the channel-agnostic ``{label, callback}`` contract.

### Why a separate app

The :mod:`apps.booking` (singular) app from B2 owns the data model
(``BookingRequest`` + ``BookingReminder``). This app (plural) owns the
operational logic that *consumes* that model — keeps reminder-lifecycle
code separate from raw persistence so the two surfaces evolve cleanly.

### Idempotency contract (cornerstone)

The ``unique_together(yclients_record_id, kind)`` constraint on
``BookingReminder`` (locked in B2) plus :meth:`update_or_create` keyed
on that pair makes :func:`reminders_factory.create_reminders_for_booking`
re-invocation-safe. Calling it twice for the same booking is a no-op
(returns the same 2 rows; never duplicates).

### Atomic dispatch

:func:`tasks.send_due_reminders` uses compare-and-set on row status
(``filter(pk=pk, status=PENDING).update(status=...)``) so two workers
racing on the same PENDING row produce **exactly one** outbound send.
The loser sees ``rowcount == 0`` and skips. This is the bedrock of the
"reminder fires exactly once" guarantee even when beats overlap.
"""
