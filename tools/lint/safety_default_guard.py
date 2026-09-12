"""Сторож: вердикт безопасности не подставляется умолчанием (решение владельца 11.09 §3).

# Зачем он есть

Матрица безопасности §3, дословно: «Конструкция ``payload.get("safety") or
NOT_APPLICABLE`` запрещена и охраняется AST-тестом». И инвариант §18.2:
«Нет данных ≠ ``NOT_APPLICABLE``». Отсутствие вердикта имеет одно законное
имя — ``UNKNOWN`` (``SafetyResult.not_evaluated()``), и оно **закрывает**
(``BLOCKED(SAFETY_UNKNOWN)``). Любое другое состояние, подставленное там,
где вердикта не оказалось, — это ответ, которого никто не давал.

Место без вердикта читается как недоделка. Следующий добросовестный человек
найдёт «разумное умолчание» и вернёт его одной строкой — и это будет
выглядеть как забота: ``NORMAL`` «чтобы не блокировать», ``NOT_APPLICABLE``
«тут же не про здоровье». Оба — молчаливое разрешение, и ни одна проверка
потребителя этого не увидит: ``UNKNOWN`` без ревизии — законная пара, а
подставленный ``NORMAL`` на верной ревизии неотличим от честного.

Подставленный **отказ** не лучше: ``or STOP`` не даёт жалоб, но даёт вердикт,
у которого нет происхождения — ни правила, ни версии политики. Поэтому
запрещён КЛАСС «умолчание вместо вердикта», а не одно написание из §3.

# Что ловится

Четыре формы, в которых умолчание вердикта приезжает живьём::

    payload.get("safety") or NOT_APPLICABLE          # or-умолчание после get
    payload.get("safety", SafetyState.NORMAL)        # умолчание аргументом get
    getattr(state, "safety", SafetyState.NORMAL)     # то же через getattr
    verdict if verdict else SafetyState.NORMAL       # то же через if-else
    state.safety or SafetyState.NORMAL               # or-умолчание на атрибуте

«Ключ безопасности» — строка/атрибут из :data:`SAFETY_KEYS`. «Состояние» —
член ``SafetyState`` по имени (``SafetyState.NORMAL``, ``NOT_APPLICABLE`` голым
именем, строка ``"normal"`` и т.д.) — любой, **кроме** ``UNKNOWN`` и
``not_evaluated()``: они и есть то, что положено писать вместо умолчания.

# Что НЕ ловится, и это надо знать

**Переименование ключа.** Заведут ``payload["sfty"]`` — сторож ослепнет;
лечится дописыванием в :data:`SAFETY_KEYS`.

**Умолчание в две строки.** ``v = payload.get("safety")`` / ``if v is None: v =
NORMAL`` — не одно выражение, и AST-обход одиночных узлов его не сложит.
На это стоит второй рубеж — сторож потребителя ``SafetyResult.__post_init__``
(известное состояние без ревизии не собирается) — и он ловит результат, а не
форму. Названо, чтобы не читалось как закрытая граница.

**Умолчание вне Python.** Mini App и промпты этот сканер не видит.

Замер 11.09.2026: ``uv run python tools/lint/safety_default_guard.py apps/``
на ``origin/dev = 69420d39`` → ``0 violation(s)``; подстановка
``state.safety or SafetyState.NORMAL`` в
``apps/orchestrator/decision_readiness/state.py`` (кодировщик) →
``1 violation(s)`` со строкой; возврат → ``0``. Обе стороны держит
``tests/tools/test_safety_default_guard.py``.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

#: Имена, под которыми вердикт безопасности ездит в словарях и атрибутах.
SAFETY_KEYS = frozenset(
    {
        "safety",
        "safety_state",
        "safety_result",
        "safety_verdict",
        "safety_status",
    }
)

#: Члены `SafetyState`, подстановка которых умолчанием — выдуманный ответ.
#: `UNKNOWN` намеренно отсутствует: это единственное честное умолчание.
STATE_NAMES = frozenset({"NORMAL", "CAUTION", "CLARIFY", "STOP", "NOT_APPLICABLE"})
STATE_VALUES = frozenset({"normal", "caution", "clarify", "stop", "not_applicable"})

#: Файлы, где эти формы допустимы: сторож сам и его тесты (они их
#: демонстрируют, чтобы доказать обе стороны).
ALLOWED = frozenset(
    {
        "tools/lint/safety_default_guard.py",
        "tests/tools/test_safety_default_guard.py",
    }
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    detail: str

    def format(self) -> str:
        return (
            f"{self.path}:{self.line}: вердикт безопасности подставлен умолчанием — {self.detail}"
        )


def _is_state(node: ast.AST) -> bool:
    """`SafetyState.NORMAL`, голое `NOT_APPLICABLE`, строка `"normal"`."""
    if isinstance(node, ast.Attribute) and node.attr in STATE_NAMES:
        return True
    if isinstance(node, ast.Name) and node.id in STATE_NAMES:
        return True
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value in STATE_VALUES
    return False


def _is_honest_absence(node: ast.AST) -> bool:
    """`None` и `not_evaluated()` — то, что умолчанием быть МОЖЕТ."""
    if isinstance(node, ast.Constant) and node.value is None:
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "not_evaluated"
    if isinstance(node, ast.Attribute) and node.attr == "UNKNOWN":
        return True
    return False


def _names_a_safety_key(node: ast.AST) -> bool:
    """Выражение слева от умолчания говорит о безопасности?"""
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in SAFETY_KEYS:
            return True
        if isinstance(n, ast.Attribute) and n.attr in SAFETY_KEYS:
            return True
        if isinstance(n, ast.Name) and n.id in SAFETY_KEYS:
            return True
    return False


def _get_default(node: ast.Call) -> ast.AST | None:
    """Умолчание в `x.get(key, default)` / `getattr(obj, key, default)`, если ключ — safety."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "get" and len(node.args) == 2:
        key = node.args[0]
        if isinstance(key, ast.Constant) and key.value in SAFETY_KEYS:
            return node.args[1]
    if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) == 3:
        key = node.args[1]
        if isinstance(key, ast.Constant) and key.value in SAFETY_KEYS:
            return node.args[2]
    return None


