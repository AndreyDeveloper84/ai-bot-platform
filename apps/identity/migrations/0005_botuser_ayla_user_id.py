"""Bridge column: BotUser.ayla_user_id → Ayla djangoproject users.User.id.

Unblocks Gamma's #442 booking consumer per cross-stream handoff 2026-05-23:
the consumer needs to resolve envelope.user_id (canonical Ayla User UUID
from event-contract.md §2 envelope) to a local BotUser row in order to
write BookingReminder rows + update Conversation.last_booking_at.

Pure AddField — nullable, indexed, no backfill. Existing BotUsers (created
pre-bridge from channel events alone) get NULL; the consumer populates the
field opportunistically when a booking.* event first observes the linkage.

Per ADR-0009 §Hard rule #1: NOT a Django FK because canonical User lives
in Ayla djangoproject (separate DB / no shared tables).
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0004_userpreferences"),
    ]

    operations = [
        migrations.AddField(
            model_name="botuser",
            name="ayla_user_id",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text=(
                    "Canonical Ayla User UUID (from Ayla djangoproject "
                    "users.User.id). NULL until the bridge is established "
                    "— typically populated when the booking event consumer "
                    "(Gamma #442) sees this channel-user for the first "
                    "time. Used by the consumer to resolve envelope.user_id "
                    "→ BotUser for BookingReminder + Conversation update."
                ),
                null=True,
            ),
        ),
    ]
