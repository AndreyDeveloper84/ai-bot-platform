"""UserPersonalContext + MemoryEntry + RedZoneAccessLog — Sprint 1 Track A.

Implements docs/specs/memory-entry-schema.md §1-§4 + §12.

# Why atomic = False

Per spec §12 (NOT VALID + VALIDATE migration discipline) + tech-lead
mandate 2026-05-22:

1. **NOT VALID + VALIDATE requires two transactions.** With atomic=True
   Django wraps the whole migration in one tx — VALIDATE then fails
   because NOT VALID hasn't committed yet.

2. **CREATE INDEX CONCURRENTLY** (not used here, but the pattern is
   standardised for the future): cannot run inside a transaction. Future
   schema-tightening migrations on populated tables will need this.

3. **DB role separation + view + trigger** (veha 2, separate migration):
   creating roles via CREATE ROLE also doesn't play well with atomic
   transactions in some pg versions.

# CHECK constraints — 3 of them per spec §4

The constraints are NOT managed by Django (no models.CheckConstraint in
Meta) — they live here as RunSQL with NOT VALID + VALIDATE pairs.
Rationale lives in apps/identity/models.py:MemoryEntry.Meta docstring.

Application-side mirror validation lives in apps/identity/services/
memory_writer.py (veha 3).

# Tests this migration MUST satisfy

Spec §13 tests 14.1 / 14.2 / 14.3 (schema), 14.9-14.14 (CHECK constraint
behaviour), 14.15 (unknown_legacy backfill self-test).

# Backfill for CHECK 3 (deletion_reason nullness)

Round-3 S1 fix: pre-existing soft-deleted rows in dev/staging (from
earlier schema iterations, not from this PR) get `deletion_reason =
'unknown_legacy'` before VALIDATE CONSTRAINT step. Greenfield production
will never trigger this code path (no rows exist).
"""

import uuid

from django.db import migrations, models
from django_cryptography.fields import encrypt


