# Generated for PR #521 follow-up — adversarial blocker #3.
#
# Adds ``ScheduleChangeRequest.resolved_by_bot_user_id`` — a direct
# UUIDField holding the BotUser id of the decider. Without this column
# the audit trail of «who approved request X» is only reachable via
# the audit-row payload (opaque BotUser UUID embedded in JSON) which
# has shorter retention than the DB row.
#
# No data backfill — existing rows retain NULL (they're forensically
# already unrecoverable from the DB alone).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0002_m3_availability_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="schedulechangerequest",
            name="resolved_by_bot_user_id",
            field=models.UUIDField(
                blank=True,
                help_text="BotUser.id of the admin/owner who decided this "
                "request. Populated even when ``resolved_by`` FK is NULL.",
                null=True,
            ),
        ),
    ]
