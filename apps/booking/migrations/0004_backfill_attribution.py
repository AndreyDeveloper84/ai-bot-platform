"""Backfill attribution_metadata.actor_type on pre-4a rows (idempotent)."""

from __future__ import annotations

from django.db import migrations


def backfill_actor_type(apps, schema_editor):
    BookingRequest = apps.get_model("booking", "BookingRequest")
    for row in BookingRequest.objects.iterator():
        meta = row.attribution_metadata or {}
        if "actor_type" not in meta:
            meta["actor_type"] = "system"
            meta.setdefault("backfilled", True)
            BookingRequest.objects.filter(pk=row.pk).update(
                attribution_metadata=meta,
                billing_reason=row.billing_reason or "backfilled: pre-4a row",
            )


def reverse_noop(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0003_bookingrequest_ai_assist_score_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_actor_type, reverse_noop),
    ]
