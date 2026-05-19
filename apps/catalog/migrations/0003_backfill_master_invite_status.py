"""Backfill CatalogMaster.invite_status for is_active=False legacy rows."""

from __future__ import annotations

from django.db import migrations


def backfill_cancelled_status(apps, schema_editor):
    CatalogMaster = apps.get_model("catalog", "CatalogMaster")
    CatalogMaster.objects.filter(is_active=False).update(invite_status="cancelled")


def reverse_noop(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_masterservice_catalogmaster_archived_at_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_cancelled_status, reverse_noop),
    ]
