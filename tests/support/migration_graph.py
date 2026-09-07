"""Return a worker's test database to the head of the WHOLE migration graph.

DRF-1551. Tests that drive the real ``MigrationExecutor`` down to a node in
the middle of the graph must put the database back before the next test runs
— pytest-django hands one database to an xdist worker for the whole session,
so anything left unapplied is left unapplied for every test that lands on
that worker afterwards.

The trap is that «back» is not «the head of the app I rolled back». Django's
graph is cross-app: ``apps/handoff/0002`` and ``apps/catalog/0016`` both
declare ``("identity", "0020_drop_userpreferences_allergies")`` as a
dependency, so ``migrate([("identity", "0015")])`` unapplies them too.
Migrating ``identity`` forward to *its* head does not bring them back —
they are not identity's migrations. The worker then runs on a schema with no
``handoff_handoffsilencenotice`` table and no ``handoff_admintask
.assigned_queue`` column, and every later test that touches them fails with
an error that has nothing to do with the code under test.

Hence: restore to ``graph.leaf_nodes()`` — every app's head, read from the
graph at call time, never a tuple typed into a test file. A hard-coded target
is wrong the day someone adds a migration on top of it, and wrong silently.
"""

from __future__ import annotations

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader


def graph_leaf_nodes() -> set[tuple[str, str]]:
    """Every app's current head node, read from the migration files on disk."""

    return set(MigrationLoader(connection).graph.leaf_nodes())


def unapplied_leaf_nodes() -> set[tuple[str, str]]:
    """Graph leaves the database does not record as applied.

    Empty is the only healthy answer for a worker between tests.
    """

    loader = MigrationLoader(connection)
    return set(loader.graph.leaf_nodes()) - set(loader.applied_migrations)


def restore_migration_head() -> None:
    """Re-apply everything a mid-graph rollback unapplied, across all apps.

    Self-checking on purpose: the whole point of DRF-1551 is that an
    incomplete restore is invisible at the call site and only surfaces as
    somebody else's failing test, on another worker, in another PR. Failing
    loudly here names the culprit.
    """

    loader = MigrationLoader(connection)
    MigrationExecutor(connection).migrate(list(loader.graph.leaf_nodes()))

    missing = unapplied_leaf_nodes()
    if missing:  # pragma: no cover — a green suite never reaches this
        raise AssertionError(
            "restore_migration_head() left the migration graph incomplete; "
            "this worker's database is poisoned for every test after this "
            f"one. Unapplied leaves: {sorted(missing)}"
        )
