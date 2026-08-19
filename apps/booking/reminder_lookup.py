"""Which Ayla appointment does a :class:`BookingReminder` belong to? (DRF-1144)

A reminder can carry the Ayla appointment identity in **two** columns, and
which one is populated depends on who wrote the row — not on any property of
the booking:

``ayla_appointment_id``
    Written by :func:`apps.eventbus.consumers.booking._schedule_reminders`,
    i.e. reminders born from an inbound ``booking.created`` event.

``yclients_record_id``
    Written by :func:`apps.bookings.reminders_factory.create_reminders_for_booking`.
    Under ``BOOKING_VIA_AYLA_REST`` this column holds the **Ayla appointment
    UUID as a string** (``apps.skills.booking.tools._schedule_reminders`` passes
    ``yc_id``, which is the canonical appointment handle under the flag), while
    ``ayla_appointment_id`` stays NULL. That is the path every booking made in
    the bot's own dialog takes — the product's main booking path (DRF-1069).

Before this module existed, every consumer-side lifecycle sweep filtered on
``ayla_appointment_id`` alone. Dialog-created reminders were therefore
invisible to cancellation and to reschedule re-pegging: ``booking.cancelled``
matched zero rows and the reminder stayed ``PENDING`` until the beat sent it.
That is the mechanism behind the ``sent`` reminders found on the pilot for
appointments that no longer exist (DRF-1144).

Legacy YClients rows are unaffected by design: their ``yclients_record_id`` is
an integer string, which does not parse as a UUID, so
:func:`ayla_appointment_id_of` returns ``None`` for them and callers keep their
pre-existing behaviour.
"""

from __future__ import annotations

from uuid import UUID

from django.db.models import Q, QuerySet

from apps.booking.models import BookingReminder


def ayla_appointment_id_of(reminder: BookingReminder) -> UUID | None:
    """Return the Ayla appointment UUID this reminder belongs to, or ``None``.

    ``None`` means «this is a legacy YClients reminder» — there is no Ayla
    appointment behind it and the Ayla mirror must not be consulted.
    """
    if reminder.ayla_appointment_id is not None:
        return reminder.ayla_appointment_id
    raw = (reminder.yclients_record_id or "").strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except (AttributeError, TypeError, ValueError):
        # Integer YClients record id (or junk) — not an Ayla appointment.
        return None


def _appointment_id_spellings(appointment_id: UUID) -> list[str]:
    """String forms a writer may have stored in ``yclients_record_id``.

    ``yc_id`` is stringified from whatever Ayla's REST response carried, so we
    do not get to assume canonical lowercase-hyphenated form. Four spellings
    cover every representation ``str(UUID)`` / ``UUID.hex`` can produce.
    """
    canonical = str(appointment_id)
    return [canonical, canonical.upper(), appointment_id.hex, appointment_id.hex.upper()]


def reminders_for_appointment(appointment_id: UUID | str) -> QuerySet[BookingReminder]:
    """All reminders for an Ayla appointment, whichever column identifies it.

    Cross-tenant on purpose (``all_tenants``): the callers are eventbus
    consumers which run outside a tenant scope and have already authorised the
    envelope's tenant. ``appointment_id`` is Ayla's globally unique
    appointment UUID, so the filter cannot straddle tenants in practice.
    """
    if not isinstance(appointment_id, UUID):
        appointment_id = UUID(str(appointment_id))
    return BookingReminder.all_tenants.filter(
        Q(ayla_appointment_id=appointment_id)
        | Q(yclients_record_id__in=_appointment_id_spellings(appointment_id))
    )
