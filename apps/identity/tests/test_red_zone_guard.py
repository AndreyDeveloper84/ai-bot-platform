"""AST lint tests for tools/lint/red_zone_guard.py (#230 + spec §13 row 14.25).

# What this file tests

| Class | What |
|---|---|
| TestBlocksExplicitRedPatterns | All denied patterns from the script docstring fire violations |
| TestAllowsLegitPatterns | Runtime-variable / non-zone / docstring-literal patterns DON'T fire |
| TestAllowlistPaths | Sanctioned modules (red_zone_reader, memory_writer, tests, migrations) DON'T fire |
| TestRealAppsAreClean | Integration: scanning the actual `apps/` directory yields zero violations |
| TestKnownLimitations | Honest documentation: AST lint does NOT catch indirect patterns; RLS is the final defence |

# Why these tests exist

Per tech-lead direction 2026-05-23: AST lint = first line of defence,
RLS = final defence. We deliberately test BOTH what's caught AND what
isn't, so future readers know the contract.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

# Project root for the lint tests — the `apps/` and `pyproject.toml`
# both live here, which is what `_detect_repo_root` looks for.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Import the lint module via path injection because `tools/` is not a
# package (no `__init__.py`). The script is intended as CLI but we
# also want to exercise its functions in tests.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import red_zone_guard  # noqa: E402


def _write_py(tmp_path: Path, name: str, source: str) -> Path:
    """Write `source` to `tmp_path/name` and return the path."""
    p = tmp_path / name
    p.write_text(dedent(source), encoding="utf-8")
    return p


# ───────────────────────────────────────────────────────────────────────
# DENIED — all patterns from the script docstring must fire
# ───────────────────────────────────────────────────────────────────────


class TestBlocksExplicitRedPatterns:
    def test_filter_with_red_string(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone='red')
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert len(violations) == 1
        assert "sensitivity_zone" in violations[0].message
        assert "red" in violations[0].message

    def test_filter_with_in_list_red(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone__in=['red'])
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert len(violations) == 1

    def test_filter_with_in_list_red_mixed(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone__in=['yellow', 'red'])
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert len(violations) == 1

    def test_get_with_red(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.get(sensitivity_zone='red')
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert len(violations) == 1

    def test_exclude_with_red(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.exclude(sensitivity_zone='red')
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert len(violations) == 1

    def test_q_object_with_red(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.filter(Q(sensitivity_zone='red'))
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        # Both the outer .filter() call AND the Q() call have keywords;
        # only Q() has the sensitivity_zone='red' kwarg, so exactly 1.
        assert len(violations) == 1

    def test_q_object_in_list_red(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.filter(Q(sensitivity_zone__in=['red', 'yellow']))
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert len(violations) == 1

    def test_chained_filter_get_red(self, tmp_path) -> None:
        """Chained calls — both Call nodes are visited."""
        f = _write_py(
            tmp_path,
            "bad.py",
            """
            MemoryEntry.objects.filter(user_id=x).get(sensitivity_zone='red')
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        # Only the .get(sensitivity_zone='red') call fires — the
        # .filter(user_id=x) doesn't have a sensitivity_zone kwarg.
        assert len(violations) == 1


# ───────────────────────────────────────────────────────────────────────
# ALLOWED — legit patterns must NOT fire
# ───────────────────────────────────────────────────────────────────────