def scan_source(source: str, *, path: str) -> list[Violation]:
    out: list[Violation] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        # `payload.get("safety") or NORMAL`, `state.safety or NORMAL`
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            *left, last = node.values
            if _is_state(last) and any(_names_a_safety_key(v) for v in left):
                out.append(Violation(path, node.lineno, f"or-умолчание: {ast.unparse(node)}"))
            continue
        # `payload.get("safety", NORMAL)`, `getattr(obj, "safety", NORMAL)`
        if isinstance(node, ast.Call):
            default = _get_default(node)
            if default is not None and not _is_honest_absence(default) and _is_state(default):
                out.append(Violation(path, node.lineno, f"умолчание в get: {ast.unparse(node)}"))
            continue
        # `verdict if verdict else NORMAL`
        if (
            isinstance(node, ast.IfExp)
            and _is_state(node.orelse)
            and _names_a_safety_key(node.test)
        ):
            out.append(Violation(path, node.lineno, f"if-else умолчание: {ast.unparse(node)}"))
    return out


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _detect_repo_root(start: Path) -> Path:
    current = start.resolve() if start.is_absolute() else (Path.cwd() / start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "apps").is_dir() and (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def scan_file(path: Path, *, repo_root: Path) -> list[Violation]:
    rel = _rel(path, repo_root)
    if rel in ALLOWED:
        return []
    return scan_source(path.read_text(encoding="utf-8"), path=rel)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: safety_default_guard.py <path> [<path> ...]", file=sys.stderr)
        return 2
    repo_root = _detect_repo_root(Path(argv[1]))
    found: list[Violation] = []
    scanned = 0
    for arg in argv[1:]:
        p = Path(arg)
        files = [p] if p.is_file() else sorted(p.rglob("*.py"))
        for f in files:
            scanned += 1
            found.extend(scan_file(f, repo_root=repo_root))
    for v in found:
        print(v.format())
    print(f"safety_default_guard: {len(found)} violation(s) in {scanned} file(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
