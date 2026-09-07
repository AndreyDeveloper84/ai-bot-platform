"""Classify ``MigrationExecutor.migrate()`` calls in a test module by DIRECTION.

DRF-1554, the remainder of DRF-1551.

DRF-1551 fixed **restoration** — the `finally` and the statements after a
fixture's `yield`. It missed the moves **forward in the middle of a test**::

    _executor().migrate([_MIG_0002])   # fixture: roll consent back
    ...
    _executor().migrate([_MIG_0003])   # test: walk consent forward again

The second line walks *consent* forward and nothing else. ``identity/0021``
declares ``consent/0003`` a dependency, so the rollback took identity down
too, and this move does not bring it back: identity — and everything standing
on it — stays unapplied **until the end of this test**. The fixture's restore
repairs the *next* test, never this one. On `dev` that was silent, because
identity's leaf happened to be a migration these tests never read; PR #1399's
``identity/0022`` added a column to ``BotUser`` itself and six tests went red.

# Direction, not variable name

The same constant is a legitimate target in one test and a bug in another:
``_MIG_0003`` is where ``test_reverse_...`` rolls *back* from, and where the
other five walked *forward* to. So this module does not pattern-match names.
It reads the real migration graph, tracks where each test's database sits
statement by statement, and calls each move forward or backward.

* **Backward is allowed** and must stay allowed — reversibility tests are
  written with it, and a guard that forbade it would leave them unwritable.
* **Forward to a hand-typed node** is the defect... with one real exception,
  measured rather than assumed.

# The exception, and why it is not a loophole

Some tests position the database at a historical node *on purpose*, to write
and read through that node's historical registry
(``loader.project_state(node).apps``). Moving those to the graph head does not
make them stricter, it makes them impossible: measured 07.09.2026 on
``apps/identity/tests/test_memory_entry_step3_backfill.py``, two tests insert
rows through the 0016-era model, which has no ``provenance`` column — at head,
migration 0019's ``memory_entry_explicit_requires_provenance`` CHECK rejects
the insert. The historical position is the point of those tests.

So a forward move is accepted when the same function also names that node to
``project_state``/``_apps_at`` — i.e. it declares it keeps working in
historical space. A test that walks forward and then uses the **runtime** ORM
(the six consent tests) declares nothing, and is reported.

The second acceptance is about **resting**, not intent: a move the database
leaves again in the very next statement — ``migrate(0018)`` then
``migrate(0017)``, the shape of a reversibility probe — never exposes a
statement to the incomplete graph. What hurts is sitting there.

# What this does NOT do

Stated because a scanner that cannot see looks exactly like a clean tree.

* It resolves node tuples only from literal 2-tuples: a module constant, a
  class attribute (``self._HEAD``), or an inline literal. A target built at
  runtime is invisible, and :func:`forward_moves_to_pinned_nodes` reports how
  many moves it managed to classify so a caller can refuse to trust a zero.
* Position is tracked per function, seeded from the rollback fixture the test
  takes by name. A fixture in a `conftest.py`, or a rollback hidden in a
  helper, is not seen.
* It does not know whether the runtime ORM is actually touched after the move
  — only whether the test declared historical intent.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache

from django.db.migrations.loader import MigrationLoader

Node = tuple[str, str]


@dataclass(frozen=True)
class Move:
    """One ``executor.migrate([target])`` call, placed on the graph."""

    lineno: int
    function: str
    target: Node
    direction: str  # "forward" | "backward" | "same" | "unknown"
    declares_historical: bool
    resting: bool

    @property
    def is_violation(self) -> bool:
        """Forward, rested on, and not declared as historical work."""

        return self.direction == "forward" and self.resting and not self.declares_historical


@lru_cache(maxsize=1)
def _graph():
    """The migration graph as it exists on disk. No database is touched."""

    return MigrationLoader(None, ignore_no_migrations=True).graph


def _as_node(expr: ast.expr, scopes: list[dict[str, Node]]) -> Node | None:
    """Resolve an expression to an ``(app_label, migration_name)`` tuple."""

    if isinstance(expr, ast.Tuple) and len(expr.elts) == 2:
        a, b = expr.elts
        if isinstance(a, ast.Constant) and isinstance(b, ast.Constant):
            if isinstance(a.value, str) and isinstance(b.value, str):
                return (a.value, b.value)
        return None
    name: str | None = None
    if isinstance(expr, ast.Name):
        name = expr.id
    elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        # `self._HEAD` / `cls._PREV`
        name = expr.attr
    if name is None:
        return None
    for scope in reversed(scopes):
        if name in scope:
            return scope[name]
    return None


def _tuple_constants(body: list[ast.stmt]) -> dict[str, Node]:
    """Name -> node, for `NAME = ("app", "0001_x")` assignments in `body`."""

    out: dict[str, Node] = {}
    for stmt in body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not isinstance(target, ast.Name):
            continue
        node = _as_node(stmt.value, [])
        if node is not None:
            out[target.id] = node
    return out


def _migrate_target(call: ast.Call) -> ast.expr | None:
    """The single element of `...migrate([X])`, if the call has that shape."""

    if not isinstance(call.func, ast.Attribute) or call.func.attr != "migrate":
        return None
    if len(call.args) != 1:
        return None
    arg = call.args[0]
    if isinstance(arg, (ast.List, ast.Tuple)) and len(arg.elts) == 1:
        return arg.elts[0]
    return None


def _historical_nodes(func: ast.AST, scopes: list[dict[str, Node]]) -> set[Node]:
    """Nodes this function hands to `project_state` / `_apps_at`.

    Naming a node to either is the function saying «I am reading through this
    node's historical registry», which is the one legitimate reason to sit
    below the graph head.
    """

    declared: set[Node] = set()
    for call in ast.walk(func):
        if not isinstance(call, ast.Call):
            continue
        fname = (
            call.func.attr
            if isinstance(call.func, ast.Attribute)
            else call.func.id
            if isinstance(call.func, ast.Name)
            else None
        )
        if fname not in {"project_state", "_apps_at"}:
            continue
        for arg in call.args:
            candidates = arg.elts if isinstance(arg, (ast.List, ast.Tuple)) else [arg]
            # A bare 2-tuple of strings IS the node, not a list of them.
            if _as_node(arg, scopes) is not None:
                candidates = [arg]
            for element in candidates:
                node = _as_node(element, scopes)
                if node is not None:
                    declared.add(node)
    return declared


@lru_cache(maxsize=None)
def _ancestors(node: Node) -> frozenset[Node]:
    """Everything that must be applied to reach `node`, `node` included."""

    return frozenset(_graph().forwards_plan(node))


def _direction(current: Node | None, target: Node) -> str:
    graph = _graph()
    if current is None or current == target:
        return "same"
    if target not in graph.nodes or current not in graph.nodes:
        return "unknown"
    if target in _ancestors(current):
        return "backward"
    if current in _ancestors(target):
        return "forward"
    return "unknown"


_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers")


def _flatten(node: ast.AST) -> list[ast.stmt]:
    """Every statement under `node`, in source order, blocks inlined."""

    out: list[ast.stmt] = []
    for field in _BLOCK_FIELDS:
        for stmt in getattr(node, field, []) or []:
            if isinstance(stmt, ast.excepthandler):
                out.extend(_flatten(stmt))
                continue
            out.append(stmt)
            if any(getattr(stmt, f, None) for f in _BLOCK_FIELDS):
                out.extend(_flatten(stmt))
    return out


def _own_nodes(stmt: ast.AST) -> list[ast.AST]:
    """`stmt`'s own expression tree, WITHOUT the bodies of nested blocks.

    Nested statements arrive in the flat list on their own, so a `try:` must
    not also claim the `migrate()` calls sitting inside it — counting them
    twice moves the tracked position twice and turns a real forward move into
    a no-op «same».
    """

    out: list[ast.AST] = []
    stack: list[ast.AST] = [stmt]
    while stack:
        node = stack.pop()
        out.append(node)
        for field, value in ast.iter_fields(node):
            if field in _BLOCK_FIELDS:
                continue
            values = value if isinstance(value, list) else [value]
            stack.extend(v for v in values if isinstance(v, ast.AST))
    return out


def _restores(stmt: ast.stmt) -> bool:
    """Does this statement put the graph back at its leaves?"""

    for node in _own_nodes(stmt):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name == "restore_migration_head":
                return True
    return False


def _migrate_calls(stmt: ast.stmt) -> list[ast.Call]:
    return sorted(
        (n for n in _own_nodes(stmt) if isinstance(n, ast.Call) and _migrate_target(n) is not None),
        key=lambda n: (n.lineno, n.col_offset),
    )


def _walk_moves(
    func: ast.AST,
    scopes: list[dict[str, Node]],
    position: dict[str, Node],
) -> list[tuple[int, Node, str, bool]]:
    """Statement-ordered `migrate()` targets: line, node, direction, resting.

    «Resting» is the half that matters. A move the database immediately
    leaves again — `migrate(0018)` followed straight away by
    `migrate(0017)`, the shape of a reversibility probe — never exposes a
    single statement to the incomplete graph. A move the test then *sits* on
    does, for every statement to the end of that test.

    `position` is mutated, so a caller can read the exit position of a
    fixture's pre-`yield` half.
    """

    graph = _graph()
    statements = _flatten(func)
    moves: list[tuple[int, Node, str, bool]] = []
    for i, stmt in enumerate(statements):
        calls = _migrate_calls(stmt)
        for j, call in enumerate(calls):
            arg_expr = _migrate_target(call)
            target = _as_node(arg_expr, scopes) if arg_expr is not None else None
            if target is None:
                continue
            app = target[0]
            current = position.get(app)
            if current is None:
                leaves = [n for n in graph.leaf_nodes() if n[0] == app]
                current = leaves[0] if len(leaves) == 1 else None
            more_here = j + 1 < len(calls)
            next_stmt_moves = (
                bool(_migrate_calls(statements[i + 1])) if i + 1 < len(statements) else False
            )
            resting = not (more_here or next_stmt_moves)
            moves.append((call.lineno, target, _direction(current, target), resting))
            position[app] = target
        if _restores(stmt):
            position.clear()
    return moves


def _statements_before_yield(func: ast.AST) -> ast.Module:
    """A fixture's setup half — everything up to and including the `yield`."""

    body = list(getattr(func, "body", []))
    for i, stmt in enumerate(body):
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Yield, ast.YieldFrom)):
            return ast.Module(body=body[: i + 1], type_ignores=[])
    return ast.Module(body=body, type_ignores=[])


