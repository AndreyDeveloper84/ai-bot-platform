"""Mirror column: BotUser.avatar_url ← Ayla user.profile.updated event.

Unblocks Gamma's #446 user.profile.updated consumer per cross-stream
handoff 2026-05-25: consumer needs a destination column to mirror the
avatar_url from Ayla per event-contract.md §3.12.

Per ADR-0009 §Hard rule #1 — Ayla owns the canonical value; this column
is a mirror for UI rendering only (mini app, conversation thread,
master-side internal-chat). Refresh path = REST GET
/api/v1/users/{ayla_user_id} on event receipt OR re-sync from event
payload.

Pure AddField — URLField, blank-allowed, empty-string default, no
backfill needed. Existing BotUsers (created pre-bridge) get the empty-
string default; the consumer populates the field on the first
user.profile.updated event for each user.

NOT NULL with empty-string default chosen over nullable because the
field is read in UI render paths — None vs "" branches add noise. The
empty string is the «no avatar» semantic; consumer treats it as such.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0005_botuser_ayla_user_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="botuser",
            name="avatar_url",
            field=models.URLField(
                blank=True,
                default="",
                max_length=500,
                help_text=(
                    "Avatar URL mirrored from Ayla user.profile.updated event "
                    "(per event-contract.md §3.12). Used by bot-platform for "
                    "UI rendering (mini app, conversation thread). NOT a "
                    "canonical store — Ayla djangoproject is. Refresh via "
                    "REST GET /api/v1/users/{ayla_user_id} on event receipt. "
                    "Empty string default for backward compat."
                ),
            ),
        ),
    ]
