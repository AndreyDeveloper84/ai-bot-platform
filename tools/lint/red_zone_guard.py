"""Red-zone direct-access AST guard (spec §13 row 14.25).

Scans Python files under `apps/` for direct ORM queries that read
red-zone MemoryEntry rows outside the sanctioned modules. Blocks
common-mistake patterns; serves as the FIRST line of defence.

# Defence-in-depth layers (recap)

1. **AST lint (this script)** — FIRST line: catches explicit
   `sensitivity_zone='red'` literals at static-analysis time.
2. **PostgreSQL RLS policy** (veha 2) — FINAL line: hides red rows
   for ANY caller (including DB superuser-equivalent roles via
   `FORCE ROW LEVEL SECURITY`) unless `ayla.red_zone_access_context`
   GUC is set to a valid UUID by the sanctioned accessor.

# What this lint detects

DENIED (raises violation):
  - `MemoryEntry.objects.filter(sensitivity_zone='red')`
  - `MemoryEntry.objects.filter(sensitivity_zone__in=['red'])`
  - `MemoryEntry.objects.filter(sensitivity_zone__in=['yellow', 'red'])`
  - `MemoryEntry.objects.get(sensitivity_zone='red')`
  - `MemoryEntry.objects.exclude(sensitivity_zone='red')` (rare but symmetric)
  - `Q(sensitivity_zone='red')`
  - `Q(sensitivity_zone__in=[..., 'red', ...])`

ALLOWED:
  - `MemoryEntry.objects.filter(sensitivity_zone=user_choice)` — runtime variable
  - `MemoryEntry.objects.filter(user_id=x)` — non-zone query
  - String literal `'red'` inside docstring or comment

# Allowlist

These paths are permitted to query red rows (they're the audited path):
  - `apps/identity/services/red_zone_reader.py` (THE sanctioned reader)
  - `apps/identity/services/memory_writer.py` (writer's read-before-update flows)
  - `apps/identity/tests/**` (fixtures)
  - `apps/identity/migrations/**` (backfills)

# KNOWN LIMITATIONS (explicitly NOT detected)

This AST lint is intentionally narrow — it ONLY detects the literal
`'red'` constant appearing in `sensitivity_zone=` or
`sensitivity_zone__in=` keyword arguments to ORM lookup methods. It
does NOT detect:

  - **Indirect filters via exclusion**:
    `.exclude(sensitivity_zone__in=['green', 'yellow'])` — equivalent
    to «red only» but the literal `'red'` never appears.
  - **Variable-resolved zone values**:
    `zone = config.get('zone'); .filter(sensitivity_zone=zone)` — the
    constant lives in config not source.
  - **Raw SQL**:
    `.raw("SELECT * FROM identity_memoryentry WHERE sensitivity_zone='red'")`.
  - **String-built kwargs**:
    `**{'sensitivity_zone': 'red'}` — kwarg synthesised at runtime.

These patterns are caught by the FINAL line of defence — the RLS
policy (`memory_entry_non_red_visible`) hides red rows from the
unprivileged `ayla_app` role unless the GUC is set. AST lint catches
common mistakes; RLS catches everything else (except DB superuser).

# Per tech-lead direction 2026-05-23

> Do not chase infinite obtuse patterns. AST lint = FIRST line of
> defence (common mistakes), RLS = FINAL line of defence.

We file the documented honest gap as a regression test in
`apps/identity/tests/test_red_zone_guard.py::TestKnownLimitations`
to make explicit what AST lint does NOT cover.

# CLI usage

  python tools/lint/red_zone_guard.py apps/

Exits 0 with no output when clean. Exits 1 with one line per
violation: `file:line:col: <message>`.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# Path fragments (forward-slash form — we normalise via PurePath) that
# are allowed to query red rows directly. The accessor + writer ARE
# the audited path; tests + migrations need fixture access.
_ALLOWLIST_FRAGMENTS = (
    "apps/identity/services/red_zone_reader.py",
    "apps/identity/services/memory_writer.py",
    "apps/identity/tests/",
    "apps/identity/migrations/",
)

# ORM lookup-style methods that take filter kwargs.
_ORM_LOOKUP_METHODS = {
    "filter",
    "get",
    "exclude",
    "get_or_create",
    "update_or_create",
    "annotate",
}

# Keyword arguments that match the sensitivity_zone column.
_SENSITIVITY_KWARGS = {
    "sensitivity_zone",
    "sensitivity_zone__in",
    "sensitivity_zone__exact",
    "sensitivity_zone__iexact",
}


@dataclass(frozen=True)
class Violation:
    file: Path
    lineno: int
    col_offset: int
    message: str

    def format(self) -> str:
        return f"{self.file}:{self.lineno}:{self.col_offset}: {self.message}"


def _is_red_constant(node: ast.AST) -> bool:
    """Return True iff `node` is the string constant 'red'."""
    return isinstance(node, ast.Constant) and node.value == "red"


def _list_or_tuple_contains_red(node: ast.AST) -> bool:
    """Return True iff `node` is a list/tuple literal containing 'red'."""
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return False
    return any(_is_red_constant(elt) for elt in node.elts)


class _RedZoneVisitor(ast.NodeVisitor):
    """AST visitor that records every `sensitivity_zone={red literal}` kwarg.

    Scope:
      - Call nodes whose `.func` ends in an ORM lookup method
        (filter/get/exclude/get_or_create/update_or_create/annotate)
      - Call nodes whose `.func.id == 'Q'` (Django Q objects)
    """

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.violations: list[Violation] = []

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_orm_lookup_call(node.func) or self._is_q_call(node.func):
            for kw in node.keywords:
                if kw.arg not in _SENSITIVITY_KWARGS:
                    continue
                if _is_red_constant(kw.value) or _list_or_tuple_contains_red(kw.value):
                    self.violations.append(
                        Violation(
                            file=self.file_path,
                            lineno=node.lineno,
                            col_offset=node.col_offset,
                            message=(
                                f"direct red-zone query via {kw.arg!r}="
                                f"'red' — use apps.identity.services."
                                "red_zone_reader.RedZoneReader.read() "
                                "instead (spec §10)."
                            ),
                        )
                    )
        self.generic_visit(node)

    @staticmethod
    def _is_orm_lookup_call(func: ast.AST) -> bool:
        # Match `.filter(...)`, `.get(...)`, etc.
        return isinstance(func, ast.Attribute) and func.attr in _ORM_LOOKUP_METHODS

    @staticmethod
    def _is_q_call(func: ast.AST) -> bool:
        # Match `Q(...)` — Django Q expression. We don't follow
        # imports; any local `Q(` is treated as a Q-object expression.
        return isinstance(func, ast.Name) and func.id == "Q"


def _is_allowlisted(file_path: Path, repo_root: Path) -> bool:
    """Return True if `file_path` lives in an allowlisted directory.

    Compares the POSIX-form relative path against
    `_ALLOWLIST_FRAGMENTS` — works uniformly on Windows + POSIX.
    """
    try:
        rel = file_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        # file_path outside repo_root — treat as not allowlisted.
        return False
    rel_posix = rel.as_posix()
    return any(rel_posix.startswith(frag) for frag in _ALLOWLIST_FRAGMENTS)


def scan_file(file_path: Path, repo_root: Path | None = None) -> list[Violation]:
    """Scan a single .py file for red-zone-direct-access violations.

    Returns an empty list when the file is allowlisted OR clean.

    Args:
        file_path: absolute or repo-relative path to a .py file.
        repo_root: root for the allowlist comparison. Defaults to
            file_path's first ancestor named 'apps' / 'tools' parent.
    """
    if repo_root is None:
        # Heuristic for direct CLI use: walk up until we find a directory
        # that contains `apps/` and `tools/` (or fall back to cwd).
        repo_root = _detect_repo_root(file_path)

    if _is_allowlisted(file_path, repo_root):
        return []

    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError:
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        # Don't block the lint on broken syntax — that's ruff's job.
        return []

    visitor = _RedZoneVisitor(file_path=file_path)
    visitor.visit(tree)
    return visitor.violations


def scan_directory(root: Path, repo_root: Path | None = None) -> list[Violation]:
    """Scan a directory recursively. Returns all violations."""
    if repo_root is None:
        repo_root = _detect_repo_root(root)
    violations: list[Violation] = []
    for py_file in root.rglob("*.py"):
        violations.extend(scan_file(py_file, repo_root=repo_root))
    return violations


def _detect_repo_root(start: Path) -> Path:
    """Walk upward from `start` looking for a directory with `apps/`.

    Falls back to `start.parent` if no marker is found — keeps the
    CLI usable from arbitrary cwd in tests.
    """
    current = start.resolve() if start.is_absolute() else (Path.cwd() / start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "apps").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: red_zone_guard.py <path> [<path> ...]", file=sys.stderr)
        return 2

    repo_root = _detect_repo_root(Path(argv[1]))
    all_violations: list[Violation] = []
    for arg in argv[1:]:
        target = Path(arg)
        if not target.exists():
            print(f"red_zone_guard: path does not exist: {target}", file=sys.stderr)
            continue
        if target.is_file():
            all_violations.extend(scan_file(target, repo_root=repo_root))
        else:
            all_violations.extend(scan_directory(target, repo_root=repo_root))

    if not all_violations:
        return 0

    for v in all_violations:
        print(v.format())
    print(
        f"\nred_zone_guard: {len(all_violations)} violation(s) detected. "
        "See `apps/identity/services/red_zone_reader.py` for the sanctioned path.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