def forward_moves_to_pinned_nodes(source: str) -> tuple[list[Move], int]:
    """Classify every `migrate([node])` in `source`.

    Returns ``(moves, classified_count)``. The count is the honesty channel:
    a caller that gets zero classified moves out of a file that clearly
    migrates should treat the clean result as «could not see», not «clean».
    """

    tree = ast.parse(source)
    module_consts = _tuple_constants(tree.body)

    functions: dict[str, tuple[ast.AST, dict[str, Node]]] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[stmt.name] = (stmt, {})
        elif isinstance(stmt, ast.ClassDef):
            class_consts = _tuple_constants(stmt.body)
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[sub.name] = (sub, class_consts)

    # Where each rollback fixture leaves the database for the tests that take it.
    fixture_exit: dict[str, dict[str, Node]] = {}
    for name, (func, class_consts) in functions.items():
        exit_position: dict[str, Node] = {}
        _walk_moves(_statements_before_yield(func), [module_consts, class_consts], exit_position)
        if exit_position:
            fixture_exit[name] = exit_position

    moves: list[Move] = []
    classified = 0
    for name, (func, class_consts) in functions.items():
        scopes = [module_consts, class_consts]
        position: dict[str, Node] = {}
        for arg in _func_args(func):
            seeded = fixture_exit.get(arg.arg)
            if seeded:
                position.update(seeded)
        declared = _historical_nodes(func, scopes)
        for lineno, target, direction, resting in _walk_moves(func, scopes, position):
            if direction in {"forward", "backward"}:
                classified += 1
            moves.append(
                Move(
                    lineno=lineno,
                    function=name,
                    target=target,
                    direction=direction,
                    declares_historical=target in declared,
                    resting=resting,
                )
            )
    return moves, classified


