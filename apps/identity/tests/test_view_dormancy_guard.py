"""Dormancy guard for `memory_entry_safe` view (#675 PRE_PILOT).

Per Sprint 1 Track A Round-5 adversarial finding F5 (filed as #675):
the `memory_entry_safe` view created in migration 0008 currently does
NOT filter `soft_deleted_at IS NOT NULL` rows. If any production code
path wires the view to a live dashboard / ops query / API surface
BEFORE #675 lands the WHERE-clause fix, the view becomes a 152-ФЗ
Chapter 3 violation surface (ops staff see rows the user asked to
forget).

# Independent reviewer recommendation 2026-05-25

> «memory_entry_safe view сейчас dormant (никто не SELECT'ит) →
> exploit impossible. BUT — если кто-либо подключит view к live
> dashboard до fix → мгновенно PRE_MERGE (cross-tenant leak). Add
> guard-test в CI.»

# What this test enforces

The view name MUST NOT appear in any `apps/` Python file outside this
allowlist:

1. The migration that CREATEs the view (`0008_red_zone_db_security.py`)
   — necessary for the view to exist.
2. Other identity migrations (`0009+` may need to reference the view
   in further DDL like the future `0010_*.py` that adds the WHERE
   clause).
3. Identity tests directory (`apps/identity/tests/`) — tests verify
   the view exists / has correct definition. Tests don't expose data
   to end users.

Anything else — admin code, views, services, skills, mini-app
serializers, signal handlers — is REJECTED until #675 fix lands and
this guard is relaxed / removed.

# Lifetime

This test should be **REMOVED** when #675 lands (the view is patched
to filter `soft_deleted_at IS NULL` and is safe to use). Until then,
it guards the architectural invariant that the view stays dormant.

# Comparison to red_zone_guard.py

`tools/lint/red_zone_guard.py` is AST-based and ships as a CI step.
This guard is a pytest test — simpler (text grep), narrower scope
(one symbol), and self-removing on #675 close. The two complement
each other: red_zone_guard catches red-row queries; this catches
the specific dormant-view symbol.
"""

from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_APPS_ROOT = _PROJECT_ROOT / "apps"

# Allowlist — files / directories that MAY reference `memory_entry_safe`
# without triggering this guard. Paths are repo-relative, POSIX-form,
# matched as «startswith» prefixes.
_ALLOWLIST_PREFIXES = (
    "apps/identity/migrations/",  # view CREATE / future ALTER + WHERE fix
    "apps/identity/tests/",  # test_db_security.py + test_backup_restore.py
)

_SYMBOL = "memory_entry_safe"


def _scan() -> list[Path]:
    """Return every `.py` file under `apps/` that references the view
    AND is NOT in the allowlist.
    """
    offenders: list[Path] = []
    for py_file in _APPS_ROOT.rglob("*.py"):
        try:
            rel = py_file.relative_to(_PROJECT_ROOT).as_posix()
        except ValueError:
            continue
        if any(rel.startswith(prefix) for prefix in _ALLOWLIST_PREFIXES):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        if _SYMBOL in content:
            offenders.append(py_file)
    return offenders


def test_memory_entry_safe_view_remains_dormant_until_675_fixed() -> None:
    """#675 invariant — view must not be wired to production code paths.

    Per ADR-0011 §11.1: entries with `delete_requested_at NOT NULL`
    MUST stay unreadable. The current view definition lacks the
    `AND soft_deleted_at IS NULL` clause; until #675 patches it,
    NO production code may SELECT from this view.

    Test scope: `apps/` Python files. `tools/`, `config/`, `tests/`
    (cross-cutting) are NOT scanned — production runtime code lives
    in `apps/`. Migrations + identity tests are allowlisted because
    they create / verify the view itself, not expose data to users.

    Remove this test (and #675 close-out) together.
    """
    offenders = _scan()
    if offenders:
        formatted = "\n  ".join(str(o.relative_to(_PROJECT_ROOT)) for o in offenders)
        raise AssertionError(
            f"`memory_entry_safe` view referenced in production code "
            f"BEFORE #675 lands the `soft_deleted_at IS NULL` filter:\n"
            f"  {formatted}\n\n"
            f"Either:\n"
            f"  - revert the offending reference until #675 fix lands, OR\n"
            f"  - close #675 first (patch the view) THEN remove this guard test.\n\n"
            f"ADR-0011 §11.1: tombstoned rows MUST stay unreadable; the "
            f"current view definition does not filter them. Wiring it to "
            f"any callable surface (admin / dashboard / API) leaks 152-ФЗ "
            f"forgotten data."
        )
