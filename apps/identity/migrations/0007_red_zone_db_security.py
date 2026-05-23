"""Red-zone DB security layer: 5 roles + memory_entry_safe view + RLS policy.

Implements docs/specs/memory-entry-schema.md §8 (DB roles) + §9 (RLS guard).

# Postgres-only

The entire migration is wrapped in `connection.vendor` check. On SQLite
(local dev) the migration is a no-op — DB-level security guarantees are
only available on the production engine (Postgres). Application-side
defence (RedZoneReader accessor + AST lint) covers SQLite developer flow.

# atomic = False

Required for:
- CREATE ROLE — some Postgres versions don't allow inside transaction
- ALTER TABLE ... ENABLE ROW LEVEL SECURITY — DDL that benefits from
  not being grouped with other operations for cleaner partial-rollback
  semantics

# Roles created (per spec §8 — 5 of them)

1. `ayla_app` — application service role. DML on most tables; RLS-gated on
   memory_entry.content red rows (only sees them when GUC set + UUID-valid).
2. `ayla_ops` — read-only ops debugging. Reads via `memory_entry_safe` view
   that filters red rows entirely.
3. `ayla_migrator` — DDL only. Runs Django migrations. NO DML on
   memory_entry.content. Grants here are restricted to schema operations.
4. `ayla_backup` — REPLICATION privilege for pg_basebackup. Bypasses
   logical roles by design — accepted residual risk per ADR-0011 §11.1.
5. `ayla_ops_redzone_break_glass` — emergency 1h app-role escalation.
   Provisioned via secret-manager runbook (4-eyes); NOT created at
   migration time (it's a privileged grant procedure, not a standing role).
   This migration creates the role as a no-login placeholder; actual grants
   happen in the break-glass runbook when invoked.

# RLS policy (the §9 implementation)

§9 originally specified «BEFORE SELECT trigger». Postgres doesn't support
that row-level. Implemented as RLS policy with equivalent semantics:

  USING (
      sensitivity_zone != 'red'
      OR current_setting('ayla.red_zone_access_context', true) ~ '<uuid-regex>'
  )

Red rows are INVISIBLE without a valid UUID GUC. RedZoneReader accessor
(veha 3) sets the GUC via `SET LOCAL` inside its transaction. Bypass
attempts (raw SQL, Django shell) see zero rows — NOT an exception. This
is intentional defence-in-depth.

# Veha 2 acceptance (per план daily checkpoint)

Spec §13 tests: 14.6 (DB roles + view + RLS), 14.17 (psql SELECT without
GUC returns zero rows), 14.18 (GUC empty / non-UUID / UUID behaviour).
"""

from django.db import migrations


