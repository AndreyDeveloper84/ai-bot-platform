"""Dead-column guard for ``BotUser.food_scanner_consent_at`` (DRF-1963, M1).

Scans Python files under `apps/` for ANY reference to the column — read or
write — outside migrations, and points the author at
:mod:`apps.consent.nutrition` instead.

# Why this guard exists

Owner ruling 15.09 (`docs/PROMPT_ORCHESTRATOR_AYLA_CONTROLLED_PILOT_NEXT_WAVE.md`
§6 M1): the food diary / scanner consent is a row of the one consent
registry, scope ``food_diary_processing`` — with a document version, a source
and a withdrawal that leaves the grant on record. The column was a second
consent backend: no version, no source, an audit row beside ``ConsentRecord``
rather than in it, and a withdrawal that wiped the grant.

DRF-1963 moved every reader (the scanner gate, the proactive diary layer, the
Mini App screen, ``/me``) and every writer onto the registry. The column stays
in the schema for one release — old processes still read it during a rollout —
and is removed by the second half of the leaf. Until then the only thing that
keeps it dead is that nobody touches it, and "nobody touches it" is a claim a
lint can hold and a code review cannot.

# How this differs from `consent_column_guard.py`

That guard is about ``BotUser.consent_at``, a *live* stamp: writes are allowed,
reads are allowed in named modules, fixtures may read it. Here the column is
dead, so the rule is flat: no read and no write, anywhere in `apps/`, tests
included — a fixture that still stamps the column builds a person the gate no
longer believes, and the test would pass for the wrong reason.

# What this lint detects

  - `x.food_scanner_consent_at` in any context — load, store, del
  - `getattr(x, "food_scanner_consent_at", ...)` / `setattr(...)` / `hasattr(...)`
  - any keyword argument naming the column as a whole `__` segment —
    `.filter(food_scanner_consent_at=...)`, `.update(...)`, `.create(...)`,
    `BotUser(...)`, `Q(bot_user__food_scanner_consent_at__isnull=True)`
  - the column name as a string constant anywhere — `update_fields=[...]`,
    `.values("...")`, dict keys

# Allowlist

  - `**/migrations/**` — the column's own history and the transfer to the
    registry (`apps/consent/migrations/0005_food_diary_processing.py`).
  - `apps/consent/tests/test_food_diary_consent_migration.py` — the test of
    that transfer has to build a row with the column set.
  - `apps/consent/tests/test_food_scanner_column_guard.py` — this guard's own
    tests have to spell every form the guard catches.
  - `apps/identity/services/profile.py` — spells the legacy `/me` response KEY
    (`LEGACY_ME_CONSENT_KEY`), whose name happens to equal the column's. Its
    value comes from the registry; the key goes with the second half. Every
    other module imports the constant instead of spelling the name.

# KNOWN LIMITATIONS (explicitly NOT detected)

  - Indirection that never spells the name: `"food_scanner" + "_consent_at"`,
    `**{name: ...}` with a computed `name`, raw SQL.
  - Non-Python readers. The Mini App reads the registry through
    `me/food-scanner-consent/`; `/me` still carries the key, computed from the
    registry, until the second half removes it.

# CLI usage

  python tools/lint/food_scanner_column_guard.py apps/

Exits 0 with no output when clean. Exits 1 with one line per violation:
`file:line:col: <message>`.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

import lint_parse  # DRF-2538: нечитаемый вход — отдельный исход, не ноль

#: The column this guard keeps dead.
_COLUMN = "food_scanner_consent_at"

#: Files allowlisted by name (repo-relative, forward-slash form).
_ALLOWLIST_FILES = (
    "apps/consent/tests/test_food_diary_consent_migration.py",
    "apps/consent/tests/test_food_scanner_column_guard.py",
    "apps/identity/services/profile.py",
)

#: Directory fragments allowlisted wholesale.
_ALLOWLIST_DIRS = ("/migrations/",)

_ADVICE = (
    f"reference to {_COLUMN!r} — the column is no longer a consent record "
    "(DRF-1963, M1). Ask apps.consent.nutrition: diary_is_granted / "
    "diary_current_record to read, grant_diary / withdraw_diary to write."
)


@dataclass(frozen=True)
class Violation:
    file: Path
    lineno: int
    col_offset: int
    message: str

    def format(self) -> str:
        return f"{self.file}:{self.lineno}:{self.col_offset}: {self.message}"


def _names_column(kwarg: str | None) -> bool:
    """True for ``food_scanner_consent_at``, ``…__isnull``, ``bot_user__…``."""
    return bool(kwarg) and _COLUMN in kwarg.split("__")  # type: ignore[union-attr]


class _ColumnVisitor(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.violations: list[Violation] = []

    def _flag(self, node: ast.AST, detail: str) -> None:
        self.violations.append(
            Violation(
                file=self.file_path,
                lineno=getattr(node, "lineno", 0),
                col_offset=getattr(node, "col_offset", 0),
                message=f"{detail}: {_ADVICE}",
            )
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr == _COLUMN:
            self._flag(node, f"attribute `.{_COLUMN}`")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        for kw in node.keywords:
            if _names_column(kw.arg):
                self._flag(node, f"keyword `{kw.arg}=`")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _names_column(node.value):
            self._flag(node, f'string "{node.value}"')
        self.generic_visit(node)


def _is_allowlisted(file_path: Path, repo_root: Path) -> bool:
    try:
        rel = file_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    rel_posix = rel.as_posix()
    if rel_posix in _ALLOWLIST_FILES:
        return True
    return any(frag in f"/{rel_posix}" for frag in _ALLOWLIST_DIRS)


def scan_file(file_path: Path, repo_root: Path | None = None) -> list[Violation]:
    """Scan one .py file. Empty list when allowlisted OR clean."""
    if repo_root is None:
        repo_root = _detect_repo_root(file_path)
    if _is_allowlisted(file_path, repo_root):
        return []
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        lint_parse.unreadable(file_path, exc)
        return []
    tree = lint_parse.parse_or_report(source, file_path)
    if tree is None:
        return []
    visitor = _ColumnVisitor(file_path=file_path)
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
    current = start.resolve() if start.is_absolute() else (Path.cwd() / start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "apps").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def _run(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: food_scanner_column_guard.py <path> [<path> ...]", file=sys.stderr)
        return 2
    repo_root = _detect_repo_root(Path(argv[1]))
    all_violations: list[Violation] = []
    for arg in argv[1:]:
        target = Path(arg)
        if not target.exists():
            print(f"food_scanner_column_guard: path does not exist: {target}", file=sys.stderr)
            return 2
        if target.is_file():
            all_violations.extend(scan_file(target, repo_root=repo_root))
        else:
            all_violations.extend(scan_directory(target, repo_root=repo_root))
    if not all_violations:
        return 0
    for v in all_violations:
        print(v.format())
    print(
        f"\nfood_scanner_column_guard: {len(all_violations)} violation(s) detected. "
        "See `apps/consent/nutrition.py` for the registry API to use instead.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str]) -> int:
    lint_parse.reset()
    code = _run(argv)
    if code not in (0, 1):  # ошибка вызова — охват не о чем печатать
        return code
    return max(code, lint_parse.finish("food_scanner_column_guard"))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
