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
from tests.support.migration_moves import (
    forward_moves_to_pinned_nodes,
    runtime_access_below_head,
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
            calls = self._calls(region)
            # Presence before absence, on the same `calls`: "no .migrate()
            # here" means nothing unless this region provably has calls in it
            # at all (DRF-1406's rule).
            assert "restore_migration_head" in calls, (
                f"{relpath}: restore region does not call restore_migration_head()"
            )
            assert "migrate" not in calls, (
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


# --- DRF-1554: the moves FORWARD, which the restore guard above cannot see ---

#: Real nodes, so direction resolves against the real graph. `_LOW` is below
#: `_HIGH` in `consent`; if that ever stops being true these fixtures fail
#: loudly rather than passing vacuously.
_LOW = ("consent", "0002_alter_consentrecord_consent_type")
_HIGH = ("consent", "0003_backfill_memory_green_consent")

_SYNTHETIC_PREAMBLE = f"""
_A = {_LOW!r}
_B = {_HIGH!r}


@pytest.fixture
def at_a():
    _executor().migrate([_A])
    yield
    restore_migration_head()
"""


def _violating(moves) -> list[str]:
    return [f"{m.function}:{m.lineno} -> {m.target}" for m in moves if m.is_violation]


def _violations(source: str) -> list[str]:
    moves, _ = forward_moves_to_pinned_nodes(source)
    return _violating(moves)


class TestForwardMoveClassifier:
    """The classifier itself, on sources written to be unambiguous.

    The repo's own tests are the subject of the next class; these pin the
    RULE, so a future refactor of the classifier cannot quietly stop
    distinguishing the two directions.
    """

    def test_a_forward_move_to_a_pinned_node_is_reported(self) -> None:
        """The falsifier. This is the shape DRF-1554 was filed about."""

        source = (
            _SYNTHETIC_PREAMBLE
            + """

def test_walks_forward(at_a):
    _executor().migrate([_B])
    assert ConsentRecord.all_tenants.count() == 1
"""
        )
        reported = _violations(source)
        assert len(reported) == 1, reported
        assert reported[0].startswith("test_walks_forward:")
        assert str(_HIGH) in reported[0]

    def test_a_backward_move_is_not_reported(self) -> None:
        """Paired positive guard (DRF-1411).

        Rolling back to a hand-typed node is how every reversibility test in
        this repo is written. A guard that flagged it would leave them
        unwritable, and the cheapest way to satisfy such a guard is to delete
        the rollback — i.e. to delete the test's whole point.
        """

        source = (
            _SYNTHETIC_PREAMBLE
            + """

def test_rolls_back():
    _executor().migrate([_A])
    assert ConsentRecord.all_tenants.count() == 0
"""
        )
        moves, classified = forward_moves_to_pinned_nodes(source)
        assert classified >= 1, "the classifier saw nothing — it proves nothing"
        # Presence before absence, over the same `moves` (DRF-1406/1411):
        # «no violations» is worth nothing unless the move was actually seen.
        assert [m.direction for m in moves if m.function == "test_rolls_back"] == ["backward"]
        assert _violating(moves) == []

    def test_a_forward_move_that_declares_historical_intent_is_allowed(self) -> None:
        """Measured exception, not a loophole — see `migration_moves` docstring.

        `apps/identity/tests/test_memory_entry_step3_backfill.py` writes rows
        through the 0016-era registry; at the graph head migration 0019's
        `memory_entry_explicit_requires_provenance` CHECK rejects them.
        Sitting below head IS the test.
        """

        source = (
            _SYNTHETIC_PREAMBLE
            + """

def test_forward_but_historical(at_a):
    _executor().migrate([_B])
    apps = _apps_at(_B)
    assert apps is not None
"""
        )
        moves, _ = forward_moves_to_pinned_nodes(source)
        assert [m.direction for m in moves if m.function == "test_forward_but_historical"] == [
            "forward"
        ]
        assert _violating(moves) == []

    def test_the_same_constant_is_judged_by_direction_not_by_name(self) -> None:
        """`_B` is a bug in one test and correct in the other, same file."""

        source = (
            _SYNTHETIC_PREAMBLE
            + """

def test_forward_to_b(at_a):
    _executor().migrate([_B])


def test_back_to_b_from_head():
    _executor().migrate([_B])
    _executor().migrate([_A])
"""
        )
        reported = _violations(source)
        assert len(reported) == 1
        assert reported[0].startswith("test_forward_to_b:")


class TestNoForwardMovesToPinnedNodes:
    """DRF-1554. The five modules must not walk forward and stop short.

    Walking one app forward leaves every app that depends on it unapplied for
    the REST OF THIS TEST — the fixture's restore repairs the next test, not
    this one. Where the test then reads through the runtime ORM, it reads a
    live model against a truncated schema.
    """

    @pytest.mark.parametrize("relpath", _MIGRATION_TEST_FILES)
    def test_the_classifier_can_see_this_file(self, relpath: str) -> None:
        """Presence before absence (DRF-1406/1411).

        «No forward violations» means nothing if the classifier resolved no
        moves at all — a renamed helper or a computed target would make every
        file silently clean.
        """

        source = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
        _, classified = forward_moves_to_pinned_nodes(source)
        assert classified >= 1, (
            f"{relpath}: the direction classifier resolved no migrate() "
            "target. A clean result here would be blindness, not health."
        )

    @pytest.mark.parametrize("relpath", _MIGRATION_TEST_FILES)
    def test_no_forward_move_stops_short_of_the_graph_head(self, relpath: str) -> None:
        source = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
        moves, classified = forward_moves_to_pinned_nodes(source)
        assert classified >= 1
        # Paired positive guard, and the presence half of DRF-1406's rule:
        # «no forward violations» proves nothing over a file whose moves this
        # analysis never resolved.
        assert any(m.direction == "backward" for m in moves), (
            f"{relpath}: no backward move left — the reversibility this file "
            "exists to prove has been removed, not fixed (DRF-1411)."
        )
        assert _violating(moves) == [], (
            f"{relpath}: a test walks the graph FORWARD to a hand-typed node "
            "and then keeps working through the runtime ORM. Walking one app "
            "forward does not re-apply the apps that depend on it — they stay "
            "off until this test ends. Use restore_migration_head(), or name "
            "the node to project_state()/_apps_at() if you deliberately mean "
            "to read through that node's historical registry (DRF-1554)."
        )


class TestNoRuntimeModelUseWhileRolledBack:
    """DRF-1554, second finding — and the one that actually bit.

    The six consent tests never reached their forward move. They seeded rows
    through `BotUser.all_tenants.create(...)` while the fixture held the graph
    at `consent/0002`, and PR #1399's `identity/0022` put a column on
    `BotUser` itself.

    No ordering of restores can fix that: `identity/0021` declares
    `consent/0003` a dependency, so «consent at 0002 AND identity at 0022» is
    not a reachable state of the graph. The cure is what you touch while you
    are down — seed at head and roll back with the data already there, or go
    through the historical registry, whose model has exactly the columns that
    exist at that node.
    """

    @pytest.mark.parametrize("relpath", _MIGRATION_TEST_FILES)
    def test_nothing_touches_a_runtime_model_below_the_head(self, relpath: str) -> None:
        source = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
        findings, _ = runtime_access_below_head(source)
        assert findings == [], (
            f"{relpath}: a statement uses a runtime model while the graph is "
            "rolled back. The runtime model carries the columns of the CURRENT "
            "code; below the head those columns may not exist yet, and no "
            "restore order can help — the state is unreachable by "
            "construction. Seed at head before rolling back, or read through "
            "project_state(node).apps (DRF-1554)."
        )

    def test_the_analysis_reaches_the_dangerous_state_at_all(self) -> None:
        """Presence before absence (DRF-1406/1411).

        Across the five files at least one statement must actually execute
        below the head — otherwise «no findings» would mean the analysis never
        entered the state it exists to police.
        """

        seen = 0
        for relpath in _MIGRATION_TEST_FILES:
            source = (_REPO_ROOT / relpath).read_text(encoding="utf-8")
            _, below = runtime_access_below_head(source)
            seen += below
        assert seen > 0, (
            "no statement in any of the five files was analysed below the "
            "graph head — a clean result here would be blindness"
        )

    def test_a_runtime_write_below_the_head_is_reported(self) -> None:
        """The falsifier: the exact shape that broke the six consent tests."""

        source = """
from apps.identity.models import BotUser

_A = ("consent", "0002_alter_consentrecord_consent_type")


@pytest.fixture
def at_a():
    _executor().migrate([_A])
    yield
    restore_migration_head()


def _seed():
    return BotUser.all_tenants.create(channel="max")


def test_seeds_while_rolled_back(at_a):
    bu = _seed()
    assert bu is not None
"""
        findings, below = runtime_access_below_head(source)
        assert below > 0
        assert [f.function for f in findings] == ["test_seeds_while_rolled_back"]
        assert findings[0].via == "_seed()"

    def test_the_same_write_at_the_head_is_not_reported(self) -> None:
        """Paired positive guard (DRF-1411).

        Seeding through the runtime models is the deliberate choice this file
        documents — a historical manager is not tenant-scoped, and seeding
        through one would hide exactly the difference the tests exercise. The
        guard must object to *where*, never to *what*.
        """

        source = """
from apps.identity.models import BotUser

_A = ("consent", "0002_alter_consentrecord_consent_type")


@pytest.fixture
def rerun():
    def _rerun():
        _executor().migrate([_A])
        restore_migration_head()

    return _rerun


def _seed():
    return BotUser.all_tenants.create(channel="max")


def test_seeds_at_head_then_reruns(rerun):
    bu = _seed()
    rerun()
    assert bu is not None
"""
        findings, _ = runtime_access_below_head(source)
        assert findings == [], findings
