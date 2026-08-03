# AGENT_BOT_FIX_CANONICAL_RESCHEDULE_CONSUMER — canonical
# appointment.rescheduled version-aware ordering (AYLA-DEC-0022,
# AYLA-DEC-0036).
#
# Adds the local baseline RemoteBookingProxy tracks the highest
# canonical DER `version` it has applied against. NULL (the default
# for every existing row) means "no canonical version-tracked event
# has been applied yet" — proxies written only via legacy
# booking.created/booking.rescheduled stay NULL until the first
# appointment.rescheduled event bootstraps them.
#
# Migration safety: nullable column ADD — Postgres does not rewrite
# the table (no default to backfill), sub-ms ACCESS EXCLUSIVE lock.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0016_alter_remotebookingproxy_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="remotebookingproxy",
            name="last_applied_appointment_version",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text=(
                    "Highest canonical `appointment.rescheduled` DER "
                    "`version` applied to this proxy (AYLA-DEC-0022/0036 "
                    "version-aware ordering). NULL means no canonical "
                    "version-tracked event has been applied yet — proxies "
                    "created or updated only via legacy "
                    "booking.created/booking.rescheduled never set this "
                    "field. See "
                    "apps.eventbus.consumers.booking.handle_appointment_rescheduled_canonical "
                    "for the ordering state machine (bootstrap / skip / "
                    "apply / gap)."
                ),
            ),
        ),
    ]
