"""Consent-column direct-read AST guard (DRF-1314).

Scans Python files under `apps/` for **reads** of ``consent_at`` outside
the small set of modules that are allowed to read it, and points the
author at :func:`apps.notifications.proactive.consent_blocker` instead.

# Why this guard exists

``BotUser.consent_at`` is a denormalised stamp. The record of consent is
``ConsentRecord``, and :func:`apps.consent.services.withdraw` marks the
record ``withdrawn_at`` and **deliberately leaves the column set** — a
soft delete on a live row (spec §4). The column therefore answers "did
this person ever consent?", never "may we write to them now".

Three separate surfaces derived a proactive-messaging permission from
that column, independently, within one quarter:

* DRF-1301 — the post-visit follow-up. Sent seven messages to two
  people who never consented before anyone noticed.
* DRF-1307 — the master-deactivation cascade. Caught before its first
  press.
* DRF-1314 — the proactive nutrition layer. Deployed to the pilot and
  live behind a feature flag; on 2026-08-23, of the twelve reachable
  ``BotUser`` rows, five had ``consent_at`` set and **four of those five
  had withdrawn**.

The three did not copy each other. They each read a field that looks
sufficient and is not, and its insufficiency is visible only to somebody
who has read ``withdraw()``. That is the shape a lint fixes and a code
review does not.

# Why a path allowlist rather than a smarter pattern

The three sick call sites and the two legitimate ones are *the same
expression*::

    if getattr(bot_user, "consent_at", None) is None:   # gate  — wrong
    if getattr(bot_user, "consent_at", None) is None:   # stamp — right

The first blocks a message; the second skips a redundant write in the
flow that is granting consent right now. Nothing in the syntax
distinguishes them — only which module they are in does. So the guard
bans the read and enumerates the modules that may perform it.

# What this lint detects

DENIED (raises a violation):
  - `bot_user.consent_at` in a load context — any attribute read
  - `getattr(bot_user, "consent_at", None)`
  - `.filter(consent_at__isnull=True)` / `.exclude(...)` / `.get(...)`
  - `.filter(bot_user__consent_at__isnull=False)` — related lookups
  - `Q(consent_at__isnull=True)`
  - `.values("consent_at")` / `.values_list("consent_at", flat=True)`
    / `.only("consent_at")`

ALLOWED:
  - `bot_user.consent_at = timezone.now()` — a **write**. Writes are not
    the defect; DRF-1314 explicitly does not change how consent is
    recorded, only who may read the recording.
  - `.update(consent_at=...)` / `Model(consent_at=...)` /
    `.create(consent_at=...)` — writes again, and the constructor form
    is how `MemoryEntry` rows are built (see the collision below).
  - `save(update_fields=["consent_at"])` — the string appears in a call
    to `save`, which is not a lookup method.
  - Any read inside an allowlisted path (below).

# Allowlist — and why each entry is not a sixth copy of the gate

  - `apps/notifications/proactive.py` — THE shared gate. It reads the
    column as the third of its four conditions and then reads the
    record. This is the module every other caller is redirected to.
  - `apps/consent/services.py` — where consent is written. `grant()`
    reads the column to reconcile a legacy row whose stamp is missing
    (#1074 made the stamp and the record atomic); that read is about the
    column's own state, not about permission.
  - `apps/skills/welcome/skill.py` — the other stamping site: the
    per-tenant welcome flow, whose read makes its stamp idempotent.
  - `apps/bookings/management/commands/post_visit_followup_dryrun.py` —
    an operator census. It prints "... with consent_at set: N" as one
    line of a funnel *next to* the honest gate's count, which is the one
    place where showing what the column alone says is the point.
  - `**/tests/**`, `**/test_*.py`, `**/migrations/**` — fixtures must be
    able to build the exact rows the gate rejects, and a backfill reads
    columns by definition.

# KNOWN LIMITATIONS (explicitly NOT detected)

  - **`MemoryEntry` has a `consent_at` column too**, unrelated to
    `BotUser`'s and with different semantics (per-entry consent for the
    yellow/red memory zones). This guard matches on the *name* and
    cannot tell the two apart. Today that costs nothing — every
    `MemoryEntry.consent_at` site in `apps/` is a write
    (`memory_writer.py`, `memory_inferred.py`, `personal_context.py`),
    and writes are allowed — but a future *read* of it would be a false
    positive. The fix when that day comes is one more allowlist entry
    plus a line here, not a cleverer matcher: an AST cannot resolve
    which model an attribute belongs to without type inference.
  - **Indirection**: `field = "consent_at"; getattr(u, field)`, or
    `**{"consent_at__isnull": True}`, or raw SQL. The constant never
    appears in a position this guard inspects.
  - **The column reached through a serializer or a `values()` dict**
    built in one module and read by key in another.

Same posture as `red_zone_guard.py`: this is the FIRST line of defence —
it catches the mistake three engineers actually made — and the shared
gate is the final one. Do not chase infinite patterns.

# CLI usage

  python tools/lint/consent_column_guard.py apps/

Exits 0 with no output when clean. Exits 1 with one line per violation:
`file:line:col: <message>`.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

#: The column this guard is about.
_COLUMN = "consent_at"

#: Modules allowed to read the column directly. Forward-slash form;
#: compared against the POSIX-form repo-relative path.
_ALLOWLIST_FRAGMENTS = (
    # THE shared gate — the module every other caller is sent to.
    "apps/notifications/proactive.py",
    # The two places consent is stamped; both read the column to make
    # their own write idempotent.
    "apps/consent/services.py",
    "apps/skills/welcome/skill.py",
    # An operator census that prints what the column says next to what
    # the honest gate says.
    "apps/bookings/management/commands/post_visit_followup_dryrun.py",
)

#: Directory fragments allowlisted wholesale. Fixtures must be able to
#: build the rows the gate rejects; backfills read columns by nature.
_ALLOWLIST_DIRS = ("/tests/", "/migrations/")

#: ORM methods whose keyword arguments are *lookups* — i.e. reads.
#: ``update``, ``create``, ``get_or_create`` and friends are deliberately
#: absent: their kwargs write the column.
_LOOKUP_METHODS = {"filter", "exclude", "get", "annotate", "alias"}

#: ORM methods that take the column name as a positional string and
#: hand back its value.
_PROJECTION_METHODS = {"values", "values_list", "only", "defer"}

#: The advice every violation carries.
_ADVICE = (
    f"direct read of {_COLUMN!r} — the column is a denormalised stamp that "
    "apps.consent.services.withdraw() never clears, so it says 'ever consented', "
    "not 'may we write to them'. Use "
    "apps.notifications.proactive.consent_blocker(bot_user) (DRF-1314)."
)


@dataclass(frozen=True)
class Violation:
    file: Path
    lineno: int
    col_offset: int
    message: str

    def format(self) -> str:
        return f"{self.file}:{self.lineno}:{self.col_offset}: {self.message}"


def _is_column_lookup(kwarg: str | None) -> bool:
    """True for ``consent_at``, ``consent_at__isnull``, ``bot_user__consent_at``…

    Split on ``__`` and look for the column as a whole segment, so
    ``food_scanner_consent_at`` — a different field, with no
    ``ConsentRecord`` behind it and therefore nothing to reconcile it
    against — does not match.
    """
    if not kwarg:
        return False
    return _COLUMN in kwarg.split("__")


def _is_column_constant(node: ast.AST) -> bool:
    """True iff ``node`` is the string constant naming the column."""
    return isinstance(node, ast.Constant) and node.value == _COLUMN


class _ConsentColumnVisitor(ast.NodeVisitor):
    """Records every read of the column."""

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
        # `x.consent_at` read. Store (`x.consent_at = ...`) and Del are
        # writes and are left alone — see the module docstring.
        if node.attr == _COLUMN and isinstance(node.ctx, ast.Load):
            self._flag(node, f"attribute read `.{_COLUMN}`")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = (
            func.attr
            if isinstance(func, ast.Attribute)
            else (func.id if isinstance(func, ast.Name) else "")
        )

        # `getattr(obj, "consent_at", default)`
        if name == "getattr" and len(node.args) >= 2 and _is_column_constant(node.args[1]):
            self._flag(node, f'getattr(..., "{_COLUMN}")')

        # `.filter(consent_at__isnull=...)`, `Q(bot_user__consent_at=...)`
        if name in _LOOKUP_METHODS or name == "Q":
            for kw in node.keywords:
                if _is_column_lookup(kw.arg):
                    self._flag(node, f"ORM lookup `{kw.arg}=`")

        # `.values_list("consent_at", flat=True)`, `.only("consent_at")`
        if name in _PROJECTION_METHODS:
            for arg in node.args:
                if _is_column_constant(arg):
                    self._flag(node, f'{name}("{_COLUMN}")')

        self.generic_visit(node)


def _is_allowlisted(file_path: Path, repo_root: Path) -> bool:
    """True if ``file_path`` may read the column directly."""
    try:
        rel = file_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        # Outside repo_root — treat as not allowlisted.
        return False
    rel_posix = rel.as_posix()
    if any(rel_posix.startswith(frag) for frag in _ALLOWLIST_FRAGMENTS):
        return True
    if any(frag in f"/{rel_posix}" for frag in _ALLOWLIST_DIRS):
        return True
    return file_path.name.startswith("test_")


def scan_file(file_path: Path, repo_root: Path | None = None) -> list[Violation]:
    """Scan one .py file. Empty list when allowlisted OR clean."""
    if repo_root is None:
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
        # Broken syntax is ruff's problem, not this guard's.
        return []

    visitor = _ConsentColumnVisitor(file_path=file_path)
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
    """Walk upward from ``start`` looking for the repo markers."""
    current = start.resolve() if start.is_absolute() else (Path.cwd() / start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "apps").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: consent_column_guard.py <path> [<path> ...]", file=sys.stderr)
        return 2

    repo_root = _detect_repo_root(Path(argv[1]))
    all_violations: list[Violation] = []
    for arg in argv[1:]:
        target = Path(arg)
        if not target.exists():
            print(f"consent_column_guard: path does not exist: {target}", file=sys.stderr)
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
        f"\nconsent_column_guard: {len(all_violations)} violation(s) detected. "
        "See `apps/notifications/proactive.py` for the gate to call instead.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