# --------------------------------------------------------------------------
# The other half of the defect class: writing through a RUNTIME model while
# the graph is rolled back (DRF-1554, second finding).
# --------------------------------------------------------------------------
#
# The forward move is only half the story. `apps/consent/tests/
# test_memory_green_backfill.py` never even reached its forward move: it
# seeded rows through `BotUser.all_tenants.create(...)` while the fixture
# held the graph at `consent/0002`, and PR #1399's `identity/0022` added a
# column to `BotUser` itself. Measured 07.09.2026 against a probe migration
# of that shape: all six tests died in the seeding helper.
#
# And no ordering of restores can fix it. `identity/0021` declares
# `consent/0003` a dependency, so «consent at 0002 AND identity at 0022» is
# not a reachable state of the graph. The cure is not *when* you restore, it
# is *what* you touch while you are down: either seed at head and roll back
# with the data already there, or read and write through the historical
# registry, whose model has exactly the columns that exist at that node.

#: Manager attributes that mean «the live table, as the running app sees it».
_RUNTIME_MANAGERS = frozenset({"objects", "all_tenants", "_default_manager", "_base_manager"})


@dataclass(frozen=True)
class RuntimeAccess:
    """A statement that uses a runtime model while the graph is not at head."""

    lineno: int
    function: str
    via: str


