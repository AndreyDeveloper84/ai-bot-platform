"""Mirror columns: ClientProfile review fields ← Ayla review.created event.

Unblocks Gamma's #446 review.created consumer per cross-stream handoff
2026-05-25: consumer needs destination columns to mirror the latest
review rating + timestamp + low-rating flag + sentiment derivative from
Ayla per event-contract.md §3.13.

Per ADR-0009 §Hard rule #1 — Ayla owns the canonical Review state.
These columns are bot-platform's denormalised projection for:
  - admin queue surfacing («clients with recent low rating»)
  - sentiment analytics rollups
  - proactive nudge logic (e.g. apology flow after low rating)

Refresh path: consumer writes all 4 fields atomically when a
review.created event arrives for an existing ClientProfile.

Pure AddField — all 4 fields are either nullable (IntegerField,
DateTimeField) or have defaults (BooleanField=False, FloatField=0.0).
No backfill needed; existing ClientProfile rows get the defaults.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0008_red_zone_db_security"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientprofile",
            name="last_review_rating",
            field=models.IntegerField(
                blank=True,
                null=True,
                help_text=(
                    "Rating (1-5) of this client's most recent review, "
                    "from review.created event payload. NULL = no "
                    "reviews yet."
                ),
            ),
        ),
        migrations.AddField(
            model_name="clientprofile",
            name="last_review_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text=(
                    "Timestamp when the most recent review.created "
                    "event was emitted (Ayla domain). NULL = no "
                    "reviews yet."
                ),
            ),
        ),
        migrations.AddField(
            model_name="clientprofile",
            name="low_rating_flag",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "True if the most recent review had rating <= 2. "
                    "Triggers admin queue «review attention» surfacing."
                ),
            ),
        ),
        migrations.AddField(
            model_name="clientprofile",
            name="sentiment_score",
            field=models.FloatField(
                default=0.0,
                help_text=(
                    "Derived from last_review_rating: 5→1.0, 4→0.5, "
                    "3→0.0, 2→-0.5, 1→-1.0. Bonus field for future "
                    "analytics. Stays 0.0 for clients with no reviews yet."
                ),
            ),
        ),
    ]