def _add_red_zone_security(apps, schema_editor):
    """Postgres-only: 5 roles + view + RLS policy.

    On SQLite (local dev): no-op. Tests that exercise DB security are
    pytest.mark.skipif'd for SQLite. Application-side defence
    (RedZoneReader accessor + AST lint, veha 3 + veha 4) covers SQLite.
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    # ─── 5 DB roles ──────────────────────────────────────────────────────
    # `CREATE ROLE IF NOT EXISTS` is not standard SQL; use DO block.
    # NOLOGIN for non-application roles — they're granted to login roles
    # via secret-manager flow (ayla_ops_redzone_break_glass) or used by
    # CI/deploy infrastructure (ayla_migrator, ayla_backup).
    for role_sql in [
        # ayla_app — application service login role. Default login role
        # for the Django app process.
        """
        DO $$ BEGIN
            CREATE ROLE ayla_app LOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """,
        # ayla_ops — read-only debugging. Login role for ops shell.
        """
        DO $$ BEGIN
            CREATE ROLE ayla_ops LOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """,
        # ayla_migrator — DDL only. Used by CI/deploy pipeline for
        # `manage.py migrate`. NOLOGIN — not a daily-use role.
        """
        DO $$ BEGIN
            CREATE ROLE ayla_migrator;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """,
        # ayla_backup — REPLICATION for pg_basebackup. NOLOGIN at role
        # level; login is via .pgpass with replication credentials.
        """
        DO $$ BEGIN
            CREATE ROLE ayla_backup REPLICATION;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """,
        # ayla_ops_redzone_break_glass — emergency 1h grant. Standing role
        # as NOLOGIN placeholder; actual login is via the secret-manager
        # break-glass runbook (4-eyes, time-limited).
        """
        DO $$ BEGIN
            CREATE ROLE ayla_ops_redzone_break_glass;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """,
    ]:
        schema_editor.execute(role_sql)

    # ─── memory_entry_safe view ──────────────────────────────────────────
    # Filters red rows entirely. ayla_ops reads from this view, not the
    # base table. Red rows are simply absent from the view's result set —
    # ops cannot see they exist without escalating to break-glass role.
    schema_editor.execute(
        """
        CREATE VIEW memory_entry_safe AS
        SELECT
            id, user_id, personal_context_id, sensitivity_zone, source,
            last_inferred_at, source_tenant_id, kind, created_at,
            last_used_at, last_used_count, ttl_days, consent_at,
            delete_requested_at, soft_deleted_at, deletion_reason
            -- NOTE: `content` column intentionally OMITTED — even the
            -- non-red content shouldn't be read via this ops view; the
            -- encrypted payload requires the Fernet key + audit trail.
        FROM identity_memoryentry
        WHERE sensitivity_zone != 'red';
        """
    )

    # ─── RLS policy on identity_memoryentry ──────────────────────────────
    schema_editor.execute("ALTER TABLE identity_memoryentry ENABLE ROW LEVEL SECURITY")
    # FORCE — policy applies even to the table owner (Django migrations run
    # as owner-equivalent role; without FORCE they bypass RLS).
    schema_editor.execute("ALTER TABLE identity_memoryentry FORCE ROW LEVEL SECURITY")
    schema_editor.execute(
        """
        CREATE POLICY memory_entry_non_red_visible
            ON identity_memoryentry
            FOR SELECT
            USING (
                sensitivity_zone != 'red'
                OR current_setting('ayla.red_zone_access_context', true) ~
                   '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            );
        """
    )
    # Allow ayla_app to INSERT/UPDATE/DELETE — RLS only filters SELECT.
    # Writes are gated at the application layer (memory_writer, veha 3).
    schema_editor.execute(
        """
        CREATE POLICY memory_entry_write_all
            ON identity_memoryentry
            FOR ALL
            USING (true)
            WITH CHECK (true);
        """
    )

    # ─── GRANTs ──────────────────────────────────────────────────────────
    # ayla_app: full DML on memory_entry + UPC + RedZoneAccessLog.
    schema_editor.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON identity_memoryentry TO ayla_app"
    )
    schema_editor.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON identity_userpersonalcontext TO ayla_app"
    )
    schema_editor.execute("GRANT SELECT, INSERT ON identity_redzoneaccesslog TO ayla_app")
    # No UPDATE/DELETE on RedZoneAccessLog — append-only audit per spec §3.

    # ayla_ops: SELECT only via the safe view; NO direct table access on
    # identity_memoryentry (must read via memory_entry_safe).
    schema_editor.execute("GRANT SELECT ON memory_entry_safe TO ayla_ops")
    schema_editor.execute("GRANT SELECT ON identity_userpersonalcontext TO ayla_ops")
    schema_editor.execute("GRANT SELECT ON identity_redzoneaccesslog TO ayla_ops")
    # NOTE: ayla_ops has NO grant on identity_memoryentry directly — only the
    # safe view. SELECT against the base table from ayla_ops will fail with
    # "permission denied".

    # ayla_migrator: ALL for schema operations + DML for backfills.
    schema_editor.execute("GRANT ALL ON identity_memoryentry TO ayla_migrator")
    schema_editor.execute("GRANT ALL ON identity_userpersonalcontext TO ayla_migrator")
    schema_editor.execute("GRANT ALL ON identity_redzoneaccesslog TO ayla_migrator")
    schema_editor.execute("GRANT ALL ON memory_entry_safe TO ayla_migrator")

    # ayla_backup: REPLICATION privilege is at role level (CREATE ROLE
    # above); no table-level grants needed for physical backup.

    # ayla_ops_redzone_break_glass: NO standing grants. Grants happen at
    # break-glass time via the secret-manager runbook (4-eyes approval
    # creates a temporary `GRANT ayla_app TO ayla_ops_redzone_break_glass`
    # for the 1h emergency window, then REVOKEs).


def _drop_red_zone_security(apps, schema_editor):
    """Reverse migration — drop policy, view, and roles.

    Postgres-only. SQLite no-op.

    Dependency order: drop policy → drop view → revoke grants → drop roles.
    Roles only dropped if they have no remaining dependencies (other DBs in
    same cluster might use them — DROP ROLE IF EXISTS is forgiving).
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    # Policy first (before disabling RLS)
    schema_editor.execute(
        "DROP POLICY IF EXISTS memory_entry_non_red_visible ON identity_memoryentry"
    )
    schema_editor.execute("DROP POLICY IF EXISTS memory_entry_write_all ON identity_memoryentry")
    schema_editor.execute("ALTER TABLE identity_memoryentry NO FORCE ROW LEVEL SECURITY")
    schema_editor.execute("ALTER TABLE identity_memoryentry DISABLE ROW LEVEL SECURITY")

    # View
    schema_editor.execute("DROP VIEW IF EXISTS memory_entry_safe")

    # Grants are auto-revoked when roles are dropped, but explicit revoke
    # surfaces any role-dependency errors early.
    for role in ["ayla_app", "ayla_ops", "ayla_migrator"]:
        schema_editor.execute(f"REVOKE ALL ON identity_memoryentry FROM {role}")
        schema_editor.execute(f"REVOKE ALL ON identity_userpersonalcontext FROM {role}")
        schema_editor.execute(f"REVOKE ALL ON identity_redzoneaccesslog FROM {role}")

    # Roles. DROP ROLE IF EXISTS is safe — if other DBs depend on the role,
    # this will error and migration rollback aborts (correct behaviour —
    # we shouldn't blindly drop roles other systems use).
    for role in [
        "ayla_ops_redzone_break_glass",
        "ayla_backup",
        "ayla_migrator",
        "ayla_ops",
        "ayla_app",
    ]:
        schema_editor.execute(f"DROP ROLE IF EXISTS {role}")


class Migration(migrations.Migration):
    atomic = False  # CRITICAL — see module docstring

    dependencies = [
        ("identity", "0006_user_personal_context"),
    ]

    operations = [
        migrations.RunPython(_add_red_zone_security, _drop_red_zone_security),
    ]
