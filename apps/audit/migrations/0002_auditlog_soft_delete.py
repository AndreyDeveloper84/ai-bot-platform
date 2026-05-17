"""Soft-delete columns on AuditLog (DRF-851 / Phase 1 / PI1).

When ``AUDIT_LOG_RETENTION_MODE="soft"`` the cleanup task flips
``is_archived=True`` + stamps ``archived_at`` instead of DELETE-ing.
Default behaviour (mode="hard") still issues DELETE — these fields
remain at their defaults and the new index is cold.

Index is on ``(is_archived, archived_at)`` to support a future
hard-delete-archived sweep without a full-table scan.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="is_archived",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Soft-delete flag flipped by cleanup task in 'soft' "
                    "retention mode. Default manager hides these rows; use "
                    "the 'archived' or 'all_tenants' manager to query them."
                ),
            ),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="archived_at",
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text=(
                    "Timestamp when ``is_archived`` was flipped to True. "
                    "NULL for live rows. A future task may use this to "
                    "hard-delete archived rows past a longer cutoff."
                ),
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["is_archived", "archived_at"],
                name="audit_auditl_is_arch_idx",
            ),
        ),
    ]
