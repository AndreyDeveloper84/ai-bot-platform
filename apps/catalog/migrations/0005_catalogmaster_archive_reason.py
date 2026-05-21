"""MM5 deactivation cascade — operator note captured at deactivation time.

Adds ``CatalogMaster.archive_reason``. Stored at deactivation; cleared
implicitly when the master is reactivated (we keep the audit row in
``AuditLog`` for forensic trail but blank the live column so the next
deactivation isn't shadowed by stale text).

Per ``docs/design/handoffs/2026-05-18-master-management-handoff.md``
§MM5 Step 3 — the «Причина (необязательно)» field on the final confirm
sheet.
"""

from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_master_invite_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="catalogmaster",
            name="archive_reason",
            field=models.TextField(
                blank=True,
                default="",
                help_text=(
                    "Owner-provided rationale for the most recent deactivation "
                    "(MM5 Step 3, «Причина»). Persisted as a forensic note next "
                    "to the AuditLog row; cleared on reactivate so the next "
                    "deactivation starts fresh."
                ),
            ),
        ),
    ]