def _imported_models(tree: ast.Module) -> set[str]:
    """Names bound by `from apps.<app>.models import X` at module level."""

    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.ImportFrom) and (stmt.module or "").startswith("apps."):
            if not (stmt.module or "").endswith(".models"):
                continue
            for alias in stmt.names:
                names.add(alias.asname or alias.name)
    return names


def _func_args(func: ast.AST) -> list[ast.arg]:
    """A function node's positional parameters, empty for anything else."""

    args = getattr(func, "args", None)
    return list(args.args) if isinstance(args, ast.arguments) else []


def _rebound_names(func: ast.AST) -> set[str]:
    """Names the function binds itself, so a module import no longer applies.

    This is not pedantry, it is the whole difference between the two shapes.
    ``apps/identity/tests/test_memory_entry_step3b_provenance.py`` imports the
    runtime ``MemoryEntry`` at module level AND writes::

        MemoryEntry = apps.get_model("identity", "MemoryEntry")
        MemoryEntry.objects.create(...)

    That second ``MemoryEntry`` is the HISTORICAL model — precisely the safe
    thing to use below the head. Reading `.objects` off the name without
    noticing the rebinding would report the one file that is already doing it
    right.
    """

    names: set[str] = set()
    for arg in _func_args(func):
        names.add(arg.arg)
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not func:
            names.add(node.name)
    return names


def _touches_runtime(stmt: ast.stmt, models: set[str], helpers: set[str]) -> str | None:
    """`Model.objects`-style access, or a call to a helper that has one."""

    for node in _own_nodes(stmt):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in models
            and node.attr in _RUNTIME_MANAGERS
        ):
            return f"{node.value.id}.{node.attr}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in helpers:
                return f"{node.func.id}()"
    return None


def runtime_access_below_head(source: str) -> tuple[list[RuntimeAccess], int]:
    """Statements that touch a runtime model while the graph is rolled back.

    Returns ``(findings, statements_seen_below_head)``. The second number is
    the honesty channel again: no findings out of zero statements below head
    means the analysis never got into the dangerous state, not that the file
    is safe there.
    """

    tree = ast.parse(source)
    module_consts = _tuple_constants(tree.body)
    models = _imported_models(tree)

    functions: dict[str, tuple[ast.AST, dict[str, Node]]] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[stmt.name] = (stmt, {})
        elif isinstance(stmt, ast.ClassDef):
            class_consts = _tuple_constants(stmt.body)
            for sub in stmt.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[sub.name] = (sub, class_consts)

    # One level of interprocedural reach: a module-level helper that itself
    # touches a runtime manager makes its callers runtime-touching too. That
    # is exactly the `_bot_user()` / `_grant()` shape that hid the defect.
    helpers: set[str] = set()
    for name, (func, _) in functions.items():
        if name.startswith("test_"):
            continue
        live = models - _rebound_names(func)
        if any(_touches_runtime(st, live, set()) for st in _flatten(func)):
            helpers.add(name)

    fixture_exit: dict[str, dict[str, Node]] = {}
    for name, (func, class_consts) in functions.items():
        exit_position: dict[str, Node] = {}
        _walk_moves(_statements_before_yield(func), [module_consts, class_consts], exit_position)
        if exit_position:
            fixture_exit[name] = exit_position

    graph = _graph()
    leaves = {app: node for app, node in ((n[0], n) for n in graph.leaf_nodes())}

    findings: list[RuntimeAccess] = []
    below_head_statements = 0
    for name, (func, class_consts) in functions.items():
        scopes = [module_consts, class_consts]
        position: dict[str, Node] = {}
        for arg in _func_args(func):
            position.update(fixture_exit.get(arg.arg) or {})
        live = models - _rebound_names(func)
        for stmt in _flatten(func):
            below = any(node != leaves.get(app) for app, node in position.items())
            if below:
                below_head_statements += 1
                via = _touches_runtime(stmt, live, helpers)
                if via is not None:
                    findings.append(RuntimeAccess(lineno=stmt.lineno, function=name, via=via))
            # The statement's own moves take effect for the statements AFTER it.
            for call in _migrate_calls(stmt):
                arg_expr = _migrate_target(call)
                target = _as_node(arg_expr, scopes) if arg_expr is not None else None
                if target is not None:
                    position[target[0]] = target
            if _restores(stmt):
                position.clear()
    return findings, below_head_statements
