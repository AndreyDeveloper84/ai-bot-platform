"""DRF-1551 — a migration test must hand the worker back a complete graph.

Five test modules drive the real ``MigrationExecutor`` down to a node in the
middle of the graph and back. Each of them used to come back to a hand-typed
node — the head of *its own* app. That is not the same thing as the head of
the graph, because Django's dependencies cross app boundaries::

    apps/handoff/0002  ->  ("identity", "0020_drop_userpreferences_allergies")
    apps/catalog/0016  ->  ("identity", "0020_drop_userpreferences_allergies")
    apps/identity/0021 ->  ("consent",  "0003_backfill_memory_green_consent")

Rolling ``identity`` down to 0015 therefore unapplies ``handoff/0002`` and
``catalog/0016`` too, and migrating ``identity`` forward to 0019 leaves them
unapplied. pytest-django gives an xdist worker one database for the whole
session, so from that moment on every test that lands on that worker and
touches ``handoff_handoffsilencenotice`` or ``handoff_admintask
.assigned_queue`` fails — in a different app, in a different PR, with an
error that names neither. That is the whole "random redness" behind PR #1399
and PR #1406.

These tests measure the two halves of the claim: that the damage is real,
and that ``restore_migration_head()`` actually undoes it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from tests.support.migration_graph import (
    graph_leaf_nodes,
    restore_migration_head,
    unapplied_leaf_nodes,
)

# The node the five modules roll below, and the node whose removal drags
# other apps down with it.
_BELOW = ("identity", "0019_memoryentry_lifecycle_constraints")
_CROSS_APP_ANCHOR = ("identity", "0020_drop_userpreferences_allergies")

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The five modules from DRF-1551. An explicit list on purpose: a new test
# that rolls the graph back has to be added here deliberately, by someone
# who has read why.
_MIGRATION_TEST_FILES = [
    "apps/identity/tests/test_memory_entry_step2_schema.py",
    "apps/identity/tests/test_memory_entry_step3_backfill.py",
    "apps/identity/tests/test_memory_entry_step3b_provenance.py",
    "apps/identity/tests/test_drf1371_allergies_removed.py",
    "apps/consent/tests/test_memory_green_backfill.py",
]


def _cross_app_victims() -> set[tuple[str, str]]:
    """Nodes of OTHER apps that a rollback below `_CROSS_APP_ANCHOR` unapplies."""

    graph = MigrationLoader(connection).graph
    return {
        node for node in graph.backwards_plan(_CROSS_APP_ANCHOR) if node[0] != _CROSS_APP_ANCHOR[0]
    }


class TestTheGraphIsActuallyCrossApp:
    """If this fails, the mechanism behind DRF-1551 has changed — read it."""

    def test_other_apps_depend_on_the_identity_anchor(self, db) -> None:
        victims = _cross_app_victims()
        assert victims, (
            "no app outside `identity` depends on "
            f"{_CROSS_APP_ANCHOR} any more — the cross-app rollback trap this "
            "module guards no longer exists in that shape. Re-derive the "
            "anchor before deleting these tests."
        )


@pytest.mark.django_db(transaction=True)
class TestRestoreMigrationHead:
    def test_rollback_unapplies_other_apps_and_restore_brings_them_back(self) -> None:
        """The damage is real, and the fix repairs all of it."""

        victims = _cross_app_victims()
        try:
            MigrationExecutor(connection).migrate([_BELOW])

            applied = set(MigrationLoader(connection).applied_migrations)
            assert not (victims & applied), (
                "rolling identity below its anchor was expected to unapply "
                f"{sorted(victims)}; the trap this guards has moved"
            )
            assert unapplied_leaf_nodes(), "the graph should be incomplete here"

            restore_migration_head()

            assert unapplied_leaf_nodes() == set()
            applied = set(MigrationLoader(connection).applied_migrations)
            assert victims <= applied
        finally:
            restore_migration_head()

    def test_the_old_single_app_restore_leaves_the_graph_broken(self) -> None:
        """The falsifier, kept as a test rather than as a paragraph.

        This is exactly what the five modules used to do in their `finally`
        and after their `yield`: come back to their own app's head. It leaves
        other apps' migrations off, which is the bug. If a future change ever
        makes this pass, `restore_migration_head()` has stopped being
        necessary and this whole module can go.
        """

        identity_head = next(n for n in graph_leaf_nodes() if n[0] == "identity")
        victims = _cross_app_victims()
        try:
            MigrationExecutor(connection).migrate([_BELOW])
            MigrationExecutor(connection).migrate([identity_head])  # the old way

            applied = set(MigrationLoader(connection).applied_migrations)
            assert victims - applied, (
                "migrating only identity forward was expected to leave "
                f"{sorted(victims)} unapplied — that is the defect DRF-1551 "
                "describes"
            )
            assert unapplied_leaf_nodes(), "the graph is still incomplete here"
        finally:
            restore_migration_head()

        assert unapplied_leaf_nodes() == set()

    def test_restore_at_head_is_a_noop_and_keeps_every_leaf(self) -> None:
        """Paired positive guard (DRF-1411): the fix is not a no-op that hides.

        Called on a healthy database it must leave a healthy database — not
        raise, not unapply anything, not drop a leaf.
        """

        before = set(MigrationLoader(connection).applied_migrations)
        restore_migration_head()
        after = set(MigrationLoader(connection).applied_migrations)

        assert graph_leaf_nodes() <= after
        assert before <= after, "restore must never unapply anything"
        assert unapplied_leaf_nodes() == set()


class TestNoHardCodedRestoreTargets:
    """The five modules must not go back to typing a node into a `finally`.

    Static, no database: runs in every job and costs nothing. It reads the
    source, not the behaviour — the cheap half of the guard; the self-check
    inside `restore_migration_head()` is the expensive half.
    """

    @staticmethod
    def _restore_regions(tree: ast.AST) -> list[list[ast.stmt]]:
        """Statement blocks that exist to put the database back.

        Two shapes in this repo: a `try/finally`, and everything after the
        `yield` of a generator fixture.
        """

        regions: list[list[ast.stmt]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try) and node.finalbody:
                regions.append(node.finalbody)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = node.body
                for i, stmt in enumerate(body):
                    is_yield = isinstance(stmt, ast.Expr) and isinstance(
                        stmt.value, (ast.Yield, ast.YieldFrom)
                    )
                    if is_yield and i + 1 < len(body):
                        regions.append(body[i + 1 :])
        return regions

    @staticmethod
    def _calls(stmts: list[ast.stmt]) -> set[str]:
        names: set[str] = set()
        for stmt in stmts:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute):
                        names.add(func.attr)
                    elif isinstance(func, ast.Name):
                        names.add(func.id)
        return names

    @pytest.mark.parametrize("relpath", _MIGRATION_TEST_FILES)
    def test_restore_goes_through_the_helper(self, relpath: str) -> None:
        source = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
        tree = ast.parse(source)

        assert "restore_migration_head" in source, (
            f"{relpath} drives MigrationExecutor but never restores the graph "
            "through the helper — see DRF-1551"
        )

        regions = self._restore_regions(tree)
        assert regions, f"{relpath}: no try/finally or post-yield restore found"

        restoring = [r for r in regions if "restore_migration_head" in self._calls(r)]
        assert restoring, f"{relpath}: no restore region calls restore_migration_head()"
        for region in restoring:
            assert "migrate" not in self._calls(region), (
                f"{relpath}: a restore region still calls .migrate() with a "
                "hand-typed target. Restoring one app's head leaves every "
                "other app that depends on it unapplied (DRF-1551)."
            )

    @pytest.mark.parametrize("relpath", _MIGRATION_TEST_FILES)
    def test_the_module_still_rolls_the_graph_back(self, relpath: str) -> None:
        """Paired positive guard (DRF-1411).

        The cheapest way to make the redness disappear is to stop rolling
        back at all — and then these modules would assert nothing about the
        migrations they exist to test. They must still drive the executor.
        """

        source = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
        assert "MigrationExecutor" in source, f"{relpath} no longer runs migrations"
        tree = ast.parse(source)
        migrate_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "migrate"
        ]
        assert migrate_calls, (
            f"{relpath} stopped calling executor.migrate() — the migration "
            "test has been hollowed out, not fixed"
        )
