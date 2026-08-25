"""Tests for tools/lint/miniapp_style_contract.py — the DRF-1066 guard.

Two things are pinned here and they are different in kind.

The unit tests build a throwaway Mini App tree and prove the mechanics:
an unstyled class fails, a styled one passes, a stale baseline entry
fails, and runtime-built class names are left alone on purpose.

The last test pins the guard against the **real** ``apps/miniapp``. That
one is the regression that matters: the defect DRF-1066 describes is not
"the guard has a bug", it is "somebody shipped a class name with no rule
and nothing said so". A guard that passes its own fixtures while the real
tree drifts would be exactly the failure it exists to prevent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_import_boundaries.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import miniapp_style_contract as guard  # type: ignore[import-not-found]  # noqa: E402


def _app(tmp_path: Path, *, tsx: str, css: str, rel: str = "src/screens/S.tsx") -> Path:
    root = tmp_path / "miniapp"
    (root / "src" / "styles").mkdir(parents=True)
    (root / "src" / "styles" / "globals.css").write_text(css, encoding="utf-8")
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tsx, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# Mechanics.
# --------------------------------------------------------------------------


def test_a_class_with_no_rule_is_reported(tmp_path: Path) -> None:
    root = _app(
        tmp_path,
        tsx='export const S = () => <p className="lonely">x</p>;\n',
        css=".other { color: red; }\n",
    )

    assert guard.scan(root) == ["src/screens/S.tsx::lonely"]


def test_a_class_with_a_rule_is_not_reported(tmp_path: Path) -> None:
    root = _app(
        tmp_path,
        tsx='export const S = () => <p className="styled">x</p>;\n',
        css=".styled { display: block; }\n",
    )

    assert guard.scan(root) == []


def test_every_name_in_a_multi_class_attribute_is_checked(tmp_path: Path) -> None:
    """The DRF-1066 shape was one bad name sitting beside good ones."""
    root = _app(
        tmp_path,
        tsx='export const S = () => <p className="ok also-ok missing">x</p>;\n',
        css=".ok {}\n.also-ok {}\n",
    )

    assert guard.scan(root) == ["src/screens/S.tsx::missing"]


def test_runtime_built_class_names_are_deliberately_ignored(tmp_path: Path) -> None:
    """A text scan cannot resolve them; claiming otherwise would overpromise."""
    root = _app(
        tmp_path,
        tsx="export const S = () => <p className={cx('a', b)}>x</p>;\n",
        css="",
    )

    assert guard.scan(root) == []


def test_test_files_are_not_scanned(tmp_path: Path) -> None:
    """Fixtures in tests name classes that intentionally do not exist."""
    root = _app(
        tmp_path,
        tsx='it("x", () => render(<p className="fixture-only" />));\n',
        css="",
        rel="src/screens/S.test.tsx",
    )

    assert guard.scan(root) == []


def test_all_stylesheets_in_the_styles_dir_count(tmp_path: Path) -> None:
    """A rule in tokens.css satisfies the contract as much as one in globals.css."""
    root = _app(
        tmp_path,
        tsx='export const S = () => <p className="from-tokens">x</p>;\n',
        css=".elsewhere {}\n",
    )
    (root / "src" / "styles" / "tokens.css").write_text(".from-tokens {}\n", encoding="utf-8")

    assert guard.scan(root) == []


def test_a_missing_styles_directory_is_a_refusal_not_a_pass(tmp_path: Path) -> None:
    empty = tmp_path / "not-the-app"
    empty.mkdir()

    assert guard.main(["miniapp_style_contract.py", str(empty)]) == 2


# --------------------------------------------------------------------------
# Baseline mechanics — accepted debt passes, new debt fails, stale fails.
# --------------------------------------------------------------------------


def test_accepted_debt_passes_and_new_debt_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _app(
        tmp_path,
        tsx='export const S = () => <p className="known">x</p>;\n',
        css="",
    )
    monkeypatch.setattr(guard, "BASELINE", frozenset({"src/screens/S.tsx::known"}))
    assert guard.main(["miniapp_style_contract.py", str(root)]) == 0

    (root / "src" / "screens" / "S.tsx").write_text(
        'export const S = () => <p className="known fresh">x</p>;\n', encoding="utf-8"
    )
    assert guard.main(["miniapp_style_contract.py", str(root)]) == 1


def test_a_baseline_entry_that_got_styled_must_be_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ratchet: debt can only shrink, and shrinking it edits the list."""
    root = _app(
        tmp_path,
        tsx='export const S = () => <p className="was-debt">x</p>;\n',
        css=".was-debt { display: block; }\n",
    )
    monkeypatch.setattr(guard, "BASELINE", frozenset({"src/screens/S.tsx::was-debt"}))

    assert guard.main(["miniapp_style_contract.py", str(root)]) == 1


