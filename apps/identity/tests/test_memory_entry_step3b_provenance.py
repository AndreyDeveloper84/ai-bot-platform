"""Step 2.5 / 3B tests — canonical `provenance` (Memory Domain Contract §3.1).

Schema (0017): nullable CharField, canonical choices only, no backfill.
Data (0018): explicit → user_stated; inferred/signal/unknown stay NULL
(silent promotion forbidden — user_confirmed_inference requires explicit
confirmation through the proposal flow, which is out of scope here).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as tz

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.identity.models import MemoryEntry

_MIG_0017 = ("identity", "0017_memoryentry_provenance")
_MIG_0018 = ("identity", "0018_memoryentry_provenance_backfill")
_MIG_HEAD = ("identity", "0019_memoryentry_lifecycle_constraints")

_TS = datetime(2026, 1, 10, 12, 0, 0, tzinfo=tz.utc)


def _executor() -> MigrationExecutor:
    # Fresh executor per use — the loader caches applied-migration state.
    return MigrationExecutor(connection)


def _apps_at(target):
    return _executor().loader.project_state([target]).apps


@pytest.fixture
def at_0017():
    """Migrate down to 0017; ALWAYS return to the chain HEAD afterwards.

    Head, not 0018: migrating «to 0018» unapplies everything after it, and
    since pytest-django reuses one database for the whole session that would
    silently strip migration 0019's CHECK constraints from every test that
    runs later (DRF-1263).
    """
    _executor().migrate([_MIG_0017])
    yield _apps_at(_MIG_0017)
    _executor().migrate([_MIG_HEAD])


def _row(apps, upc, *, source="explicit", **fields) -> uuid.UUID:
    MemoryEntry = apps.get_model("identity", "MemoryEntry")
    if source in ("inferred", "signal"):  # CHECK 1 invariant
        fields.setdefault("last_inferred_at", _TS)
    entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone="green",
        source=source,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
        **fields,
    )
    return entry.pk


def _field(apps, pk, name):
    return getattr(apps.get_model("identity", "MemoryEntry").objects.get(pk=pk), name)


@pytest.mark.django_db
class TestProvenanceSchema:
    def test_provenance_nullable_for_inferred(self, db):
        """1. The field is optional — an inferred row is valid with NULL.

        Narrowed by DRF-1263: the column stays nullable, but CHECK 5
        (migration 0019) forbids NULL on `source='explicit'`. NULL is now
        exactly what it was supposed to mean — «not yet confirmed through the
        proposal flow» — instead of a de-facto third provenance value.
        """
        from django.utils import timezone

        from apps.identity.models import UserPersonalContext

        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_INFERRED,
            last_inferred_at=timezone.now(),  # CHECK 1
            kind="lifestyle",
            content={"key": "diet", "value": "vegan"},
        )
        entry.refresh_from_db()
        assert entry.provenance is None
        assert MemoryEntry._meta.get_field("provenance").null is True

    def test_choices_are_canonical_only(self):
        """2. Exactly the §3.1 vocabulary — nothing else, no confidence."""
        assert [v for v, _ in MemoryEntry.PROVENANCE_CHOICES] == [
            "user_stated",
            "user_confirmed_inference",
        ]

    def test_no_confidence_field(self):
        """12. `confidence` is forbidden in canonical MemoryEntry
        (AYLA-DEC-0024) — pin its absence."""
        assert "confidence" not in {f.name for f in MemoryEntry._meta.fields}


@pytest.mark.django_db(transaction=True)
class TestProvenanceBackfill:
    def test_backfill_mapping(self, at_0017):
        """3./4./5./10. explicit→user_stated; inferred/signal stay NULL;
        the legacy `source` column itself is never modified."""
        apps = at_0017
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())

        explicit = _row(apps, upc, source="explicit")
        inferred = _row(apps, upc, source="inferred")
        signal = _row(apps, upc, source="signal")

        _executor().migrate([_MIG_0018])
        apps = _apps_at(_MIG_0018)

        assert _field(apps, explicit, "provenance") == "user_stated"
        assert _field(apps, inferred, "provenance") is None
        assert _field(apps, signal, "provenance") is None
        # 10. legacy source untouched by the backfill.
        assert _field(apps, explicit, "source") == "explicit"
        assert _field(apps, inferred, "source") == "inferred"
        assert _field(apps, signal, "source") == "signal"

    @pytest.mark.skipif(
        connection.vendor == "postgresql",
        reason="CHECK 1 (memory_entry_inferred_nullness) makes an unknown "
        "source value unrepresentable on Postgres; SQLite has no such CHECK.",
    )
    def test_unknown_legacy_source_stays_null(self, at_0017):
        """6. A source value outside the known vocabulary stays NULL."""
        apps = at_0017
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())
        legacy = _row(apps, upc, source="legacy_unknown", last_inferred_at=_TS)

        _executor().migrate([_MIG_0018])
        apps = _apps_at(_MIG_0018)

        assert _field(apps, legacy, "provenance") is None
        assert _field(apps, legacy, "source") == "legacy_unknown"

    def test_prefilled_provenance_not_overwritten(self, at_0017):
        """7./9. NULL-guard: existing provenance survives; re-run is a no-op."""
        import importlib

        apps = at_0017
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())
        # A row already carrying the confirmation-flow value.
        confirmed = _row(apps, upc, source="inferred", provenance="user_confirmed_inference")
        explicit = _row(apps, upc, source="explicit")

        _executor().migrate([_MIG_0018])
        apps = _apps_at(_MIG_0018)
        assert _field(apps, confirmed, "provenance") == "user_confirmed_inference"
        assert _field(apps, explicit, "provenance") == "user_stated"

        migration = importlib.import_module(
            "apps.identity.migrations.0018_memoryentry_provenance_backfill"
        )
        migration.backfill_provenance(apps, None)  # direct second run
        assert _field(apps, confirmed, "provenance") == "user_confirmed_inference"
        assert _field(apps, explicit, "provenance") == "user_stated"

    def test_schema_migration_0017_reversible(self, at_0017):
        """0017 (schema) itself rolls back to 0016 and re-applies cleanly."""
        _executor().migrate([("identity", "0016_memoryentry_step3_backfill")])
        old = _apps_at(("identity", "0016_memoryentry_step3_backfill")).get_model(
            "identity", "MemoryEntry"
        )
        assert "provenance" not in {f.name for f in old._meta.fields}

        _executor().migrate([_MIG_0018])
        new = _apps_at(_MIG_0018).get_model("identity", "MemoryEntry")
        assert "provenance" in {f.name for f in new._meta.fields}

    def test_rollback(self, at_0017):
        """8. Backwards NULLs exactly source=explicit AND provenance=
        user_stated; confirmation-flow values survive the rollback."""
        apps = at_0017
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())
        explicit = _row(apps, upc, source="explicit")
        confirmed = _row(apps, upc, source="inferred", provenance="user_confirmed_inference")

        _executor().migrate([_MIG_0018])
        _executor().migrate([_MIG_0017])  # rollback
        apps = _apps_at(_MIG_0017)

        assert _field(apps, explicit, "provenance") is None
        assert _field(apps, confirmed, "provenance") == "user_confirmed_inference"
