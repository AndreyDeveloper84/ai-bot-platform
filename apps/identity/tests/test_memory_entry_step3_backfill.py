"""Step-3 legacy backfill tests (Memory Domain Contract §3.1, migration 0016).

The data migration is deterministic, idempotent (NULL-guarded UPDATEs) and
reversible. Schema is byte-identical between 0015 and 0016 (data-only), so
these tests drive the executor down to 0015, insert legacy-shaped rows via
the historical app registry, migrate forward and assert the exact mapping.

Provenance is deliberately NOT tested: the canonical `provenance` field was
not part of the Step-2 schema additions, so there is nothing to backfill
into (contract/migration-plan mismatch — see the Step-3 report).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone as tz

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)

_MIG_0015 = ("identity", "0015_memoryentry_consent_scope_and_more")
_MIG_0016 = ("identity", "0016_memoryentry_step3_backfill")
_MIG_HEAD = ("identity", "0019_memoryentry_lifecycle_constraints")

_TS = datetime(2026, 1, 10, 12, 0, 0, tzinfo=tz.utc)


def _executor() -> MigrationExecutor:
    # Fresh executor per use — the loader caches applied-migration state.
    return MigrationExecutor(connection)


def _apps_at(target):
    return _executor().loader.project_state([target]).apps


@pytest.fixture
def at_0015():
    """Migrate down to 0015; ALWAYS return to the chain head afterwards
    (0017 adds the `provenance` column the runtime model writes; 0019 the
    lifecycle CHECK constraints). The target MUST be the real head — the
    session shares one database, so returning to an earlier node leaves every
    later test running against a schema with the constraints stripped."""
    _executor().migrate([_MIG_0015])
    yield _apps_at(_MIG_0015)
    _executor().migrate([_MIG_HEAD])


def _row(apps, upc, *, created_at=_TS, source="explicit", **fields) -> uuid.UUID:
    """Insert a legacy-shaped row through the historical model registry."""
    MemoryEntry = apps.get_model("identity", "MemoryEntry")
    if source in ("inferred", "signal"):  # CHECK 1 invariant
        fields.setdefault("last_inferred_at", created_at)
    entry = MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone="green",
        source=source,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
        **fields,
    )
    MemoryEntry.objects.filter(pk=entry.pk).update(created_at=created_at)
    return entry.pk


def _field(apps, pk, name):
    return getattr(apps.get_model("identity", "MemoryEntry").objects.get(pk=pk), name)


class TestBackfillForward:
    def test_status_effective_updated_expires_mapping(self, at_0015):
        apps = at_0015
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())

        active = _row(apps, upc)  # 1: plain active explicit row
        pending = _row(  # 2: delete requested, sweep not yet run
            apps,
            upc,
            delete_requested_at=_TS + timedelta(days=1),
            deletion_reason="user_delete",
        )
        deleted = _row(  # 3: soft-deleted
            apps,
            upc,
            soft_deleted_at=_TS + timedelta(days=2),
            deletion_reason="user_delete",
        )
        both = _row(  # 4: both stamps — soft_deleted wins
            apps,
            upc,
            delete_requested_at=_TS + timedelta(days=1),
            soft_deleted_at=_TS + timedelta(days=2),
            deletion_reason="forget_all",
        )
        ttl_row = _row(apps, upc, ttl_days=90)  # 5: ttl-backed expires_at
        no_ttl = _row(apps, upc, ttl_days=None)  # 6: ttl NULL → expires NULL
        used = _row(apps, upc)  # 8: updated_at must NOT track last_used_at
        apps.get_model("identity", "MemoryEntry").objects.filter(pk=used).update(
            last_used_at=_TS + timedelta(days=5)
        )
        inferred = _row(apps, upc, source="inferred")  # 10: not promoted
        signal = _row(apps, upc, source="signal")  # 11: not promoted
        preset_expires = _TS + timedelta(days=30)
        preset = _row(  # 14a: already-populated fields stay untouched
            apps,
            upc,
            status="active",
            effective_from=_TS + timedelta(days=3),
            updated_at=_TS + timedelta(days=4),
            expires_at=preset_expires,
        )

        _executor().migrate([_MIG_0016])
        apps = _apps_at(_MIG_0016)

        # 1. normal active row: full mapping applied.
        assert _field(apps, active, "status") == "active"
        assert _field(apps, active, "effective_from") == _TS  # 7
        assert _field(apps, active, "updated_at") == _TS
        assert _field(apps, active, "expires_at") is None
        # 2./3./4. status from deletion stamps.
        assert _field(apps, pending, "status") == "deletion_pending"
        assert _field(apps, deleted, "status") == "deleted"
        assert _field(apps, both, "status") == "deleted"
        # 5./6. expires_at only from ttl_days, exact.
        assert _field(apps, ttl_row, "expires_at") == _TS + timedelta(days=90)
        assert _field(apps, no_ttl, "expires_at") is None
        # 8. updated_at = created_at, never last_used_at.
        assert _field(apps, used, "updated_at") == _TS
        assert _field(apps, used, "updated_at") != _field(apps, used, "last_used_at")
        # 10./11. inferred/signal: status by stamps, source untouched.
        assert _field(apps, inferred, "status") == "active"
        assert _field(apps, inferred, "source") == "inferred"
        assert _field(apps, signal, "status") == "active"
        assert _field(apps, signal, "source") == "signal"
        # 12./13. consent_scope / evidence fields are never fabricated.
        for pk in (active, pending, deleted, both, ttl_row):
            assert _field(apps, pk, "consent_scope") is None
            assert _field(apps, pk, "evidence_refs") == []
            assert _field(apps, pk, "derivation_method") is None
            assert _field(apps, pk, "superseded_by_id") is None
            assert _field(apps, pk, "supersession_reason") is None
            assert _field(apps, pk, "source_event_id") is None
            assert _field(apps, pk, "purpose_tags") == []
        # 14a. pre-set values survive the backfill (NULL-guards).
        assert _field(apps, preset, "status") == "active"
        assert _field(apps, preset, "effective_from") == _TS + timedelta(days=3)
        assert _field(apps, preset, "updated_at") == _TS + timedelta(days=4)
        assert _field(apps, preset, "expires_at") == preset_expires

    def test_forward_is_idempotent(self, at_0015):
        """14. Re-running the backfill changes nothing."""
        import importlib

        apps = at_0015
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())
        pk = _row(apps, upc, ttl_days=365)

        _executor().migrate([_MIG_0016])
        apps = _apps_at(_MIG_0016)
        before = {
            f: _field(apps, pk, f) for f in ("status", "effective_from", "updated_at", "expires_at")
        }

        migration = importlib.import_module(
            "apps.identity.migrations.0016_memoryentry_step3_backfill"
        )
        migration.backfill_step2_fields(apps, None)  # direct second run

        after = {
            f: _field(apps, pk, f) for f in ("status", "effective_from", "updated_at", "expires_at")
        }
        assert before == after
        assert before["status"] == "active"
        assert before["expires_at"] == _TS + timedelta(days=365)


class TestBackfillRollback:
    def test_rollback_restores_nulls(self, at_0015):
        """15. Backwards migration NULLs exactly the four backfilled fields."""
        apps = at_0015
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())
        pk = _row(apps, upc, ttl_days=90, soft_deleted_at=_TS, deletion_reason="user_delete")

        _executor().migrate([_MIG_0016])
        apps16 = _apps_at(_MIG_0016)
        assert _field(apps16, pk, "status") == "deleted"
        assert _field(apps16, pk, "expires_at") is not None

        _executor().migrate([_MIG_0015])
        apps15 = _apps_at(_MIG_0015)
        for field_name in ("status", "effective_from", "updated_at", "expires_at"):
            assert _field(apps15, pk, field_name) is None, field_name


class TestRollbackTouchesOnlyWhatForwardWrote:
    """DRF-1264 — the reverse function must not erase state it never created.

    `revert_step2_fields` was a filter-less
    ``update(status=None, effective_from=None, updated_at=None, expires_at=None)``
    over the WHOLE table. One rollback therefore wiped the lifecycle of every
    row, including rows written long after the migration ran — a supersession,
    a deletion, a zone transition, a TTL. Today the table is empty and the
    reverse costs nothing; after the pilot fills it, it costs the state of
    living people's memory.
    """

    def _post_migration_row(self, apps, upc):
        """A row whose lifecycle values the forward rule could never produce."""
        MemoryEntry = apps.get_model("identity", "MemoryEntry")
        pk = _row(apps, upc)
        MemoryEntry.objects.filter(pk=pk).update(
            status="superseded",  # forward would derive 'active' from the stamps
            effective_from=_TS + timedelta(days=3),  # forward: == created_at
            updated_at=_TS + timedelta(days=4),  # forward: == created_at
            expires_at=_TS + timedelta(days=7),  # forward: NULL (no ttl_days)
        )
        return pk

    def test_rollback_preserves_a_row_the_backfill_never_wrote(self, at_0015):
        apps = at_0015
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())

        _executor().migrate([_MIG_0016])
        apps16 = _apps_at(_MIG_0016)
        upc16 = apps16.get_model("identity", "UserPersonalContext").objects.get(pk=upc.pk)
        pk = self._post_migration_row(apps16, upc16)

        _executor().migrate([_MIG_0015])  # rollback
        apps15 = _apps_at(_MIG_0015)

        assert _field(apps15, pk, "status") == "superseded", (
            "The rollback erased a supersession that happened AFTER the "
            "backfill — the row now claims a lifecycle it never had."
        )
        assert _field(apps15, pk, "effective_from") == _TS + timedelta(days=3)
        assert _field(apps15, pk, "updated_at") == _TS + timedelta(days=4)
        assert _field(apps15, pk, "expires_at") == _TS + timedelta(days=7)

    def test_rollback_preserves_a_ttl_that_is_not_the_backfilled_one(self, at_0015):
        """`expires_at` reverts only when it still equals created_at + ttl_days."""
        apps = at_0015
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())

        _executor().migrate([_MIG_0016])
        apps16 = _apps_at(_MIG_0016)
        upc16 = apps16.get_model("identity", "UserPersonalContext").objects.get(pk=upc.pk)
        MemoryEntry16 = apps16.get_model("identity", "MemoryEntry")

        backfilled = _row(apps16, upc16, ttl_days=90)
        MemoryEntry16.objects.filter(pk=backfilled).update(
            expires_at=_TS + timedelta(days=90)  # exactly the backfill's value
        )
        retimed = _row(apps16, upc16, ttl_days=90)
        MemoryEntry16.objects.filter(pk=retimed).update(
            expires_at=_TS + timedelta(days=400)  # extended after the backfill
        )

        _executor().migrate([_MIG_0015])
        apps15 = _apps_at(_MIG_0015)

        assert _field(apps15, backfilled, "expires_at") is None
        assert _field(apps15, retimed, "expires_at") == _TS + timedelta(days=400)

    def test_rollback_still_reverts_a_deletion_pending_row(self, at_0015):
        """The backfill's own three status mappings all still revert."""
        apps = at_0015
        UPC = apps.get_model("identity", "UserPersonalContext")
        upc = UPC.objects.create(user_id=uuid.uuid4())
        pending = _row(
            apps,
            upc,
            delete_requested_at=_TS + timedelta(days=1),
            deletion_reason="user_delete",
        )
        deleted = _row(
            apps,
            upc,
            soft_deleted_at=_TS + timedelta(days=2),
            deletion_reason="user_delete",
        )

        _executor().migrate([_MIG_0016])
        apps16 = _apps_at(_MIG_0016)
        assert _field(apps16, pending, "status") == "deletion_pending"
        assert _field(apps16, deleted, "status") == "deleted"

        _executor().migrate([_MIG_0015])
        apps15 = _apps_at(_MIG_0015)
        assert _field(apps15, pending, "status") is None
        assert _field(apps15, deleted, "status") is None
