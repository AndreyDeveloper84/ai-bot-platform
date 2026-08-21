# Data migration — Memory Domain Contract §3.1 legacy backfill (Migration Plan Step 3).
#
# Deterministic, idempotent (every UPDATE guards on the target field still
# being NULL), and reversible (backwards NULLs exactly the four backfilled
# fields). Touches ONLY the Step-2 schema fields; legacy fields
# (source, ttl_days, deletion stamps) are read, never written. QuerySet
# .update() in batches — no save(), no model side effects. Historical
# models via apps.get_model, so no class-level constants are available —
# status literals are inlined from MemoryEntry.STATUS_*.

from datetime import timedelta

from django.db import migrations
from django.db.models import F


def backfill_step2_fields(apps, schema_editor):
    MemoryEntry = apps.get_model("identity", "MemoryEntry")

    # 1. status — soft_deleted_at wins over delete_requested_at. Rows that
    # already carry a status (re-run, or Step-4+ writers) are untouched.
    MemoryEntry.objects.filter(status__isnull=True, soft_deleted_at__isnull=False).update(
        status="deleted"
    )
    MemoryEntry.objects.filter(
        status__isnull=True,
        soft_deleted_at__isnull=True,
        delete_requested_at__isnull=False,
    ).update(status="deletion_pending")
    MemoryEntry.objects.filter(
        status__isnull=True,
        soft_deleted_at__isnull=True,
        delete_requested_at__isnull=True,
    ).update(status="active")

    # 2./3. effective_from / updated_at — fall back to created_at (never
    # last_used_at: usage tracking is not a state transition).
    MemoryEntry.objects.filter(effective_from__isnull=True).update(effective_from=F("created_at"))
    MemoryEntry.objects.filter(updated_at__isnull=True).update(updated_at=F("created_at"))

    # 4. expires_at — only where ttl_days is set; ttl_days stays the policy
    # input, nothing is invented for ttl-less rows. Batched per distinct
    # ttl value (portable interval arithmetic on Postgres AND SQLite).
    ttls = (
        MemoryEntry.objects.filter(expires_at__isnull=True, ttl_days__isnull=False)
        .values_list("ttl_days", flat=True)
        .distinct()
    )
    for ttl in ttls:
        MemoryEntry.objects.filter(expires_at__isnull=True, ttl_days=ttl).update(
            expires_at=F("created_at") + timedelta(days=ttl)
        )

    # NOT backfilled, by contract: provenance (no such field yet — see
    # report), consent_scope (NULL — never invented), purpose_tags ([] =
    # inherit), superseded_by / supersession_reason, source_event_id,
    # evidence_refs, derivation_method.


def revert_step2_fields(apps, schema_editor):
    MemoryEntry = apps.get_model("identity", "MemoryEntry")
    MemoryEntry.objects.update(status=None, effective_from=None, updated_at=None, expires_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0015_memoryentry_consent_scope_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_step2_fields, revert_step2_fields),
    ]
