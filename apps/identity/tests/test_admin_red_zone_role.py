"""Admin red-zone DB-role assertion tests (#572 / Sprint 1 Track A AS6).

Per spec `docs/specs/memory-entry-schema.md` §11 + ADR-0011 §10.

The AST lint (`tools/lint/red_zone_guard.py`) allowlists `apps/identity/
admin.py`, so it cannot enforce that the admin runs as the RLS-subject role
`ayla_app`. `apps.identity.admin.assert_admin_db_role()` is the runtime guard
that closes that gap on the admin-serving path. These tests lock its behaviour
plus the underlying role separation (migration 0008): `ayla_ops` must be denied
direct access to the red-zone base table.

# Postgres-only

DB-level roles + RLS + GRANTs only exist on Postgres. On SQLite (local dev
default) `assert_admin_db_role()` is a no-op and these tests are skipped — the
application-layer defence (RedZoneReader accessor) covers the SQLite flow.
"""

from __future__ import annotations

import pytest
from django.db import ProgrammingError, connection, transaction

from apps.identity.admin import RedZoneAdminRoleError, assert_admin_db_role

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="DB role separation + RLS are Postgres-only.",
    ),
]


def test_guard_passes_for_ayla_app() -> None:
    """Connected as `ayla_app` (the RLS-subject app role) → no error."""
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ROLE ayla_app")
    # Must not raise — ayla_app is the expected serving role. The guard
    # returns None; the assertion is simply that it does not raise.
    assert_admin_db_role()


def test_guard_raises_for_ayla_ops() -> None:
    """Connected as `ayla_ops` (read-only debug role) → fail loud.

    Simulates a misconfigured deployment where the admin process connects as
    `ayla_ops` instead of `ayla_app`. The guard must refuse rather than serve,
    so the operator fixes credentials instead of risking an un-gated red read.
    """
    with connection.cursor() as cur:
        cur.execute("SET LOCAL ROLE ayla_ops")
    with pytest.raises(RedZoneAdminRoleError):
        assert_admin_db_role()


def test_ayla_ops_denied_on_red_zone_base_table() -> None:
    """RLS-style denial: `ayla_ops` has no grant on the base table.

    Role separation (migration 0008) gives `ayla_ops` SELECT only on the
    `memory_entry_safe` view, never on `identity_memoryentry` directly. A
    direct base-table read therefore raises «permission denied» — the DB-level
    half of the defence the admin guard backs up at the app level.
    """
    with pytest.raises(ProgrammingError):
        # Nested atomic so the aborted-transaction state from the denied query
        # is confined to this savepoint and doesn't break test teardown.
        with transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("SET LOCAL ROLE ayla_ops")
                cur.execute("SELECT 1 FROM identity_memoryentry LIMIT 1")
