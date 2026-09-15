"""AST lint tests for tools/lint/food_scanner_column_guard.py (DRF-1963, M1).

The column ``BotUser.food_scanner_consent_at`` stays in the schema for one
release and must have no readers and no writers. The guard is what holds that.
Both halves are pinned: what it flags, and that the real tree has nothing to flag
— the second only after the first proves the scanner can see.
"""

from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import food_scanner_column_guard as guard  # type: ignore[import-not-found]  # noqa: E402


def _scan(tmp_path: Path, source: str, name: str = "apps/surface.py"):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent(source), encoding="utf-8")
    (tmp_path / "pyproject.toml").touch()
    return guard.scan_file(p, repo_root=tmp_path)


@pytest.mark.parametrize(
    "source",
    [
        "x = bot_user.food_scanner_consent_at\n",
        "bot_user.food_scanner_consent_at = now\n",
        'x = getattr(bot_user, "food_scanner_consent_at", None)\n',
        "BotUser.objects.filter(food_scanner_consent_at__isnull=False)\n",
        "BotUser.all_tenants.filter(pk=1).update(food_scanner_consent_at=None)\n",
        "BotUser.all_tenants.create(tenant=t, food_scanner_consent_at=now)\n",
        "Q(bot_user__food_scanner_consent_at__isnull=True)\n",
        'bot_user.save(update_fields=["food_scanner_consent_at"])\n',
        'rows = BotUser.objects.values("food_scanner_consent_at")\n',
    ],
    ids=["read", "write", "getattr", "lookup", "update", "create", "q", "update_fields", "values"],
)
def test_every_spelling_is_flagged(tmp_path, source) -> None:
    """Dead means dead: reads AND writes, attribute, keyword and string forms."""
    violations = _scan(tmp_path, source)
    assert len(violations) == 1
    assert "food_diary_processing" not in violations[0].message
    assert "apps.consent.nutrition" in violations[0].message


def test_tests_are_not_exempt(tmp_path) -> None:
    """A fixture stamping the column builds a person the gate no longer believes."""
    violations = _scan(
        tmp_path,
        "bot_user.food_scanner_consent_at = now\n",
        name="apps/skills/food_scanner/tests/test_skill.py",
    )
    assert len(violations) == 1


def test_neighbouring_column_is_not_this_one(tmp_path) -> None:
    """``consent_at`` is the other guard's column; the segment match keeps them apart."""
    assert (
        len(_scan(tmp_path, "x = bot_user.food_scanner_consent_at\ny = bot_user.consent_at\n")) == 1
    )


def test_migrations_and_the_transfer_test_may_reference_it(tmp_path) -> None:
    source = "BotUser.objects.filter(food_scanner_consent_at__isnull=False)\n"
    flagged = _scan(tmp_path, source, name="apps/surface.py")
    assert len(flagged) == 1  # the same source IS flagged elsewhere
    allowed_paths = (
        "apps/consent/migrations/0005_food_diary_processing.py",
        "apps/consent/tests/test_food_diary_consent_migration.py",
        "apps/identity/services/profile.py",
    )
    allowed = {path: _scan(tmp_path, source, name=path) for path in allowed_paths}
    assert sorted(allowed) == sorted(allowed_paths)  # presence: every allowlisted path was scanned
    assert {path: len(found) for path, found in allowed.items()} == dict.fromkeys(allowed_paths, 0)
    assert guard._ALLOWLIST_FILES == (
        "apps/consent/tests/test_food_diary_consent_migration.py",
        "apps/consent/tests/test_food_scanner_column_guard.py",
        "apps/identity/services/profile.py",
    )


def test_real_apps_tree_has_no_reference_outside_the_allowlist() -> None:
    """Integration. Presence first: the scanner walks real files and sees a planted read."""
    apps_dir = _PROJECT_ROOT / "apps"
    scanned = list(apps_dir.rglob("*.py"))
    assert len(scanned) > 500  # an empty walk would read as «clean»
    planted = guard._ColumnVisitor(Path("planted.py"))
    planted.visit(__import__("ast").parse("x = u.food_scanner_consent_at\n"))
    assert len(planted.violations) == 1

    violations = guard.scan_directory(apps_dir, repo_root=_PROJECT_ROOT)
    assert [v.format() for v in violations] == []


def test_cli_exits_nonzero_on_a_violation(tmp_path) -> None:
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "surface.py").write_text(
        "x = u.food_scanner_consent_at\n", encoding="utf-8"
    )
    assert guard.main(["guard", str(tmp_path / "apps")]) == 1
    (tmp_path / "apps" / "surface.py").write_text("x = u.welcomed_at\n", encoding="utf-8")
    assert guard.main(["guard", str(tmp_path / "apps")]) == 0
