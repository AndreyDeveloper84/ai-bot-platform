"""Master M0 onboarding: invite_token + invite_expires_at + linked_bot_user.

Adds the fields that back the claim-invite Mini App flow per
``docs/design/handoffs/2026-05-18-master-mobile-handoff.md`` §M0.

* ``invite_token`` — opaque UUID issued by MM2 invite-create endpoint
  (separate PR); carried in the Mini App deeplink as ``?token=<uuid>``.
  Cleared on accept (one-shot). Unique so a stale token can't collide.
* ``invite_expires_at`` — invited_at + 7d (Q-MM2). Past-expiry tokens
  return 410 from the onboarding endpoints.
* ``linked_bot_user`` — OneToOne to identity.BotUser. SET_NULL on
  BotUser delete so the master row survives for audit. A master has
  exactly one MAX/Telegram identity in Phase 1.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_backfill_master_invite_status"),
        ("identity", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogmaster",
            name="invite_token",
            field=models.UUIDField(
                blank=True,
                db_index=True,
                help_text=(
                    "One-shot opaque token from MM2 invite-create. Cleared on "
                    "accept; uniqueness enforced so a stale token can't collide."
                ),
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="catalogmaster",
            name="invite_expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="invited_at + 7d (Q-MM2). Past-expiry tokens 410.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="catalogmaster",
            name="linked_bot_user",
            field=models.OneToOneField(
                blank=True,
                help_text=(
                    "MAX/Telegram BotUser this master signs in with. "
                    "SET_NULL on BotUser delete preserves the master audit trail."
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="master_identity",
                to="identity.botuser",
            ),
        ),
    ]
