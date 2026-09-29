"""Tests for tools/lint/miniapp_token_contrast.py — the DRF-1462 guard.

Two things are pinned here and they are different in kind.

The unit tests prove the mechanics against throwaway input: the contrast
arithmetic reproduces published WCAG figures, a signed value that fails
AA is caught, a hash that is an issue number is not mistaken for a
colour, and a colour written outside ``tokens.css`` is.

The last tests pin the guard against the **real** ``apps/miniapp``. Those
are the regressions that matter. The defect DRF-1462 describes is not
"the guard has a bug", it is "the palette lived in five places and two of
fourteen variables still agreed with any canon". A guard that passes its
own fixtures while the real tree drifts would be exactly the failure it
exists to prevent.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_miniapp_style_contract.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import miniapp_token_contrast as guard  # type: ignore[import-not-found]  # noqa: E402

APP_ROOT = _PROJECT_ROOT / "apps" / "miniapp"

# A minimal tokens.css: only the shape the guard parses matters.
_TOKENS_HEAD = """:root {
  --c-bg: #f8fafc;
  --c-surface-1: #ffffff;
  --c-surface-2: #f3f4f6;
  --c-divider: #e5e7eb;
  --c-text-primary: #111827;
  --c-text-secondary: #69707e;
  --c-text-on-accent: #ffffff;
  --c-accent: #4452ff;
  --c-accent-pressed: #3843d1;
  --c-accent-subtle: #e9eaff;
  --c-success: #16803d;
  --c-warning: #8f5c06;
  --c-danger: #c91111;
}
"""
_TOKENS_DARK = """@media (prefers-color-scheme: dark) {
  :root {
    --c-bg: #111827;
    --c-surface-1: #1f2937;
    --c-surface-2: #374151;
    --c-divider: #4b5563;
    --c-text-primary: #f8fafc;
    --c-text-secondary: #a7acb5;
    --c-text-on-accent: #111827;
    --c-accent: #9da5ff;
    --c-accent-pressed: #afb5ff;
    --c-accent-subtle: #2a314e;
    --c-success: #22c55e;
    --c-warning: #f7ad30;
    --c-danger: #f7a7a7;
  }
}
"""


def _themes(light_overrides: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    """Разобранная эталонная палитра, при желании с подменой одной роли."""
    parsed = guard.palettes(_TOKENS_HEAD + _TOKENS_DARK)
    parsed["light"].update(light_overrides or {})
    return parsed


# ── арифметика ─────────────────────────────────────────────────────────────


def test_contrast_reproduces_published_figures() -> None:
    """Опорные величины, которые можно проверить любым калькулятором."""
    assert round(guard.contrast("#000000", "#ffffff"), 2) == 21.0
    assert round(guard.contrast("#ffffff", "#ffffff"), 2) == 1.0
    # Подписи борда DRF-1181, из-за которых оттенки пришлось двигать.
    assert round(guard.contrast("#22c55e", "#ffffff"), 2) == 2.28
    assert round(guard.contrast("#f59e0b", "#ffffff"), 2) == 2.15
    assert round(guard.contrast("#5b68ff", "#ffffff"), 2) == 4.31


def test_contrast_is_symmetric() -> None:
    assert guard.contrast("#4452ff", "#ffffff") == guard.contrast("#ffffff", "#4452ff")


def test_mix_matches_the_browser_blend() -> None:
    assert guard.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert guard.mix("#c91111", "#ffffff", 0.10) == "#fae7e7"


def test_three_digit_hex_expands() -> None:
    assert guard.contrast("#fff", "#ffffff") == 1.0


# ── проверка контраста ─────────────────────────────────────────────────────


def test_the_reference_palette_is_clean() -> None:
    themes = _themes()

    assert sorted(themes["light"]) == sorted(themes["dark"]) != [], "нечего проверять"
    assert guard.check_contrast(themes) == []


def test_a_signed_value_that_fails_aa_is_caught() -> None:
    """Подпись `#22C55E` даёт 2.28:1 — ровно то, ради чего страж есть."""
    problems = guard.check_contrast(_themes({"success": "#22c55e"}))

    assert any("--c-success on --c-bg" in p for p in problems)
    assert any("2.18:1" in p for p in problems)


def test_a_theme_that_loses_a_role_is_caught() -> None:
    themes = _themes()
    del themes["dark"]["accent-subtle"]

    problems = guard.check_contrast(themes)

    assert any("declare different roles" in p for p in problems)


def test_a_signed_neutral_moved_off_its_label_is_caught() -> None:
    """Три нейтрали проходят AA как подписаны — двигать их нечем оправдать."""
    problems = guard.check_contrast(_themes({"bg": "#fafafa"}))

    assert any("--c-bg is #fafafa" in p and "#f8fafc" in p for p in problems)


def test_raising_a_self_wash_above_ten_percent_is_caught() -> None:
    """Доля подложки — не свободный параметр: на 18 % текст перестаёт читаться."""
    themes = _themes()
    original = guard.SELF_WASH_SHARE
    try:
        guard.SELF_WASH_SHARE = 0.18
        problems = guard.check_contrast(themes)
    finally:
        guard.SELF_WASH_SHARE = original

    assert any("on its own 18 % wash" in p for p in problems)


# ── проверка единственного источника ───────────────────────────────────────


def _tree(tmp_path: Path, *, extra: str, rel: str = "src/screens/S.tsx") -> Path:
    root = tmp_path / "miniapp"
    (root / "src" / "styles").mkdir(parents=True)
    (root / "src" / "styles" / "tokens.css").write_text(
        _TOKENS_HEAD + _TOKENS_DARK, encoding="utf-8"
    )
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(extra, encoding="utf-8")
    return root


def test_a_colour_written_outside_tokens_css_is_caught(tmp_path: Path) -> None:
    root = _tree(tmp_path, extra='const s = { color: "#ff8a00" };\n')

    problems = guard.check_single_source(root)

    assert len(problems) == 1
    assert "src/screens/S.tsx:1" in problems[0]


def test_a_dead_fallback_behind_an_undeclared_variable_is_caught(tmp_path: Path) -> None:
    """Так пряталось большинство из 95: не цветом, а фолбэком."""
    root = _tree(tmp_path, extra="a { color: var(--c-border, #e0e0e3); }\n", rel="src/styles/x.css")

    problems = guard.check_single_source(root)

    assert len(problems) == 1
    assert "#e0e0e3" in problems[0]


def test_an_issue_number_is_not_a_colour(tmp_path: Path) -> None:
    """`#951` и `#798` — номера задач. Ложное срабатывание здесь сделало бы
    страж шумным ровно там, где комментарии полезнее всего."""
    root = _tree(
        tmp_path,
        extra=(
            'it("nav «День» tab leads to the day view (/customer/wellness, #951)", () => {});\n'
            "// rebuilt from PR #798's horizontal-scroll bar\n"
        ),
    )

    # Presence: наивная часть правила на этих строках как раз срабатывает —
    # отсекает их вторая. Без этой проверки тест прошёл бы и тогда, когда
    # страж вообще перестал бы смотреть на файл.
    text = (root / "src" / "screens" / "S.tsx").read_text(encoding="utf-8")
    assert [ln for ln in text.splitlines() if guard.VALUE_POSITION.search(ln)] != []

    assert guard.check_single_source(root) == []


def test_tokens_css_itself_is_not_flagged(tmp_path: Path) -> None:
    root = _tree(tmp_path, extra="a { color: var(--c-accent); }\n", rel="src/styles/x.css")

    tokens = (root / "src" / "styles" / "tokens.css").read_text(encoding="utf-8")
    assert guard.VALUE_POSITION.findall(tokens) != [], "в фикстуре нет цветов"

    assert guard.check_single_source(root) == []


# ── настоящий apps/miniapp ─────────────────────────────────────────────────


def test_the_real_miniapp_has_one_source_of_colour() -> None:
    tokens = (APP_ROOT / guard.TOKENS).read_text(encoding="utf-8")
    assert guard.VALUE_POSITION.findall(tokens) != [], (
        "в tokens.css не осталось цветовых литералов — проверка ослепла"
    )

    assert guard.check_single_source(APP_ROOT) == []


def test_the_real_palette_meets_aa() -> None:
    themes = guard.palettes((APP_ROOT / guard.TOKENS).read_text(encoding="utf-8"))
    assert themes["light"] and themes["dark"], "разбор tokens.css вернул пусто"

    assert guard.check_contrast(themes) == []


def test_the_real_palette_still_declares_fourteen_roles_in_both_themes() -> None:
    """Если ролей стало меньше, проверка контраста тихо перестаёт покрывать
    то, что перестало объявляться."""
    themes = guard.palettes((APP_ROOT / guard.TOKENS).read_text(encoding="utf-8"))

    # Тринадцать HEX плюс `--c-overlay`, который задан через `rgb()` и в
    # разбор HEX не попадает по устройству.
    assert len(themes["light"]) == 13
    assert len(themes["dark"]) == 13