def _add_check_constraints(apps, schema_editor):
    """Postgres-only: backfill `unknown_legacy` + add 3 CHECK constraints
    using NOT VALID + VALIDATE pattern (spec §12).

    On SQLite (local dev): no-op. CHECK constraints are postgres-only
    in this project; SQLite is used solely for fast iteration and the
    relevant tests are skipped via `pytest.mark.skipif`.
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    # Backfill any pre-existing soft-deleted rows in dev/staging with
    # deletion_reason='unknown_legacy' (round-3 S1 fix). Greenfield
    # production never has rows here at this migration point — UPDATE
    # affects 0 rows and is a no-op.
    schema_editor.execute(
        "UPDATE identity_memoryentry "
        "SET deletion_reason = 'unknown_legacy' "
        "WHERE (delete_requested_at IS NOT NULL OR soft_deleted_at IS NOT NULL) "
        "AND deletion_reason IS NULL"
    )

    # CHECK 1: source/last_inferred_at nullness invariant
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        "ADD CONSTRAINT memory_entry_inferred_nullness CHECK ("
        "  (source = 'explicit' AND last_inferred_at IS NULL) "
        "  OR (source IN ('inferred', 'signal') AND last_inferred_at IS NOT NULL)"
        ") NOT VALID"
    )
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry VALIDATE CONSTRAINT memory_entry_inferred_nullness"
    )

    # CHECK 2: yellow/red requires consent_at OR soft-delete tombstone
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        "ADD CONSTRAINT memory_entry_yellow_red_requires_consent CHECK ("
        "  sensitivity_zone = 'green' "
        "  OR (sensitivity_zone IN ('yellow', 'red') "
        "      AND (consent_at IS NOT NULL OR soft_deleted_at IS NOT NULL))"
        ") NOT VALID"
    )
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        "VALIDATE CONSTRAINT memory_entry_yellow_red_requires_consent"
    )

    # CHECK 3: deletion_reason nullness vs delete/soft-delete timestamps
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        "ADD CONSTRAINT memory_entry_deletion_reason_nullness CHECK ("
        "  (delete_requested_at IS NULL AND soft_deleted_at IS NULL "
        "   AND deletion_reason IS NULL) "
        "  OR (deletion_reason IS NOT NULL "
        "      AND (delete_requested_at IS NOT NULL "
        "           OR soft_deleted_at IS NOT NULL))"
        ") NOT VALID"
    )
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry VALIDATE CONSTRAINT memory_entry_deletion_reason_nullness"
    )


def _drop_check_constraints(apps, schema_editor):
    """Reverse migration — drop the 3 CHECK constraints.

    Postgres-only. SQLite no-op (same reason as forward: SQLite was never
    given these constraints in the first place).
    """
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        "ALTER TABLE identity_memoryentry DROP CONSTRAINT IF EXISTS memory_entry_inferred_nullness"
    )
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        "DROP CONSTRAINT IF EXISTS memory_entry_yellow_red_requires_consent"
    )
    schema_editor.execute(
        "ALTER TABLE identity_memoryentry "
        "DROP CONSTRAINT IF EXISTS memory_entry_deletion_reason_nullness"
    )
    # NOTE: backfill UPDATE is one-way (deletion_reason values cannot be
    # safely "un-backfilled" — we don't know which rows were legacy).


class Migration(migrations.Migration):
    atomic = False  # CRITICAL — see module docstring

    dependencies = [
        ("identity", "0005_botuser_ayla_user_id"),
    ]

    operations = [
        # ─── UserPersonalContext ─────────────────────────────────────────
        migrations.CreateModel(
            name="UserPersonalContext",
            fields=[
                (
                    "user_id",
                    models.UUIDField(
                        editable=False,
                        help_text=(
                            "Canonical Ayla User.id. 1-to-1 with UPC. NOT a "
                            "Django FK because the canonical User lives in "
                            "Ayla djangoproject per ADR-0009 §Hard rule #1 "
                            "(no duplicate canonical state)."
                        ),
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "soft_deleted_at",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        help_text=(
                            "Set when user invoked forget-all OR account-"
                            "closure. Hard-delete forbidden — soft-delete + "
                            "tombstone retention per 152-ФЗ Chapter 3 right-"
                            "to-be-forgotten implementation."
                        ),
                        null=True,
                    ),
                ),
                (
                    "display_name_preferred",
                    models.TextField(
                        blank=True,
                        help_text="Optional — Ayla's preferred display name for the user.",
                        null=True,
                    ),
                ),
                (
                    "language_preferred",
                    models.CharField(
                        blank=True,
                        help_text=(
                            "ISO-639-1 language code, e.g. 'ru'. NULL until user sets a preference."
                        ),
                        max_length=8,
                        null=True,
                    ),
                ),
                (
                    "summary",
                    models.TextField(
                        blank=True,
                        help_text=(
                            "Ayla's running summary of who this user is. "
                            "Application-side capped at 8 KB."
                        ),
                        null=True,
                    ),
                ),
                (
                    "forget_all_requested_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "Set when user invokes POST /api/v1/users/me/"
                            "memory/forget-all (per ADR-0011 §3.3)."
                        ),
                        null=True,
                    ),
                ),
                (
                    "minor_lock",
                    models.BooleanField(
                        default=False,
                        help_text=(
                            "Per ADR-0011 §10.2 + spec §1: set true when "
                            "reconciliation job detects post-fact that the "
                            "user is a minor; blocks future yellow/red writes."
                        ),
                    ),
                ),
            ],
            options={
                "verbose_name": "User personal context",
                "verbose_name_plural": "User personal contexts",
            },
        ),
        migrations.AddIndex(
            model_name="userpersonalcontext",
            index=models.Index(fields=["soft_deleted_at"], name="upc_soft_del_idx"),
        ),
        # ─── MemoryEntry ─────────────────────────────────────────────────
        migrations.CreateModel(
            name="MemoryEntry",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "user_id",
                    models.UUIDField(
                        db_index=True,
                        help_text=("Denormalised — matches personal_context.user_id."),
                    ),
                ),
                (
                    "personal_context",
                    models.ForeignKey(
                        help_text=(
                            "Owning UPC. CASCADE — physical CASCADE only "
                            "relevant for ops-level hard-delete after 30d "
                            "tombstone retention."
                        ),
                        on_delete=models.deletion.CASCADE,
                        related_name="memory_entries",
                        to="identity.userpersonalcontext",
                    ),
                ),
                (
                    "sensitivity_zone",
                    models.CharField(
                        choices=[
                            ("green", "Green — innocuous"),
                            ("yellow", "Yellow — personal"),
                            ("red", "Red — sensitive (152-ФЗ §10 category)"),
                        ],
                        db_index=True,
                        help_text=(
                            "Per-fact zone (green/yellow/red). Drives consent "
                            "requirement, retention, audit-log scope. See spec §5."
                        ),
                        max_length=8,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("explicit", "Explicit — user stated directly"),
                            ("inferred", "Inferred — Ayla derived from conversation"),
                            ("signal", "Signal — derived from observable events"),
                        ],
                        db_index=True,
                        help_text=(
                            "How the fact entered memory. CHECK 1 enforces "
                            "last_inferred_at nullness invariant against source."
                        ),
                        max_length=10,
                    ),
                ),
                (
                    "last_inferred_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "When inference last updated this entry. MUST be "
                            "NULL when source='explicit' and NOT NULL when "
                            "source IN ('inferred', 'signal') — enforced by CHECK 1."
                        ),
                        null=True,
                    ),
                ),
                (
                    "source_tenant_id",
                    models.UUIDField(
                        blank=True,
                        help_text=(
                            "Tenant the fact originated at. NULL if cross-tenant. "
                            "Informational — NOT a scoping boundary."
                        ),
                        null=True,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("preference", "Preference"),
                            ("contraindication", "Contraindication"),
                            ("symptom", "Symptom"),
                            ("lifestyle", "Lifestyle"),
                            ("relationship", "Relationship"),
                            ("financial", "Financial"),
                            ("other", "Other"),
                        ],
                        default="other",
                        help_text="Categorical bucket for UX (Bonuses tab grouping).",
                        max_length=20,
                    ),
                ),
                (
                    "content",
                    encrypt(
                        models.JSONField(
                            default=dict,
                            help_text=(
                                "The fact payload. Encrypted at rest with the "
                                "Fernet key via django-cryptography-django5 "
                                "EncryptedJSONField (per ADR-0006)."
                            ),
                        )
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "last_used_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text=(
                            "Updated by the LLM access path (event-sourced "
                            "batch, not synchronous — see spec §13.2)."
                        ),
                    ),
                ),
                (
                    "last_used_count",
                    models.IntegerField(
                        default=0,
                        help_text=("Rolled up by the yellow-zone access count rollup job."),
                    ),
                ),
                (
                    "ttl_days",
                    models.IntegerField(
                        blank=True,
                        help_text=(
                            "Per-zone retention cap. NULL = no auto-TTL (green "
                            "default); 365 (yellow default); 90 (red default)."
                        ),
                        null=True,
                    ),
                ),
                (
                    "consent_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "When user explicitly consented to storing this "
                            "entry. MUST be NOT NULL for sensitivity_zone IN "
                            "('yellow', 'red') — enforced by CHECK 2."
                        ),
                        null=True,
                    ),
                ),
                (
                    "delete_requested_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "User-intent moment for entry deletion. Set in the "
                            "same UPDATE as deletion_reason."
                        ),
                        null=True,
                    ),
                ),
                (
                    "soft_deleted_at",
                    models.DateTimeField(
                        blank=True,
                        help_text=(
                            "Set by the soft-delete job OR in the same "
                            "transaction as delete_requested_at during "
                            "withdrawal."
                        ),
                        null=True,
                    ),
                ),
                (
                    "deletion_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("user_delete", "User-initiated per-entry delete"),
                            ("withdrawal", "Consent withdrawn for yellow/red entry"),
                            ("forget_all", "User invoked forget-all"),
                            ("ttl_purge", "Auto-purged by TTL sweep"),
                            (
                                "minor_protection",
                                "Auto-purged by minor-protection guard",
                            ),
                            (
                                "unknown_legacy",
                                "Pre-spec-era soft-delete (backfill only)",
                            ),
                        ],
                        help_text=(
                            "Distinguishes WHY a row is being deleted. CHECK 3 "
                            "enforces nullness invariant."
                        ),
                        max_length=20,
                        null=True,
                    ),
                ),
            ],
            options={
                "verbose_name": "Memory entry",
                "verbose_name_plural": "Memory entries",
            },
        ),
        migrations.AddIndex(
            model_name="memoryentry",
            index=models.Index(fields=["user_id", "sensitivity_zone"], name="me_user_zone_idx"),
        ),
        migrations.AddIndex(
            model_name="memoryentry",
            index=models.Index(
                condition=models.Q(("soft_deleted_at__isnull", True)),
                fields=["sensitivity_zone", "last_used_at"],
                name="me_zone_ttl_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="memoryentry",
            index=models.Index(
                condition=models.Q(
                    ("delete_requested_at__isnull", False),
                    ("soft_deleted_at__isnull", True),
                ),
                fields=["delete_requested_at"],
                name="me_delete_req_idx",
            ),
        ),
        # ─── RedZoneAccessLog ────────────────────────────────────────────
        migrations.CreateModel(
            name="RedZoneAccessLog",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "memory_entry_id",
                    models.UUIDField(
                        help_text=(
                            "The entry being accessed. NOT a Django FK with "
                            "CASCADE — log rows persist after entry purge."
                        )
                    ),
                ),
                (
                    "user_id",
                    models.UUIDField(
                        help_text=(
                            "Subject the entry belongs to. Indexed via "
                            "(user_id, ts) for the primary auditor query."
                        )
                    ),
                ),
                (
                    "accessor_role",
                    models.CharField(
                        choices=[
                            ("ayla_llm", "Ayla LLM prompt construction"),
                            ("system_job", "System job (TTL sweep, forget-all)"),
                            ("ops_admin", "Ops admin (break-glass)"),
                        ],
                        help_text=(
                            "Coarse category. Round-2 AS2: insufficient alone "
                            "for 152-ФЗ Chapter 3 «who accessed»."
                        ),
                        max_length=20,
                    ),
                ),
                (
                    "accessor_principal",
                    models.CharField(
                        help_text=(
                            "Round-2 AS2 fix — concrete identity per "
                            "accessor_role. Indexed for staff-X-date-range "
                            "auditor queries."
                        ),
                        max_length=256,
                    ),
                ),
                (
                    "access_type",
                    models.CharField(
                        choices=[
                            ("read", "Read"),
                            ("write", "Write"),
                            ("purge", "Purge"),
                            (
                                "withdrawal",
                                "Withdrawal — explicit consent revocation",
                            ),
                            (
                                "write_rejected_dob_lookup",
                                "Write rejected — DOB lookup failed",
                            ),
                        ],
                        help_text=(
                            "What kind of access. Round-2 AS2 + ADR-0011 §11.3 "
                            "added 'withdrawal' + 'write_rejected_dob_lookup'."
                        ),
                        max_length=32,
                    ),
                ),
                (
                    "ts",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text=(
                            "When the access happened. Indexed via three "
                            "composite indexes per spec §3."
                        ),
                    ),
                ),
                (
                    "request_id",
                    models.UUIDField(
                        help_text=(
                            "Audit reference — Celery task id, HTTP request "
                            "id, OR ops ticket id. Validated as UUID by the "
                            "BEFORE SELECT trigger (spec §9). Round-3 A5."
                        )
                    ),
                ),
                (
                    "purpose",
                    models.TextField(help_text=("Human-readable purpose (round-3 A5).")),
                ),
            ],
            options={
                "verbose_name": "Red-zone access log",
                "verbose_name_plural": "Red-zone access logs",
            },
        ),
        migrations.AddIndex(
            model_name="redzoneaccesslog",
            index=models.Index(fields=["user_id", "ts"], name="rzlog_user_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="redzoneaccesslog",
            index=models.Index(fields=["accessor_principal", "ts"], name="rzlog_principal_ts_idx"),
        ),
        migrations.AddIndex(
            model_name="redzoneaccesslog",
            index=models.Index(fields=["access_type", "ts"], name="rzlog_type_ts_idx"),
        ),
        # ─── CHECK constraints — postgres-only, NOT VALID + VALIDATE ────
        # See spec §4 + §12 for rationale. Each constraint is added in two
        # separate steps so VALIDATE runs in its own transaction (atomic =
        # False at module level enables this).
        #
        # SQLite (local dev) does not support `NOT VALID` + `VALIDATE
        # CONSTRAINT` syntax. Production + CI run Postgres; local dev runs
        # SQLite. The RunPython wrapper checks `connection.vendor` and
        # no-ops on SQLite. Tests that rely on CHECK behaviour
        # (tests 14.9-14.14) are marked `pytest.mark.skipif` for SQLite.
        migrations.RunPython(_add_check_constraints, _drop_check_constraints),
    ]
