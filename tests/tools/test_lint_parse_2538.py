"""Нечитаемый файл — не «чисто»: восемь AST-сторожей называют его и краснеют (DRF-2538).

До листа семь сторожей из восьми на `SyntaxError` отдавали пустой список, то
есть «нарушений нет», и завершались exit 0; `safety_default_guard` ещё и
засчитывал нечитаемый файл в охват. Восьмой, `personal_field_guard`, падал
трейсбэком, в последней строке которого не было имени файла.

Узел — подмена на каждого: одно и то же дерево, сначала без нечитаемых
файлов (охват печатается, «не разобрано 0»), потом с ними. Проверяется не
только exit ≠ 0, но и ИМЯ файла с причиной: красный без имени заставляет
человека искать глазами.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import lint_parse  # type: ignore[import-not-found]  # noqa: E402

#: Все сторожа CI, которые разбирают Python через `ast.parse`. Остальные шесть
#: из ci.yml читают TS/CSS/текст регулярками и к предмету не относятся.
GUARDS = (
    "red_zone_guard",
    "import_boundaries",
    "consent_column_guard",
    "food_scanner_column_guard",
    "personal_field_guard",
    "negative_assert_guard",
    "nutrition_target_guard",
    "safety_default_guard",
)

BROKEN = "def f(:\n    pass\n"
#: Одно место на каждый вид файлов, которые читают разные сторожа: боевой
#: модуль, модели (personal_field) и узел (negative_assert читает только их).
BROKEN_FILES = (
    "apps/zz2538/services.py",
    "apps/zz2538/models.py",
    "apps/zz2538/tests/test_zz2538.py",
)


def _tree(root: Path) -> Path:
    """Минимальное дерево, на котором стартует каждый из восьми.

    `personal_field_guard` без реестра, модуля политики ключей и `BotUser` падает
    `LookupError` раньше, чем что-то разберёт, — это его правило, не предмет.
    """
    (root / "pyproject.toml").write_text("", encoding="utf-8")
    for rel in (
        "apps/identity/personal_fields.py",
        "apps/identity/services/memory_key_policy.py",
        "apps/identity/models.py",
    ):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_PROJECT_ROOT / rel, dst)
    return root / "apps"


def _run(guard: str, apps: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    module = importlib.import_module(guard)
    code = module.main([f"{guard}.py", str(apps)])
    out = capsys.readouterr()
    return code, out.out + out.err


@pytest.mark.parametrize("guard", GUARDS)
def test_an_unparsed_file_is_named_and_red(guard, tmp_path, capsys):
    apps = _tree(tmp_path)

    code, text = _run(guard, apps, capsys)
    coverage = f"{guard}: разобрано "
    assert coverage in text, text[-600:]
    assert f"{guard}: НЕ РАЗОБРАН" not in text, text[-600:]
    assert ", не разобрано 0" in text, text[-600:]

    for rel in BROKEN_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BROKEN, encoding="utf-8")

    code, text = _run(guard, apps, capsys)
    named = [line for line in text.splitlines() if f"{guard}: НЕ РАЗОБРАН" in line]
    assert named, text[-600:]
    assert all("zz2538" in line and "SyntaxError" in line for line in named), named
    assert code == 1, (code, text[-600:])


def test_one_file_parsed_twice_is_counted_once():
    """`import_boundaries` разбирает одни файлы двумя проходами — учёт по пути."""
    lint_parse.reset()
    for _ in range(2):
        assert lint_parse.parse_or_report("x = 1\n", "a.py") is not None
        assert lint_parse.parse_or_report(BROKEN, "b.py") is None

    assert (lint_parse.parsed_count(), lint_parse.unparsed_count()) == (1, 1)
    assert lint_parse.finish("probe") == 1
