"""Add ``STALE_DROPPED`` to BookingReminder.Status (P0 PRE_PILOT).

Send-time booking-state re-check invariant per founder pilot-scope
sequence #2. Dispatch now re-fetches ``BookingRequest`` state before
sending; if booking changed между schedule + dispatch (cancelled,
rescheduled, completed), reminder transitions к this new terminal
status instead of being delivered.

Distinct from ``CANCELLED`` (which means «B2 webhook said cancelled»)
— ``STALE_DROPPED`` is post-hoc detection that the booking became
invalid silently. Separate enum value lets ops query stale-rate
spikes по master / service / time-of-day без conflating with
explicit cancel signals.

Pure choices-list amendment — no data migration, no constraints,
``max_length=16`` already accommodates 13-char ``stale_dropped``.
Backward-compat: existing rows стars all use other status values;
schema change is additive only.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0012_remote_booking_proxy_and_ayla_appointment_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="bookingreminder",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending dispatch"),
                    ("sent_no_reply", "Sent, no client reply yet"),
                    ("sent", "Sent (T-2h, no buttons)"),
                    ("confirmed", "Client confirmed"),
                    ("reschedule", "Client requested reschedule"),
                    ("cancelled", "Client / admin cancelled"),
                    ("escalated", "Escalated to human operator"),
                    ("failed", "Send error"),
                    ("stale_dropped", "Dropped at dispatch (booking changed)"),
                ],
                db_index=True,
                default="pending",
                max_length=16,
            ),
        ),
    ]
