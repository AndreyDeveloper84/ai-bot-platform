# Generated for customer cancel + reschedule state machine
# (customer-cancellation-reschedule-spec §2).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0005_bookingrequest_completed_at"),
    ]

    operations = [
        # Extend Status choices with the two interim states.
        # Existing values (confirmed / cancelled / rescheduled) preserved.
        migrations.AlterField(
            model_name="bookingrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("confirmed", "Confirmed"),
                    ("cancel_requested", "Cancel requested (within undo window)"),
                    ("reschedule_requested", "Reschedule requested (candidate stashed)"),
                    ("cancelled", "Cancelled"),
                    ("rescheduled", "Rescheduled (replaced)"),
                ],
                db_index=True,
                default="confirmed",
                help_text=(
                    "Lifecycle: confirmed (default) → cancel_requested → "
                    "cancelled OR reschedule_requested → rescheduled. "
                    "cancel_requested and reschedule_requested are interim "
                    "states reversible by the customer."
                ),
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="bookingrequest",
            name="cancel_requested_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "Set when state flips to CANCEL_REQUESTED. Used to "
                    "compute the undo window expiry (5s per customer-"
                    "cancellation-reschedule-spec §3.4 / Q-CR1)."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bookingrequest",
            name="rescheduled_from",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="rescheduled_into",
                to="booking.bookingrequest",
                help_text=(
                    "Self-FK: the old BookingRequest this row replaced "
                    "after a customer-initiated reschedule. PROTECT so "
                    "the audit linkage cannot be silently broken by "
                    "deleting the old row."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bookingrequest",
            name="reschedule_candidate",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Stashed candidate slot for an in-flight reschedule: "
                    "{'new_master_id', 'new_service_id', "
                    "'new_visit_at'}. Cleared on commit/abandon."
                ),
            ),
        ),
        # Drop the old confirmed-status uniqueness constraint and replace
        # it with one that also blocks cancel_requested rows from
        # double-booking the same (master, visit_at).
        migrations.RemoveConstraint(
            model_name="bookingrequest",
            name="booking_unique_master_confirmed_visit_at",
        ),
        migrations.AddConstraint(
            model_name="bookingrequest",
            constraint=models.UniqueConstraint(
                fields=["master", "visit_at"],
                condition=models.Q(
                    status__in=("confirmed", "cancel_requested", "reschedule_requested"),
                    visit_at__isnull=False,
                ),
                name="booking_unique_master_active_visit_at",
            ),
        ),
    ]
