"""Tests for the GIN-index migration #679.

Migration module: ``apps/audit/migrations/0003_auditlog_payload_gin_concurrently.py``.

Two failure modes pinned here:

1. **Postgres path emits the right SQL.** The migration's forward
   operation builds a ``CREATE INDEX CONCURRENTLY ... USING gin
   (payload jsonb_path_ops)`` statement. A future refactor that
   accidentally drops ``CONCURRENTLY`` (→ ACCESS EXCLUSIVE lock during
   prod deploy → outage) or swaps ``jsonb_path_ops`` for default
   ``jsonb_ops`` (→ ~2× larger index, slower writes) would silently
   ship.

2. **Non-Postgres path is a true no-op.** Tests run on SQLite. If
   the vendor guard ever regresses to «emit SQL anyway», SQLite would
   reject the GIN syntax and every audit test would fail. The current
   guard returns early on non-Postgres — verified here.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

# Migration module name starts with a digit — can't be imported as a
# regular attribute access. ``importlib`` is the standard escape hatch.
_MIGRATION = importlib.import_module("apps.audit.migrations.0003_auditlog_payload_gin_concurrently")


@pytest.fixture
def fake_schema_editor():
    """Mimic ``schema_editor`` API surface the migration touches:

    * ``schema_editor.connection.vendor`` — the dispatch switch
    * ``schema_editor.execute(sql)`` — captured for assertion
    """

    editor = MagicMock()
    editor.execute = MagicMock()
    return editor


class TestForwardOperation:
    def test_postgres_emits_concurrently_gin_jsonb_path_ops(self, fake_schema_editor) -> None:
        fake_schema_editor.connection.vendor = "postgresql"
        _MIGRATION._create_gin_index_vendor_aware(apps=None, schema_editor=fake_schema_editor)

        # Exactly one SQL statement executed.
        assert fake_schema_editor.execute.call_count == 1
        sql = fake_schema_editor.execute.call_args[0][0]

        # Load-bearing literals — a refactor that drops any of these
        # would silently regress prod performance or deploy safety.
        assert "CREATE INDEX CONCURRENTLY" in sql, (
            "MUST use CONCURRENTLY to avoid ACCESS EXCLUSIVE lock during deploy"
        )
        assert "IF NOT EXISTS" in sql, (
            "MUST use IF NOT EXISTS so a retry after partial failure is safe"
        )
        assert "audit_auditlog" in sql, "target table"
        assert "USING gin" in sql, "GIN index method (vs default B-tree)"
        assert "payload" in sql, "indexed column"
        # Default ``jsonb_ops`` opclass — supports BOTH ``?`` (Admin
        # filter, primary path) AND ``@>`` (runbook raw-SQL fallback).
        # An earlier draft used ``jsonb_path_ops`` but adversarial review
        # caught the mismatch: that opclass doesn't index ``?`` →
        # Admin filter falls back to seq scan, defeating the migration.
        assert "jsonb_path_ops" not in sql, (
            "MUST NOT use jsonb_path_ops — does not index the ? operator "
            "that the Admin filter uses. Use default jsonb_ops (no "
            "explicit opclass = default)."
        )
        assert "auditlog_payload_gin" in sql, "deterministic index name for the DROP path"

    def test_sqlite_is_noop(self, fake_schema_editor) -> None:
        fake_schema_editor.connection.vendor = "sqlite"
        _MIGRATION._create_gin_index_vendor_aware(apps=None, schema_editor=fake_schema_editor)
        # No SQL emitted on non-Postgres vendors.
        fake_schema_editor.execute.assert_not_called()

    def test_other_vendor_is_noop(self, fake_schema_editor) -> None:
        """Future MySQL / Oracle / CockroachDB support shouldn't fail
        the migration — GIN is Postgres-only."""

        fake_schema_editor.connection.vendor = "mysql"
        _MIGRATION._create_gin_index_vendor_aware(apps=None, schema_editor=fake_schema_editor)
        fake_schema_editor.execute.assert_not_called()


class TestReverseOperation:
    def test_postgres_emits_concurrently_drop(self, fake_schema_editor) -> None:
        fake_schema_editor.connection.vendor = "postgresql"
        _MIGRATION._drop_gin_index_vendor_aware(apps=None, schema_editor=fake_schema_editor)

        assert fake_schema_editor.execute.call_count == 1
        sql = fake_schema_editor.execute.call_args[0][0]
        # Mirror the CREATE pattern — CONCURRENTLY + IF EXISTS so the
        # reverse is also lock-safe AND idempotent.
        assert "DROP INDEX CONCURRENTLY" in sql
        assert "IF EXISTS" in sql
        assert "auditlog_payload_gin" in sql

    def test_sqlite_reverse_is_noop(self, fake_schema_editor) -> None:
        fake_schema_editor.connection.vendor = "sqlite"
        _MIGRATION._drop_gin_index_vendor_aware(apps=None, schema_editor=fake_schema_editor)
        fake_schema_editor.execute.assert_not_called()

    def test_other_vendor_reverse_is_noop(self, fake_schema_editor) -> None:
        """Symmetry pin with ``TestForwardOperation.test_other_vendor_is_noop``:
        the reverse vendor-guard must mirror the forward one — both
        skip non-Postgres backends. Without this assertion, an
        asymmetric refactor (e.g., reverse fires on MySQL while forward
        skips) would leave deploys inconsistent."""

        fake_schema_editor.connection.vendor = "mysql"
        _MIGRATION._drop_gin_index_vendor_aware(apps=None, schema_editor=fake_schema_editor)
        fake_schema_editor.execute.assert_not_called()


class TestMigrationAtomicity:
    def test_atomic_is_false(self) -> None:
        """``CREATE INDEX CONCURRENTLY`` MUST run outside a transaction —
        Postgres rejects it inside one. ``atomic = False`` on the
        migration is the load-bearing flag."""

        # Direct attribute access — pins the value so an autofix /
        # squash doesn't quietly flip it.
        assert _MIGRATION.Migration.atomic is False
