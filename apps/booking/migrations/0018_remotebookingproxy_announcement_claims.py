"""Per-appointment announcement claims on the booking mirror (DRF-1069).

Two nullable timestamps — additive ``ADD COLUMN NULL`` on both
backends, no table rewrite, no default backfill. Existing rows keep
NULL, which reads as «never announced»: the first ``booking.created``
re-delivery after deploy would announce an old appointment once. That
is deliberate and bounded — Ayla does not re-deliver settled events,
and the alternative (backfilling «already announced» for rows nobody
was ever told about) would preserve exactly the silence this ticket
exists to end.
"""

from django.db import migrations, models


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
                "confirmation (DRF-1066). NULL also stays NULL for a booking "
                "made in the dialog: there ``execute_confirm`` already answered "
                "in chat and this channel deliberately says nothing.",
            ),
        ),
    ]