# --------------------------------------------------------------------------
# Tab bar columns — mechanics.
# --------------------------------------------------------------------------

NL = "\n"


def _tabbar_app(tmp_path: Path, *, tabs: int, columns: int) -> Path:
    """Throwaway root holding one tab bar with `tabs` tabs and `columns` columns."""
    entries = NL.join(f'  {{ key: "t{i}", label: "T{i}", to: "/x/{i}" }},' for i in range(tabs))
    return _app(
        tmp_path,
        tsx=(
            f"const tabs: TabSpec[] = [{NL}{entries}{NL}];{NL}"
            'export const Bar = () => <nav className="master-tabbar">{tabs}</nav>;' + NL
        ),
        css=(
            ".master-tabbar { display: grid; "
            f"grid-template-columns: repeat({columns}, 1fr); }}{NL}"
        ),
        rel="src/components/AdminTabBar.tsx",
    )


def test_a_bar_whose_tabs_fit_its_columns_is_not_reported(tmp_path: Path) -> None:
    root = _tabbar_app(tmp_path, tabs=4, columns=4)

    assert guard.scan_tabbar_columns(root) == []


def test_a_bar_with_more_tabs_than_columns_is_reported(tmp_path: Path) -> None:
    """The 2026-08-25 defect: five tabs in the four-column `.master-tabbar`."""
    root = _tabbar_app(tmp_path, tabs=5, columns=4)

    problems = guard.scan_tabbar_columns(root)

    assert len(problems) == 1
    assert "renders 5 tabs into a 4-column grid" in problems[0]


def test_a_root_with_no_tab_bars_at_all_is_not_a_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absence is "not applicable", not "broken".

    `main` is handed throwaway roots by the tests above, and by anything
    else pointing it at a subtree. Reporting a missing component turned
    every such root red and took an unrelated baseline test with it. What
    a rename must not do — silently disable the check — is pinned by
    `test_every_listed_tab_bar_exists` instead.
    """
    root = _app(
        tmp_path,
        tsx='export const S = () => <p className="styled">x</p>;' + NL,
        css=".styled { display: block; }" + NL,
    )
    # The real BASELINE describes the real tree, not this one-file root —
    # without this every entry reads as stale and `main` returns 1 for a
    # reason that has nothing to do with tab bars.
    monkeypatch.setattr(guard, "BASELINE", frozenset())

    assert guard.scan_tabbar_columns(root) == []
    assert guard.main(["miniapp_style_contract.py", str(root)]) == 0


# --------------------------------------------------------------------------
# The real tree.
# --------------------------------------------------------------------------


def test_every_listed_tab_bar_exists() -> None:
    """A rename must not silently switch the column check off.

    `scan_tabbar_columns` skips paths it cannot find, so this is the only
    thing standing between a moved component and a check that quietly
    stops looking at it.
    """
    app_root = _PROJECT_ROOT / "apps" / "miniapp"

    missing = [rel for rel in guard.TABBAR_COMPONENTS if not (app_root / rel).is_file()]

    assert missing == [], (
        "TABBAR_COMPONENTS names files that no longer exist — update the tuple "
        "in tools/lint/miniapp_style_contract.py to the new paths"
    )


def test_the_real_tab_bars_fit_one_row() -> None:
    """The 2026-08-25 regression, named so a revert cannot pass quietly."""
    app_root = _PROJECT_ROOT / "apps" / "miniapp"

    assert guard.scan_tabbar_columns(app_root) == []


def test_the_real_miniapp_matches_its_baseline_exactly() -> None:
    app_root = _PROJECT_ROOT / "apps" / "miniapp"
    found = set(guard.scan(app_root))

    assert sorted(found - guard.BASELINE) == [], "new unstyled classes — add a rule, not an entry"
    assert sorted(guard.BASELINE - found) == [], "stale baseline entries — delete these lines"


def test_the_booking_confirm_payment_block_is_styled() -> None:
    """The DRF-1066 instance itself, named so a revert cannot pass quietly."""
    app_root = _PROJECT_ROOT / "apps" / "miniapp"
    css = guard.stylesheet_text(app_root)

    for name in (
        "customer-confirm__payment",
        "customer-confirm__payment-title",
        "customer-confirm__payment-option",
        "customer-confirm__payment-text",
        "customer-confirm__payment-label",
        "customer-confirm__payment-hint",
    ):
        assert f".{name}" in css, f"{name} lost its rule — the payment block runs together again"
