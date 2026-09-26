"""Один исход для нечитаемого входа у AST-сторожей (DRF-2538).

# Дефект

Семь сторожей из восьми разбирали Python так::

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []          # «нарушений нет»

Файл, который не разбирается, был для сторожа неотличим от чистого. Замер на
живом дереве: три подложенных нечитаемых файла — и все семь отвечали exit 0,
а ``safety_default_guard`` ещё и засчитывал их в охват («in 2224 file(s)» →
«in 2227 file(s)»). Восьмой, ``personal_field_guard``, падал громко, но
трейсбэком без имени файла.

У трёх из них проглатывание было записано решением: «Broken syntax is ruff's
job, not ours». Оно верно про красноту ``dev`` — ruff в том же джобе краснеет
на той же ошибке. И неверно про смысл зелени самого сторожа: «0 нарушений»
утверждает что-то о коде, только если код был прочитан.

# Правило

Нечитаемый вход — отдельный исход, а не ноль. Сторож называет файл и
причину, печатает охват «разобрано N, не разобрано M» и завершается красным,
если M > 0. Правила сторожей это не меняет: ``scan_*`` по-прежнему отдают
только нарушения, а нечитаемые копятся здесь и предъявляются в ``finish``.

Учёт — по пути, а не по вызову: ``import_boundaries`` разбирает одни и те же
файлы двумя проходами, и один файл не должен считаться дважды.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_parsed: set[str] = set()
_unparsed: dict[str, str] = {}


def reset() -> None:
    """Начать учёт заново — зовётся в начале ``main`` каждого сторожа."""
    _parsed.clear()
    _unparsed.clear()


def parse_or_report(source: str, path: Path | str) -> ast.Module | None:
    """Разобрать ``source``; на ``SyntaxError`` записать файл и вернуть None."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        _unparsed[str(path)] = f"SyntaxError: {exc.msg} (строка {exc.lineno})"
        return None
    _parsed.add(str(path))
    return tree


def unreadable(path: Path | str, exc: OSError) -> None:
    """Файл не прочитан с диска — тот же исход, что и неразобранный."""
    _unparsed[str(path)] = f"{type(exc).__name__}: {exc}"


def parsed_count() -> int:
    return len(_parsed)


def unparsed_count() -> int:
    return len(_unparsed)


def finish(guard: str) -> int:
    """Предъявить нечитаемые и охват; 1, если хоть один файл не разобран."""
    for path, why in sorted(_unparsed.items()):
        print(f"{guard}: НЕ РАЗОБРАН {path}: {why}", file=sys.stderr)
    print(
        f"{guard}: разобрано {len(_parsed)}, не разобрано {len(_unparsed)}",
        file=sys.stderr,
    )
    return 1 if _unparsed else 0
