"""Add ``Conversation.last_booking_at`` for Gamma #442 booking consumer.

The booking consumer in Gamma's stream populates this field from the
``data.start_at`` of every ``booking.confirmed`` event per
``docs/architecture/event-contract.md`` §3.1.4. AI grounding reads it to
answer «когда вы у нас были последний раз» without a cross-repo REST hop
to Ayla on every turn.

Pure additive, nullable, no backfill — existing rows stay NULL until
the next ``booking.confirmed`` lands for that conversation's bot_user.
Index is created automatically from ``db_index=True``; populating
NULL on existing rows is a no-op so the migration is safe to run on a
populated table with zero downtime.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0009_aidraft"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="last_booking_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text=(
                    "Tenant-local datetime of the customer's most recent confirmed "
                    "booking start. Set from booking.confirmed event data.start_at "
                    "per docs/architecture/event-contract.md §3.1.4. Used by AI "
                    "grounding to anchor «когда вы у нас были последний раз»."
                ),
                null=True,
            ),
        ),
    ]
