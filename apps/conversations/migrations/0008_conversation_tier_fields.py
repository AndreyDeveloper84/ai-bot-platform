"""Master M6 — add tier-locking fields to Conversation (PR M6.1).

Adds 5 new columns to ``conversations_conversation`` so the M6
``POST /api/v1/master/conversations/<id>/promote`` endpoint can transition
a conversation into a HUMAN_LOCKED state with attribution + reason
audit. Same row also surfaces the «administrator working» banner in
the master detail GET response.

Columns:
* ``tier`` — one of ai_continuity|human_supervised|human_locked. Default
  ``ai_continuity`` so existing rows pass into the most-permissive
  bucket (which is also their current behaviour: bot replies freely).
* ``tier_reason_class`` — complaint|financial|medical|other; nullable.
* ``tier_locked_at`` — when the conversation entered HUMAN_LOCKED.
* ``tier_locked_by_master_id`` — FK to CatalogMaster (nullable). The
  master who escalated. Cleared if/when an admin downgrades the tier
  in a separate PR — out of scope here.
* ``tier_locked_reason_text`` — opt free text (≤ 500 chars). Forensic
  context for the audit row.
* ``last_read_by_master_at`` — set by the M6 ``mark-read`` endpoint.
  Used to compute the «unread since master last looked» count for the
  list endpoint badge (out of scope this PR — field is shipped so the
  list/detail can read it from day one).

The new fields are all nullable / defaulted so the migration is
zero-downtime on a populated table.
"""

from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("conversations", "0007_conversation_state_check"),
        # Master FK target lives in catalog.
        ("catalog", "0005_catalogmaster_archive_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="conversation",
            name="tier",
            field=models.CharField(
                choices=[
                    ("ai_continuity", "AI Continuity"),
                    ("human_supervised", "Human Supervised"),
                    ("human_locked", "Human Locked"),
                ],
                db_index=True,
                default="ai_continuity",
                help_text=(
                    "Ownership tier. HUMAN_LOCKED disables master "
                    "compose + AI auto-reply; only admin/owner downgrades."
                ),
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="tier_reason_class",
            field=models.CharField(
                max_length=32,
                blank=True,
                default="",
                help_text=(
                    "Classification when tier is locked: "
                    "complaint|financial|medical|other. Empty otherwise."
                ),
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="tier_locked_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text="When the conversation entered HUMAN_LOCKED.",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="tier_locked_by_master",
            field=models.ForeignKey(
                "catalog.CatalogMaster",
                on_delete=django.db.models.deletion.SET_NULL,
                null=True,
                blank=True,
                related_name="conversations_locked",
                help_text="Master who promoted the conversation to HUMAN_LOCKED.",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="tier_locked_reason_text",
            field=models.CharField(
                max_length=500,
                blank=True,
                default="",
                help_text="Free-form forensic context. Never surfaced as PII.",
            ),
        ),
        migrations.AddField(
            model_name="conversation",
            name="last_read_by_master_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text=(
                    "Last time the assigned master tapped mark-read on this "
                    "conversation. Used to compute the per-master unread count."
                ),
            ),
        ),
    ]
