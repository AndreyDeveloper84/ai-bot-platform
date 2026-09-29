"""Нигде нет fallback на ``formula-tela`` — сторож литерала (DRF-1785, решение владельца R4 а).

Предел, названный честно: скан видит только строковые литералы в коде. Слаг, пришедший из
env или настройки (``MAX_BOT_TENANT_SLUG=formula-tela`` на пилоте 15.09), он не видит — это
держат тесты поведения: ingress (``max_salon`` → без тенанта), подпись салонного бота
(``resolve_tenant_slug_for_init_data`` → ``""``, незнакомец → ``None``), admin_api (404).
Докстринги и комментарии не код — скан их пропускает.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCANNED = (
    "apps/channels",
    "apps/ingress",
    "apps/identity/services",
    "apps/admin_api",
    "apps/master_api",
    "config",
)
SKIP_PARTS = ("tests", "migrations", "management")
PILOT_SLUG = "formula-tela"


def _code_literals(tree: ast.AST) -> Iterator[ast.Constant]:
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node


def _files() -> list[Path]:
    out = []
    for base in SCANNED:
        for path in sorted((ROOT / base).rglob("*.py")):
            if any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts):
                continue
            out.append(path)
    return out


def test_the_scan_sees_code_literals_and_skips_docstrings():
    sample = ast.parse('def f():\n    """formula-tela в докстринге"""\n    return "formula-tela"\n')
    assert [n.value for n in _code_literals(sample)] == [PILOT_SLUG]


def test_no_code_literal_names_the_pilot_salon():
    files = _files()
    assert len(files) >= 30, f"скан нашёл только {len(files)} файлов — не тот корень"
    found = [
        f"{path.relative_to(ROOT).as_posix()}:{node.lineno}"
        for path in files
        for node in _code_literals(ast.parse(path.read_text(encoding="utf-8")))
        if PILOT_SLUG in node.value
    ]
    assert found == [], found
