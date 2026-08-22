"""Memory Domain Contract §3.1 schema-addition tests (Migration Plan Step 2).

The 11 fields added by migration 0015 are schema-only: no runtime path
reads or writes them yet, legacy rows keep them NULL (JSON defaults `[]`),
and the semantic backfill is Step 3. These tests pin the migration's
apply/rollback safety and the additive-only guarantee.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor

from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services.memory_reader import read_personal_context
from apps.identity.services.memory_writer import write_entry

pytestmark = pytest.mark.django_db

_MIG_PREV = ("identity", "0014_seed_global_bot_tenant")
_MIG_NEW = ("identity", "0015_memoryentry_consent_scope_and_more")

NEW_FIELDS = [
    "status",
    "updated_at",
    "effective_from",
    "expires_at",
    "superseded_by",
    "supersession_reason",
    "source_event_id",
    "evidence_refs",
    "derivation_method",
    "consent_scope",
    "purpose_tags",
]


def _upc() -> UserPersonalContext:
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _green(upc, **overrides):
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,  # CHECK 5 (DRF-1263)
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )
    kwargs.update(overrides)
    return MemoryEntry.objects.create(**kwargs)


class TestMigrationShape:
    def test_columns_exist_after_migrate(self):
        """A. The migration applies and the new columns are present."""
        cols = {
            c.name
            for c in connection.introspection.get_table_description(
                connection.cursor(), "identity_memoryentry"
            )
        }
        expected = {f if f != "superseded_by" else "superseded_by_id" for f in NEW_FIELDS}
        assert expected <= cols

    @pytest.mark.django_db(transaction=True)
    def test_migration_rollback_and_reapply(self):
        """B. 0015 migrates back to 0014 and forward again cleanly."""
        # Fresh executor per migrate() — the loader caches applied-migration
        # state at init, so a reused executor mis-plans the second migrate.
        # The finally ALWAYS returns the test DB to the migration-chain head:
        # 0016+ are data-only, 0017 adds `provenance` — leaving the DB behind
        # head breaks later tests that write through the runtime model.
        head = ("identity", "0018_memoryentry_provenance_backfill")
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([_MIG_PREV])
            old = executor.loader.project_state([_MIG_PREV]).apps.get_model(
                "identity", "MemoryEntry"
            )
            assert not set(NEW_FIELDS) & {f.name for f in old._meta.fields}

            executor = MigrationExecutor(connection)
            executor.migrate([_MIG_NEW])
            new = executor.loader.project_state([_MIG_NEW]).apps.get_model(
                "identity", "MemoryEntry"
            )
            assert set(NEW_FIELDS) <= {f.name for f in new._meta.fields}
        finally:
            MigrationExecutor(connection).migrate([head])


class TestLegacyRowCompat:
    def test_existing_row_shape_untouched(self):
        """C. A row written the pre-Step-2 way keeps NULL/[] new fields."""
        upc = _upc()
        entry = _green(upc)
        entry.refresh_from_db()
        for field in (
            "status",
            "updated_at",
            "effective_from",
            "expires_at",
            "superseded_by",
            "supersession_reason",
            "source_event_id",
            "derivation_method",
            "consent_scope",
        ):
            assert getattr(entry, field) is None, field
        assert entry.evidence_refs == []
        assert entry.purpose_tags == []

    def test_existing_reader_behavior_unchanged(self):
        """D. The green reader surfaces the row exactly as before."""
        upc = _upc()
        _green(upc)
        view = read_personal_context(upc.user_id)
        assert len(view.green_facts) == 1
        assert view.green_facts[0].content == {"key": "diet", "value": "vegan"}

    def test_write_path_works_without_new_fields(self):
        """E. write_entry with pre-Step-2 arguments still persists.

        NOTE: since Step 3.5 (write compatibility) an explicit write also
        stamps the canonical lifecycle fields — see
        test_memory_entry_step35_write_compat.py. The Step-2 guarantee that
        changed: «no canonical fields on write» → superseded by «canonical
        fields stamped at write». Non-canonical fields are still untouched.
        """
        upc = _upc()
        entry = write_entry(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            kind="lifestyle",
            content={"key": "diet", "value": "keto"},
            request_id=uuid.uuid4(),
            purpose="test:step2",
        )
        assert entry is not None
        entry.refresh_from_db()
        assert entry.status == MemoryEntry.STATUS_ACTIVE  # Step 3.5 stamping
        assert entry.updated_at is not None
        assert entry.consent_scope is None  # still never fabricated
        assert entry.evidence_refs == []


class TestJsonDefaults:
    def test_json_defaults_are_independent_per_instance(self):
        """F. Mutating one row's JSON default never leaks into another."""
        upc = _upc()
        first = _green(upc, content={"key": "k1"})
        second = _green(upc, content={"key": "k2"})

        first.evidence_refs.append("obs-1")
        first.purpose_tags.append("discovery")
        first.save()

        second.refresh_from_db()
        assert second.evidence_refs == []
        assert second.purpose_tags == []
        # ...and a fresh in-memory instance gets its own list objects.
        fresh = MemoryEntry()
        assert fresh.evidence_refs == [] and fresh.purpose_tags == []
        assert fresh.evidence_refs is not first.evidence_refs


class TestSourceEventId:
    def test_multiple_nulls_allowed(self):
        """G1. Legacy-style rows (NULL source_event_id) coexist."""
        upc = _upc()
        _green(upc, content={"key": "k1"})
        _green(upc, content={"key": "k2"})
        assert MemoryEntry.objects.filter(source_event_id__isnull=True).count() == 2

    def test_duplicate_non_null_rejected(self):
        """G2. The unique constraint fires on a duplicate non-NULL value."""
        upc = _upc()
        event_id = uuid.uuid4()
        _green(upc, content={"key": "k1"}, source_event_id=event_id)
        with pytest.raises(IntegrityError):
            _green(upc, content={"key": "k2"}, source_event_id=event_id)


class TestSupersededBy:
    def test_accepts_null_and_valid_link(self):
        """H. superseded_by: NULL by default; a valid self-link persists."""
        upc = _upc()
        replacement = _green(upc, content={"key": "diet", "value": "keto"})
        old = _green(upc)
        assert old.superseded_by is None

        old.superseded_by = replacement
        old.supersession_reason = MemoryEntry.SUPERSESSION_CHANGED
        old.status = MemoryEntry.STATUS_SUPERSEDED
        old.save()
        old.refresh_from_db()
        assert old.superseded_by_id == replacement.id
        assert old.supersession_reason == "changed"

    def test_set_null_on_replacement_delete(self):
        """H2. Purging the replacement SET_NULLs the link (no cascade)."""
        upc = _upc()
        replacement = _green(upc, content={"key": "diet", "value": "keto"})
        old = _green(upc, superseded_by=replacement)
        replacement.delete()
        old.refresh_from_db()
        assert old.superseded_by is None
