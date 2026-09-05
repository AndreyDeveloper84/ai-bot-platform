# DRF-1494 — a field that records when the catalog sync actually ran.
#
# `last_catalog_sync_at` (0003) is an upstream content watermark, not a run
# timestamp, so its age could not distinguish a static catalog from a dead
# sync. Twelve pilot days passed on that ambiguity. This column is the
# wall-clock of the last successful run and is what the staleness alarm reads.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0012_alter_tenantstaff_unique_together_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="last_catalog_sync_ok_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Wall-clock of the last SUCCESSFUL catalog sync run for this tenant. NULL → never synced. Age above CATALOG_SYNC_STALE_AFTER_SECONDS pages the on-call channel (apps.catalog.tasks.alert_stale_catalog_sync).",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="tenant",
            name="last_catalog_sync_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Upstream content watermark — max(updated_at) over the rows the last successful pull returned. NOT a run timestamp: a static catalog freezes this value on a healthy contour. Use last_catalog_sync_ok_at to judge freshness of the SYNC.",
                null=True,
            ),
        ),
    ]
