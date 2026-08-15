"""Per-appointment announcement claims on the booking mirror (DRF-1069).

Two nullable timestamps — additive ``ADD COLUMN NULL`` on both
backends, no table rewrite, no volatile default, so the DDL itself is
metadata-only and safe on a live table.

### Why the rows that already exist are claimed, not left NULL

NULL reads as «nobody has announced this appointment yet», and the
consumer acts on that: the next ``booking.created`` for such a row —
a re-delivery, a backfill replay, an Ayla-side resend under a fresh
``event_id`` — would page the salon with «🆕 Новая запись» about an
appointment from before the deploy, possibly one that has already
happened. The pilot has live mirror rows, so this is not theoretical.

So the migration takes the claim on their behalf, stamping both slots
with the row's own ``created_at``: «this appointment was settled
before the claim existed; it is not ours to announce». The timestamp
is deliberately the row's birth rather than the deploy instant — it
says the claim is as old as the row, which is exactly the fact being
recorded.

The cost is bounded and one-sided: a booking whose mirror row was
written in the minutes before deploy but whose ``booking.created``
arrives after it is never announced. That is one event's worth of the
*old* behaviour, in the direction the whole feature errs (idempotency
beats completeness — a missed announcement is cheaper than a false
one), and it self-heals with the next booking.

The reverse is a no-op: unapplying drops the columns anyway.
"""

from django.db import migrations, models
from django.db.models import F


def claim_pre_migration_rows(apps, schema_editor) -> None:
    """Stamp both announcement slots on every pre-existing mirror row.

    See the module docstring. Unconditional ``UPDATE`` over one table
    — the columns were added NULL one operation ago, so every row is
    by definition pre-migration and the filter would be redundant.
    """

    RemoteBookingProxy = apps.get_model("booking", "RemoteBookingProxy")
    RemoteBookingProxy.objects.update(
        salon_notified_at=F("created_at"),
        client_notified_at=F("created_at"),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0017_remotebookingproxy_last_applied_appointment_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="remotebookingproxy",
            name="salon_notified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="When a handler claimed the one-shot salon "
                "announcement for this appointment (DRF-1030 / DRF-1069). NULL "
                "= nobody on the salon side has been told yet. Set inside the "
                "ingest transaction, before the after-commit send: a rolled-back "
                "ingest releases the claim, a re-delivered event finds it taken. "
                "Records the claim, not delivery — the send itself is "
                "best-effort and may still find no reachable recipient.",
            ),
        ),
        migrations.AddField(
            model_name="remotebookingproxy",
            name="client_notified_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Same claim, for the client-facing «вы записаны» "
                "confirmation (DRF-1066), taken by whichever handler decides "
                "to send it. Stays NULL for a booking made in the bot's own "
                "dialog: there ``execute_confirm`` already answered in chat, "
                "this channel deliberately says nothing, and the chat-origin "
                "marker — not this column — is what keeps it silent.",
            ),
        ),
        migrations.RunPython(
            claim_pre_migration_rows,
            migrations.RunPython.noop,
            elidable=False,
        ),
    ]
