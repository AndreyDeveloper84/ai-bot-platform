#!/usr/bin/env python3
"""Перепись мест, где строку выбирают порядком по ОДНОМУ неуникальному полю.

Это **перепись, а не сторож**. Она ничего не валит и не должна: из 37 мест,
найденных 11.09.2026, чинить следовало два. Сторож, краснеющий в остальных 34
с рождения, отключили бы через неделю — и вместе с ним перестали бы работать
те два, ради которых он заводился (DRF-1646 дал нам этот урок на храповике).

Задача, которой скрипт принадлежит: DRF-1657.

# Что ищется и почему именно так

    qs.order_by("-created_at").first()

`ORDER BY x DESC` — не полный порядок. При равенстве `x` Postgres волен
вернуть любую строку и волен вернуть в следующий раз другую: `UPDATE`
переносит строку внутри heap. Значит `.first()` после сортировки по одному
неуникальному полю отвечает на вопрос «какая из» ответом «какая-нибудь».

Насколько это важно — зависит от того, что выбирается. Для DRF-1653 это было
«кто этот человек»: в одном тенанте владелец, в другом посторонний.

# Почему AST, а не grep

Вызовы разнесены по строкам:

    qs
      .select_related("tenant")
      .order_by("-last_seen")
      .first()

`grep 'order_by(.*).first()'` такое не видит. Обход дерева видит, потому что
разматывает цепочку атрибутов независимо от переносов.

# Что скрипт НЕ знает

Он не знает, возможна ли ничья на самом деле. `miniapp_api/views.py:189`
попал в выдачу и оказался ложной тревогой: его фильтр совпадает с
`unique_together (tenant, channel, channel_user_id)`, строк не больше одной.
Проверка уникальности фильтра — работа человека, и её нельзя вывести из этого
места кода, потому что ограничение живёт в модели, а не в запросе.

Поэтому выдача — это **список для разбора**, а не список дефектов. Числу
здесь место рядом с критерием, иначе оно читается как приговор.
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys

#: Поля, по которым порядок УЖЕ полный: ничьей быть не может.
UNIQUE_HINTS: frozenset[str] = frozenset({"pk", "id", "-pk", "-id"})

#: Методы, берущие одну строку из упорядоченного набора.
TAKE_ONE: frozenset[str] = frozenset({"first", "last"})


class _Finder(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.hits: list[tuple[str, int, str, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in TAKE_ONE:
            self._walk_back(func.value, node.lineno, func.attr)
        self.generic_visit(node)

    def _walk_back(self, node: ast.AST, lineno: int, taker: str) -> None:
        """Размотать цепочку `.a().b().order_by(...)` назад до сортировки."""

        while isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "order_by":
                literals = [
                    a.value
                    for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                ]
                # Только полностью литеральный однопольный вызов: `*args` или
                # выражение означают, что порядок известен не отсюда, и
                # называть такое место дефектом было бы догадкой.
                if len(literals) == len(node.args) == 1 and literals[0] not in UNIQUE_HINTS:
                    self.hits.append((self.path, lineno, literals[0], taker))
                return
            node = node.func.value


def census(root: pathlib.Path, *, include_tests: bool = False) -> list[tuple[str, int, str, str]]:
    hits: list[tuple[str, int, str, str]] = []
    for path in sorted(root.rglob("*.py")):
        posix = path.as_posix()
        if not include_tests and ("/tests/" in posix or path.name.startswith("test_")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            # Непрочитанный файл — не «чисто». Говорим вслух, иначе перепись
            # молча занизит себя на столько файлов, сколько не разобрала.
            print(f"::warning::не разобран: {posix}: {exc}", file=sys.stderr)
            continue
        finder = _Finder(posix)
        finder.visit(tree)
        hits.extend(finder.hits)
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="apps", type=pathlib.Path)
    parser.add_argument("--include-tests", action="store_true")
    args = parser.parse_args(argv)

    if not args.root.exists():
        print(f"::error::нет каталога {args.root}", file=sys.stderr)
        return 1

    hits = census(args.root, include_tests=args.include_tests)
    # Предмет рядом с результатом: «скрипт отработал» и «скрипт смотрел на то
    # дерево» — разные утверждения.
    print(f"корень: {args.root.resolve()}   файлов с тестами: {args.include_tests}")
    print(f"НАЙДЕНО: {len(hits)} мест — порядок по одному неуникальному полю, берётся одна строка")
    print("Это список ДЛЯ РАЗБОРА: возможна ли ничья, решает ограничение модели, а не запрос.\n")
    for path, line, field, taker in sorted(hits):
        print(f"  {path}:{line}  order_by({field!r}).{taker}()")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
