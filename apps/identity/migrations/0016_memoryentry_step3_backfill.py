# Data migration — Memory Domain Contract §3.1 legacy backfill (Migration Plan Step 3).
#
# Deterministic, idempotent (every UPDATE guards on the target field still
# being NULL), and reversible. Touches ONLY the Step-2 schema fields; legacy
# fields (source, ttl_days, deletion stamps) are read, never written. QuerySet
# .update() in batches — no save(), no model side effects. Historical
# models via apps.get_model, so no class-level constants are available —
# status literals are inlined from MemoryEntry.STATUS_*.
#
# ## Reverse scope (DRF-1264)
#
# The reverse used to be a filter-less
# `update(status=None, effective_from=None, updated_at=None, expires_at=None)`
# over the WHOLE table. One rollback therefore erased the lifecycle of every
# row, including rows written long after this migration ran — a supersession,
# a deletion, a zone transition, an extended TTL. On the pilot the table holds
# zero rows and that costs nothing; once it fills, it costs the state of living
# people's memory, silently, in a step nobody reviews at 3am.
#
# It is now mirror-scoped: each field is reverted ONLY on rows where it still
# holds exactly the value the forward rule computes for that row. Everything
# else is post-migration information and survives.
#
# Marking the pair irreversible was the alternative, and it was rejected: 0015
# is pure additive schema, and being able to roll back to it during a bad
# deploy is a real operational capability. A reverse that provably touches
# only what forward wrote keeps that capability without the blast radius.
#
# Residual ambiguity, stated plainly: a row whose lifecycle a later writer set
# to exactly the backfill's value is indistinguishable from a backfilled row
# and reverts with it. That is the same trade migration 0018 already documents
# for `provenance` (:17-23) — «rolling back a data migration is a return to the
# pre-step state». It cannot destroy information, because the value it removes
# is byte-identical to the one forward would put back.

from datetime import timedelta

from django.db import migrations
from django.db.models import DateTimeField, ExpressionWrapper, F


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
    """NULL exactly what :func:`backfill_step2_fields` could have written.

    Every filter below is the forward rule, read backwards: revert the field
    only where it still equals the value forward computes from THIS row's own
    legacy columns. A row carrying anything else — a supersession, a deletion
    stamped by `memory_deleter`, a zone transition, a re-timed expiry — keeps
    it (DRF-1264).
    """

    MemoryEntry = apps.get_model("identity", "MemoryEntry")

    # 1. status — the three forward mappings, each re-checked against the
    # deletion stamps the row still carries. `superseded` / `expired` are
    # never produced by the backfill, so they are never removed by it either.
    MemoryEntry.objects.filter(status="deleted", soft_deleted_at__isnull=False).update(status=None)
    MemoryEntry.objects.filter(
        status="deletion_pending",
        soft_deleted_at__isnull=True,
        delete_requested_at__isnull=False,
    ).update(status=None)
    MemoryEntry.objects.filter(
        status="active",
        soft_deleted_at__isnull=True,
        delete_requested_at__isnull=True,
    ).update(status=None)

    # 2./3. effective_from / updated_at — forward set both to created_at.
    MemoryEntry.objects.filter(effective_from=F("created_at")).update(effective_from=None)
    MemoryEntry.objects.filter(updated_at=F("created_at")).update(updated_at=None)

    # 4. expires_at — forward set it to created_at + ttl_days, per distinct
    # ttl value. Same batching, same arithmetic, now as a guard instead of an
    # assignment: an expiry that no longer matches was set by something else.
    ttls = (
        MemoryEntry.objects.filter(expires_at__isnull=False, ttl_days__isnull=False)
        .values_list("ttl_days", flat=True)
        .distinct()
    )
    for ttl in ttls:
        MemoryEntry.objects.filter(ttl_days=ttl).annotate(
            _backfilled_expiry=ExpressionWrapper(
                F("created_at") + timedelta(days=ttl), output_field=DateTimeField()
            )
        ).filter(expires_at=F("_backfilled_expiry")).update(expires_at=None)


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0015_memoryentry_consent_scope_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_step2_fields, revert_step2_fields),
    ]