class TestAllowsLegitPatterns:
    def test_runtime_variable_does_not_fire(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "ok.py",
            """
            zone = get_user_choice()
            MemoryEntry.objects.filter(sensitivity_zone=zone)
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_non_zone_filter_does_not_fire(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "ok.py",
            """
            MemoryEntry.objects.filter(user_id=x, kind='preference')
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_docstring_red_does_not_fire(self, tmp_path) -> None:
        """String 'red' in a docstring is NOT a Call kwarg → not flagged."""
        f = _write_py(
            tmp_path,
            "ok.py",
            """
            def my_func():
                '''This function handles 'red' zone — uses RedZoneReader.'''
                return None
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_comment_red_does_not_fire(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "ok.py",
            """
            # Note: 'red' rows are sensitive — use the accessor.
            x = 1
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_green_filter_does_not_fire(self, tmp_path) -> None:
        f = _write_py(
            tmp_path,
            "ok.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone='green')
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []


# ───────────────────────────────────────────────────────────────────────
# Allowlist — sanctioned modules must NOT fire even with red literal
# ───────────────────────────────────────────────────────────────────────


class TestAllowlistPaths:
    """Verify the allowlist excludes red_zone_reader / memory_writer / tests / migrations."""

    def _make_repo_tree(self, root: Path, rel_path: str, source: str) -> Path:
        """Create a fake repo tree with apps/ + pyproject.toml so
        `_detect_repo_root` resolves correctly, then place a file at
        `rel_path` with the given source.
        """
        (root / "apps").mkdir(exist_ok=True)
        (root / "pyproject.toml").write_text("# fake")
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(dedent(source), encoding="utf-8")
        return full

    def test_red_zone_reader_path_allowlisted(self, tmp_path) -> None:
        f = self._make_repo_tree(
            tmp_path,
            "apps/identity/services/red_zone_reader.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone='red')
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_memory_writer_path_allowlisted(self, tmp_path) -> None:
        f = self._make_repo_tree(
            tmp_path,
            "apps/identity/services/memory_writer.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone='red')
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_tests_path_allowlisted(self, tmp_path) -> None:
        f = self._make_repo_tree(
            tmp_path,
            "apps/identity/tests/test_anything.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone='red')
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_migrations_path_allowlisted(self, tmp_path) -> None:
        f = self._make_repo_tree(
            tmp_path,
            "apps/identity/migrations/9999_backfill.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone='red')
            """,
        )
        assert red_zone_guard.scan_file(f, repo_root=tmp_path) == []

    def test_random_app_path_NOT_allowlisted(self, tmp_path) -> None:
        f = self._make_repo_tree(
            tmp_path,
            "apps/skills/some_skill.py",
            """
            MemoryEntry.objects.filter(sensitivity_zone='red')
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert len(violations) == 1


# ───────────────────────────────────────────────────────────────────────
# Integration — real apps/ must be clean
# ───────────────────────────────────────────────────────────────────────


class TestRealAppsAreClean:
    """Test 14.25 — current production code has zero red-zone violations."""

    def test_apps_directory_is_clean(self) -> None:
        """Scan the real `apps/` tree from the current commit."""
        apps_dir = _PROJECT_ROOT / "apps"
        if not apps_dir.is_dir():
            pytest.skip(f"apps/ not found at {apps_dir}")
        violations = red_zone_guard.scan_directory(apps_dir, repo_root=_PROJECT_ROOT)
        if violations:
            formatted = "\n  ".join(v.format() for v in violations)
            pytest.fail(
                f"Production apps/ has {len(violations)} red-zone "
                f"violation(s):\n  {formatted}\n\n"
                "Either:\n"
                "  - Refactor caller to use "
                "apps.identity.services.red_zone_reader.RedZoneReader.read()\n"
                "  - OR add the calling module to _ALLOWLIST_FRAGMENTS in "
                "tools/lint/red_zone_guard.py (only for sanctioned paths)."
            )


# ───────────────────────────────────────────────────────────────────────
# KNOWN LIMITATIONS — honest documentation
# ───────────────────────────────────────────────────────────────────────


class TestKnownLimitations:
    """Documents what AST lint does NOT catch.

    Per tech-lead direction 2026-05-23 — don't chase infinite obtuse
    patterns. The PostgreSQL RLS policy (`memory_entry_non_red_visible`,
    veha 2) catches everything except DB-superuser.

    These tests EXPECT zero violations from AST lint — to make it
    crystal-clear in the test suite that this is intentional gap, not
    overlooked.
    """

    def test_exclude_non_red_zones_NOT_detected(self, tmp_path) -> None:
        """`.exclude(sensitivity_zone__in=['green','yellow'])` ≈ 'red only'.

        AST lint does NOT detect this — the literal 'red' never appears.
        RLS policy is the safety net.
        """
        f = _write_py(
            tmp_path,
            "edge.py",
            """
            MemoryEntry.objects.exclude(sensitivity_zone__in=['green', 'yellow'])
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert violations == [], (
            "AST lint deliberately does NOT detect exclusion patterns — "
            "RLS catches this at runtime."
        )

    def test_variable_resolved_red_NOT_detected(self, tmp_path) -> None:
        """`zone = 'red'; .filter(sensitivity_zone=zone)` not detected.

        AST lint only inspects the immediate kwarg AST — not data flow.
        """
        f = _write_py(
            tmp_path,
            "edge.py",
            """
            zone = 'red'
            MemoryEntry.objects.filter(sensitivity_zone=zone)
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert violations == []

    def test_raw_sql_red_NOT_detected(self, tmp_path) -> None:
        """`.raw("... WHERE sensitivity_zone='red'")` not detected.

        AST lint inspects ORM keyword args, not SQL string literals.
        """
        f = _write_py(
            tmp_path,
            "edge.py",
            """
            MemoryEntry.objects.raw("SELECT * FROM identity_memoryentry WHERE sensitivity_zone='red'")
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert violations == []

    def test_kwargs_unpacking_NOT_detected(self, tmp_path) -> None:
        """`**{'sensitivity_zone': 'red'}` not detected.

        AST lint inspects literal keyword arguments — not dynamic unpack.
        """
        f = _write_py(
            tmp_path,
            "edge.py",
            """
            filters = {'sensitivity_zone': 'red'}
            MemoryEntry.objects.filter(**filters)
            """,
        )
        violations = red_zone_guard.scan_file(f, repo_root=tmp_path)
        assert violations == []
