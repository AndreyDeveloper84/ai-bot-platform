"""Post-visit feedback fields + check constraint (Phase 4 / F5).

Adds the four columns the customer Mini App needs to record the F5
rating form: rating + comment + feedback_at (idempotency anchor) +
feedback_prompt_sent_at (gates the Phase 4b T+1h DM dispatcher).

Note on COMPLETED: this repo treats `status='confirmed' AND
visit_at < now()` as "post-visit"; an explicit COMPLETED status lands
when the YClients post-visit webhook ports over. No backfill needed —
rating is NULL for every existing row.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0004_backfill_attribution"),
    ]

    operations = [
        migrations.AddField(
            model_name="bookingrequest",
            name="rating",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text=(
                    "Customer rating 1-5. NULL until they submit the F5 form. "
                    "Values <= 3 fire the handoff flow."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="bookingrequest",
            name="feedback_comment",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Optional free-text comment from the F5 form (≤500 chars enforced at the API)."
                ),
            ),
        ),
        migrations.AddField(
            model_name="bookingrequest",
            name="feedback_at",
            field=models.DateTimeField(
                blank=True,
                help_text=(
                    "Wall-clock when the rating was submitted. Idempotency anchor: "
                    "submit_feedback raises 'already_rated' when this is non-NULL."
                ),
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="bookingrequest",
            name="feedback_prompt_sent_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text=(
                    "When the post-visit DM prompt was dispatched. NULL = not yet sent. "
                    "Phase 4b will scan WHERE NULL and visit_at < now()-1h."
                ),
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="bookingrequest",
            constraint=models.CheckConstraint(
                condition=models.Q(rating__isnull=True) | models.Q(rating__gte=1, rating__lte=5),
                name="booking_rating_in_range_or_null",
            ),
        ),
    ]
