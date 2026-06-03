"""ADR-0009 G-series module-boundary AST guard (#1001 — Option B).

Enforces the G1–G10 architecture contracts (ADR-0009 module boundaries,
Codex integration audit) as **source-scoped forbidden import edges**.

# Why an AST linter and not ruff TID251

`ruff`'s `flake8-tidy-imports.banned-api` (TID251) bans a module
*globally* — for every importer. The G-series contracts are different:
they forbid an edge only from a **specific source package**. Example:
`apps/miniapp_api/` must not import `apps.booking.services.create`, but
the booking app itself obviously may. TID251 cannot express
"forbidden only from package X"; this guard can.

Division of labour (do not duplicate):
  - **ruff TID251** (`pyproject.toml`) — global bans: `legacy_notifications`
    static import from `apps/*` (the G1.2 contract). Stays in ruff.
  - **this guard** — source→target edges TID251 can't express (G2.1,
    G5.1, G6.2) + the cross-repo `psycopg2` ban (ADR-0009 rule 2).
  - **Option C** (Code Reviewer checklist) — contracts not cheaply
    AST-expressible (e.g. the G9 booking-ownership dual-source
    divergence, which is a runtime-semantics invariant, not an edge).

# What this guard detects

For every production `.py` file under a contract's `source_prefixes`,
it flags an import whose target matches the contract's
`forbidden_modules` (exact dotted module OR a submodule of it), unless
the `(contract_id, file, forbidden_root)` triple is in `BASELINE`.

Import shapes detected (the adversarial set #1001 / S5 asked for):
  - `import a.b.c`            / `import a.b.c as x`
  - `from a.b import c`       (target `a.b` AND `a.b.c`)
  - `from a.b import (c, d)`  (multi-line)
  - `from a.b.c import name`  / `... import name as y`
  - relative imports `from ..x import y` — resolved to absolute via the
    file's package path
  - imports inside `if TYPE_CHECKING:` blocks — **counted** (a real
    boundary crossing hidden behind TYPE_CHECKING is still a crossing)
  - `importlib.import_module("a.b.c")` / `__import__("a.b.c")` with a
    string-literal argument — the documented dynamic escape hatch is
    NOT a blind spot

# KNOWN LIMITATIONS (explicitly NOT detected)

Per the same tech-lead direction as `red_zone_guard.py` («do not chase
infinite obtuse patterns»):
  - **Runtime-variable module names**:
    `mod = cfg["m"]; importlib.import_module(mod)` — the name is not a
    source literal.
  - **`getattr`-style attribute walking** off an already-imported
    parent package.
  - **Re-export laundering**: importing a forbidden module via a third
    module that re-exports it (the edge to the *re-exporter* is what's
    visible; add a contract for it if it becomes a pattern).
These are accepted gaps — the guard is the cheap first line; Code
Review (Option C) + the runtime tenant/idempotency guards are the rest.

# Baseline (the file `.importlinter.baseline` never existed — #1001)

The G-series tickets referenced `.importlinter.baseline` /
`.importlinter.passing` files that were never created (roadmap item
A11 was skipped). `BASELINE` below is the real, in-code replacement:
the set of **currently-accepted** crossings on `origin/dev`, each tied
to its tracking issue. CI fails on any crossing NOT in the baseline
(new debt) and on any baseline entry that no longer matches (stale —
forces the line to be deleted when the site is migrated, ratcheting
the debt down).

# CLI usage

  python tools/lint/import_boundaries.py apps/

Exits 0 when clean. Exits 1 with one line per violation:
`file:line:col: [contract] message`.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# ── Contract registry ────────────────────────────────────────────────


@dataclass(frozen=True)
class Contract:
    """A forbidden-import-edge contract.

    A file under any of ``source_prefixes`` (repo-relative, POSIX, with
    trailing slash) may not import any module that equals — or is a
    submodule of — any entry in ``forbidden_modules``.
    """

    id: str
    issue: str
    source_prefixes: tuple[str, ...]
    forbidden_modules: tuple[str, ...]
    message: str


CONTRACTS: tuple[Contract, ...] = (
    Contract(
        id="G2.1-skills-no-yclients",
        issue="#928",
        source_prefixes=("apps/skills/",),
        forbidden_modules=("apps.integrations.yclients",),
        message=(
            "skills must not import YClients directly — booking state must go "
            "through Ayla's canonical gate via REST (ADR-0009 rule 5)."
        ),
    ),
    Contract(
        id="G5.1-api-no-booking-mutators",
        issue="#925/#968",
        source_prefixes=("apps/miniapp_api/", "apps/admin_api/"),
        forbidden_modules=(
            "apps.booking.services.create",
            "apps.booking.services.reschedule",
            "apps.booking.services.transitions",
            "apps.booking.services.feedback",
        ),
        message=(
            "client/admin API surfaces must not DB-write booking via the "
            "canonical mutators — call Ayla REST; the event consumer "
            "reconciles the projection (ADR-0009 rule 5)."
        ),
    ),
    Contract(
        id="G6.2-eventbus-consumer-narrow",
        issue="#927",
        source_prefixes=("apps/eventbus/consumers/",),
        forbidden_modules=("apps.skills", "apps.channels"),
        message=(
            "eventbus consumers must not invoke skills/channels in-process — "
            "enqueue a Celery task keyed on event_id so the outbound "
            "side-effect stays exactly-once (ADR-0009 rule 7, idempotency)."
        ),
    ),
    Contract(
        id="ADR9.2-no-cross-repo-psycopg2",
        issue="ADR-0009 rule 2",
        source_prefixes=("apps/",),
        forbidden_modules=("psycopg2",),
        message=(
            "apps/ must not import psycopg2 directly — no cross-repo DB "
            "access (ADR-0009 rule 2). Use the Django ORM or Ayla REST."
        ),
    ),
)

# ── Baseline: accepted crossings on origin/dev, each tied to its issue ─
# (contract_id, file POSIX relpath, forbidden_module_root)
BaselineKey = tuple[str, str, str]

BASELINE: frozenset[BaselineKey] = frozenset(
    {
        # G2.1 — skills → YClients (#928, 2 prod files; Phase 2.2 reroute via Ayla REST)
        ("G2.1-skills-no-yclients", "apps/skills/booking/skill.py", "apps.integrations.yclients"),
        ("G2.1-skills-no-yclients", "apps/skills/booking/tools.py", "apps.integrations.yclients"),
        # G5.1 — API surfaces → booking mutators (#925 create; #968 transitions/feedback)
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "apps.booking.services.create",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "apps.booking.services.transitions",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/miniapp_api/views.py",
            "apps.booking.services.feedback",
        ),
        (
            "G5.1-api-no-booking-mutators",
            "apps/admin_api/services/master_deactivation.py",
            "apps.booking.services.transitions",
        ),
        # G6.2 — eventbus consumer → skill in-process (#927; fix = Celery task)
        (
            "G6.2-eventbus-consumer-narrow",
            "apps/eventbus/consumers/payment.py",
            "apps.skills",
        ),
    }
)

# Directory/file name fragments that are out of scope — the contracts
# describe the PRODUCTION boundary (the tickets count prod sites
# separately from test sites).
_SKIP_DIR_PARTS = frozenset({"tests", "migrations", "__pycache__"})


@dataclass(frozen=True)
class Violation:
    file: Path
    lineno: int
    col_offset: int
    message: str

    def format(self) -> str:
        return f"{self.file}:{self.lineno}:{self.col_offset}: {self.message}"


@dataclass(frozen=True)
class ImportEdge:
    """A single imported module reference found in a source file."""

    module: str
    lineno: int
    col_offset: int


class _ImportCollector(ast.NodeVisitor):
    """Collect every statically-resolvable imported module in a file.

    ``package_parts`` is the dotted package the file lives in, used to
    resolve relative imports to absolute module paths.
    """

    def __init__(self, package_parts: tuple[str, ...]) -> None:
        self.package_parts = package_parts
        self.edges: list[ImportEdge] = []

    # `import a.b.c` / `import a.b.c as x`
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add(alias.name, node)
        self.generic_visit(node)

    # `from a.b import c` / `from . import c` / `from ..a import (c, d)`
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = self._resolve_base(node.module, node.level)
        if base is not None:
            # The package/module imported FROM is itself an edge…
            if node.module is not None or node.level > 0:
                self._add(base, node)
            # …and each name may itself be a submodule (`from a.b import c`
            # where a.b.c is a module). Recording both is safe: the
            # contract match is a prefix test, so a non-module name like a
            # function simply won't match any forbidden ROOT it isn't under.
            for alias in node.names:
                if alias.name != "*":
                    self._add(f"{base}.{alias.name}", node)
        self.generic_visit(node)

    # `importlib.import_module("a.b")` / `__import__("a.b")`
    def visit_Call(self, node: ast.Call) -> None:
        if self._is_dynamic_import(node.func) and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                self._add(first.value, node)
        self.generic_visit(node)

    def _resolve_base(self, module: str | None, level: int) -> str | None:
        if level == 0:
            return module
        # Relative: level 1 = current package, 2 = parent, etc.
        drop = level - 1
        if drop >= len(self.package_parts):
            return None  # climbs to/above the top package — Python would ImportError
        base_parts = self.package_parts[: len(self.package_parts) - drop]
        tail = module.split(".") if module else []
        resolved = ".".join((*base_parts, *tail))
        return resolved or None

    @staticmethod
    def _is_dynamic_import(func: ast.AST) -> bool:
        # `importlib.import_module(...)`  (Attribute) or a bare
        # `import_module(...)` / `__import__(...)` (Name).
        if isinstance(func, ast.Attribute):
            return func.attr == "import_module"
        if isinstance(func, ast.Name):
            return func.id in {"import_module", "__import__"}
        return False

    def _add(self, module: str, node: ast.AST) -> None:
        self.edges.append(
            ImportEdge(
                module=module,
                lineno=getattr(node, "lineno", 0),
                col_offset=getattr(node, "col_offset", 0),
            )
        )


def _package_parts(rel_posix: str) -> tuple[str, ...]:
    """Dotted package parts for a repo-relative POSIX file path.

    `apps/skills/booking/skill.py`     -> ('apps','skills','booking')
    `apps/skills/booking/__init__.py`  -> ('apps','skills','booking')
    """
    # The file's package is its containing directory — drop the filename.
    # (For __init__.py the filename is the package marker, so dropping it
    # yields the same package dir; no special case needed.)
    return tuple(rel_posix.split("/")[:-1])


def _matched_root(module: str, forbidden_modules: tuple[str, ...]) -> str | None:
    """Return the forbidden root `module` falls under, or None.

    Matches exact dotted module OR a submodule (`root` or `root.<sub>`),
    never a sibling sharing a prefix (`apps.skills` != `apps.skillsfoo`).
    """
    for root in forbidden_modules:
        if module == root or module.startswith(root + "."):
            return root
    return None


def _is_skipped(rel_posix: str) -> bool:
    parts = rel_posix.split("/")
    if any(p in _SKIP_DIR_PARTS for p in parts):
        return True
    name = parts[-1]
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def evaluate_file(
    file_path: Path,
    repo_root: Path,
    *,
    contracts: tuple[Contract, ...] = CONTRACTS,
    baseline: frozenset[BaselineKey] = BASELINE,
) -> tuple[list[Violation], set[BaselineKey]]:
    """Evaluate one file against the contracts.

    Returns ``(violations, satisfied_baseline_keys)`` — the second
    element lets the caller detect stale baseline entries.
    """
    try:
        rel_posix = file_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return [], set()

    if _is_skipped(rel_posix):
        return [], set()

    applicable = [c for c in contracts if any(rel_posix.startswith(p) for p in c.source_prefixes)]
    if not applicable:
        return [], set()

    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except (OSError, SyntaxError):
        # Broken syntax / unreadable is ruff's job, not ours.
        return [], set()

    collector = _ImportCollector(_package_parts(rel_posix))
    collector.visit(tree)

    violations: list[Violation] = []
    satisfied: set[BaselineKey] = set()
    # De-dupe identical (contract, root) crossings reported on the same line.
    seen: set[tuple[str, str, int]] = set()

    for edge in collector.edges:
        for contract in applicable:
            root = _matched_root(edge.module, contract.forbidden_modules)
            if root is None:
                continue
            key: BaselineKey = (contract.id, rel_posix, root)
            if key in baseline:
                satisfied.add(key)
                continue
            dedupe = (contract.id, root, edge.lineno)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            violations.append(
                Violation(
                    file=file_path,
                    lineno=edge.lineno,
                    col_offset=edge.col_offset,
                    message=(
                        f"[{contract.id}] imports {edge.module!r} — {contract.message} "
                        f"(tracked {contract.issue})"
                    ),
                )
            )
    return violations, satisfied


def scan_paths(
    paths: list[Path],
    repo_root: Path,
    *,
    contracts: tuple[Contract, ...] = CONTRACTS,
    baseline: frozenset[BaselineKey] = BASELINE,
) -> list[Violation]:
    """Scan files/directories, returning new-edge violations + stale-baseline
    violations (a baseline entry that no longer matches any crossing).

    Stale-baseline reporting is restricted to baselined files that were
    actually visited in this scan: on a partial scan (e.g. one subtree) a
    baselined file outside the scanned paths is simply unknown, NOT stale —
    otherwise a per-subtree invocation would spuriously demand deletion of
    live baseline entries and silently weaken enforcement.
    """
    violations: list[Violation] = []
    satisfied: set[BaselineKey] = set()
    scanned_rel: set[str] = set()

    for path in paths:
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for py_file in files:
            try:
                scanned_rel.add(py_file.resolve().relative_to(repo_root.resolve()).as_posix())
            except ValueError:
                pass
            v, s = evaluate_file(py_file, repo_root, contracts=contracts, baseline=baseline)
            violations.extend(v)
            satisfied |= s

    for key in sorted(baseline - satisfied):
        contract_id, rel_posix, root = key
        if rel_posix not in scanned_rel:
            continue  # file outside the scanned paths — can't judge staleness
        violations.append(
            Violation(
                file=repo_root / rel_posix,
                lineno=0,
                col_offset=0,
                message=(
                    f"[{contract_id}] STALE BASELINE — {rel_posix} no longer "
                    f"imports {root!r}. The boundary debt was fixed; delete this "
                    "line from BASELINE in tools/lint/import_boundaries.py."
                ),
            )
        )
    return violations


def _detect_repo_root(start: Path) -> Path:
    """Walk upward from `start` to the dir holding `apps/` + `pyproject.toml`."""
    current = start.resolve() if start.is_absolute() else (Path.cwd() / start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "apps").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: import_boundaries.py <path> [<path> ...]", file=sys.stderr)
        return 2

    repo_root = _detect_repo_root(Path(argv[1]))
    targets: list[Path] = []
    for arg in argv[1:]:
        target = Path(arg)
        if not target.exists():
            print(f"import_boundaries: path does not exist: {target}", file=sys.stderr)
            continue
        targets.append(target)

    violations = scan_paths(targets, repo_root)
    if not violations:
        return 0

    for v in violations:
        print(v.format())
    print(
        f"\nimport_boundaries: {len(violations)} boundary violation(s) detected. "
        "See tools/lint/import_boundaries.py for the G-series contracts.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
