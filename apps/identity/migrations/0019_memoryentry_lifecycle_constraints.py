# DB CHECK constraints for the §3.1 lifecycle invariants (DRF-1263).
#
# Until now two invariants of the Memory Domain Contract lived in exactly one
# Python `if` each, in one service:
#
#   CHECK 4  status='deleted' ⇔ soft_deleted_at IS NOT NULL
#            Nothing enforced it, and `memory_deleter` did not write `status`
#            at all — so every deletion after migration 0016 produced
#            `status='active' AND soft_deleted_at IS NOT NULL`. Contract-
#            illegal, and it puts the forgotten fact back in front of the
#            person as soon as a read path filters on `status` (step 5).
#
#   CHECK 5  source='explicit' → provenance IS NOT NULL
#            MDC §3.1 allows exactly two provenance values. With `provenance`
#            nullable next to the legacy `source` column, NULL had become a
#            de-facto third value that the contract does not know.
#
# ## Deliberate narrowings (both documented, neither accidental)
#
# * CHECK 4 exempts `status IS NULL`. `status` is nullable by design until the
#   Step-4 writer stamps every row (inferred/signal rows are unstamped on
#   purpose), and migration 0016 exists precisely to fill NULLs — a constraint
#   forbidding NULL would make the state 0016 migrates FROM unrepresentable.
#   What it does catch is the whole of the DRF-1263 defect: a row that CLAIMS
#   a lifecycle status contradicting its own tombstone.
# * CHECK 5 says nothing about inferred/signal rows: they keep
#   `provenance=NULL` until the proposal flow confirms them (OR-MEM-3 — silent
#   promotion to `user_confirmed_inference` is forbidden).
#
# ## Backfill before constraint
#
# Both constraints are preceded by a repair UPDATE for rows already in the
# forbidden state (the pilot has zero MemoryEntry rows — this is for dev and
# staging databases, where the `status='active' + tombstone` rows minted since
# 0016 do exist). Same shape as migration 0007: repair, then NOT VALID, then
# VALIDATE in a separate statement so a populated table is not locked for the
# whole scan.
#
# Postgres-only, exactly like the three constraints in 0007: the NOT VALID /
# VALIDATE split has no SQLite equivalent. Local SQLite runs no-op, CI and
# production run Postgres, and the constraint tests skip on SQLite.

from django.db import migrations

_CHECK_4 = "memory_entry_deleted_status_matches_tombstone"
_CHECK_5 = "memory_entry_explicit_requires_provenance"


def _add_lifecycle_constraints(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    # Repair 1 — rows soft-deleted by the pre-DRF-1263 deleter. `updated_at`
    # is moved to the tombstone moment too: deletion was a state transition
    # and this row never recorded it. COALESCE keeps a row that already
    # carries a later `updated_at` (nothing is moved backwards).
    schema_editor.execute(
        "UPDATE identity_memoryentry "
        "SET status = 'deleted', "
        "    updated_at = GREATEST(COALESCE(updated_at, soft_deleted_at), soft_deleted_at) "
        "WHERE soft_deleted_at IS NOT NULL "
        "AND status IS NOT NULL AND status <> 'deleted'"
    )
    # Repair 2 — the mirror lie: `status='deleted'` with no tombstone. Such a
    # row was never produced by any code path in this repo; if one exists it
    # is hand-made, and «deleted» without a tombstone is the claim we trust
    # least. Demote to the lifecycle the timestamps actually support.
    schema_editor.execute(
        "UPDATE identity_memoryentry "
        "SET status = CASE WHEN delete_requested_at IS NOT NULL "
        "                  THEN 'deletion_pending' ELSE 'active' END "
        "WHERE status = 'deleted' AND soft_deleted_at IS NULL"
    )

    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        f"ADD CONSTRAINT {_CHECK_4} CHECK ("
        "  status IS NULL "
        "  OR (status = 'deleted' AND soft_deleted_at IS NOT NULL) "
        "  OR (status <> 'deleted' AND soft_deleted_at IS NULL)"
        ") NOT VALID"
    )
    schema_editor.execute(f"ALTER TABLE identity_memoryentry VALIDATE CONSTRAINT {_CHECK_4}")

    # Repair 3 — explicit rows the 0018 backfill could not reach (written
    # between 0018 and this migration by anything other than memory_writer).
    # `user_stated` is the only value 0018 was allowed to assign to an
    # explicit row; `user_confirmed_inference` is never inferred here.
    schema_editor.execute(
        "UPDATE identity_memoryentry "
        "SET provenance = 'user_stated' "
        "WHERE source = 'explicit' AND provenance IS NULL"
    )

    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        f"ADD CONSTRAINT {_CHECK_5} CHECK ("
        "  source <> 'explicit' OR provenance IS NOT NULL"
        ") NOT VALID"
    )
    schema_editor.execute(f"ALTER TABLE identity_memoryentry VALIDATE CONSTRAINT {_CHECK_5}")


def _drop_lifecycle_constraints(apps, schema_editor):
    """Reverse — drop both constraints. Data is NOT un-repaired.

    Dropping a constraint is always safe; re-NULLing `provenance` or rewinding
    `status` would destroy information (see DRF-1264 for why a blanket reverse
    UPDATE is the wrong instinct). Postgres-only, same reason as forward.
    """
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"ALTER TABLE identity_memoryentry DROP CONSTRAINT IF EXISTS {_CHECK_5}")
    schema_editor.execute(f"ALTER TABLE identity_memoryentry DROP CONSTRAINT IF EXISTS {_CHECK_4}")


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0018_memoryentry_provenance_backfill"),
    ]

    operations = [
        migrations.RunPython(_add_lifecycle_constraints, _drop_lifecycle_constraints),
    ]
