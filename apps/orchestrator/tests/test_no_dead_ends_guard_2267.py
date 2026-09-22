"""DRF-2267 (решение владельца CD §72) — сторож «новое завершающее сообщение без кнопок краснит».

Слой 2 из раздела 5 переписи ``docs/BOT_DEAD_ENDS_CENSUS_2026-09-21.md``:
разбор синтаксического дерева кода бота. Каждое ``SkillResult(...)`` и
``DiscoveryReply(...)`` без клавиатуры (нет ``action_data``, ``action_data=None``
или словарь без ``buttons`` / ``attachments`` / ``button_rows``) считается по
``файл::функция``:

* функция в :data:`EXCEPTIONS` — без кнопок по правилу, с причиной (кризис,
  медицинские, молчание при операторе, петля B13);
* иначе число должно совпадать с долгом :data:`DEBT` — новый ответ без кнопок
  краснит, а ответ, получивший кнопки, требует уменьшить долг (иначе он
  лжёт о форме кода).

Ограничения (измерены в переписи, раздел 1) — названы, а не спрятаны:
``action_data=<переменная>`` считается «с кнопками» (узнать нельзя без
запуска); ответы строкой (``try_handle_opt_out`` и соседи) сторож не видит.
Живой прогон лестницы обработчика (слой 1) — отдельным срезом.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from apps.orchestrator.tests.dead_ends_debt_2267 import DEBT, EXCEPTIONS

REPO = Path(__file__).resolve().parents[3]
APPS = REPO / "apps"
TARGETS = (
    APPS / "skills",
    APPS / "orchestrator",
    APPS / "bookings" / "callbacks.py",
    APPS / "persona" / "memory_commands.py",
    APPS / "recommendation" / "taps.py",
    APPS / "channels" / "max",
)
REPLY_CALLS = frozenset({"SkillResult", "DiscoveryReply"})
KEYBOARD_KEYS = frozenset({"buttons", "attachments", "button_rows"})


def _files() -> list[Path]:
    out: list[Path] = []
    for target in TARGETS:
        if target.is_file():
            out.append(target)
            continue
        out.extend(
            p
            for p in target.rglob("*.py")
            if "tests" not in p.parts and "migrations" not in p.parts
        )
    return out


def _buttonless(call: ast.Call) -> bool:
    kw = {k.arg: k.value for k in call.keywords if k.arg}
    if "action_data" not in kw:
        return True
    value = kw["action_data"]
    if isinstance(value, ast.Constant) and value.value is None:
        return True
    if isinstance(value, ast.Dict):
        keys = {k.value for k in value.keys if isinstance(k, ast.Constant)}
        return not keys & KEYBOARD_KEYS
    return False


def scan_source(source: str, rel: str) -> tuple[int, Counter[str]]:
    """``(всего ответов, Counter[файл::функция → без кнопок])`` по одному модулю."""
    tree = ast.parse(source)
    stack: list[str] = []
    counts: Counter[str] = Counter()
    total = 0

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            nonlocal total
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name in REPLY_CALLS:
                total += 1
                if _buttonless(node):
                    counts[f"{rel}::{'.'.join(stack) or '<module>'}"] += 1
            self.generic_visit(node)

    _Visitor().visit(tree)
    return total, counts


def _scan() -> tuple[int, Counter[str]]:
    total = 0
    counts: Counter[str] = Counter()
    for path in _files():
        rel = path.relative_to(REPO).as_posix()
        t, c = scan_source(path.read_text(encoding="utf-8"), rel)
        total += t
        counts.update(c)
    return total, counts


def test_positive_pair_the_scan_sees_replies_with_and_without_buttons() -> None:
    total, counts = _scan()
    assert total > 200, total
    assert sum(counts.values()) < total  # есть ответы С кнопками
    assert counts  # и есть без — иначе «всё зелёное» прошло бы на пустом множестве
    assert EXCEPTIONS and DEBT


def test_the_guard_catches_a_new_buttonless_reply() -> None:
    """Стража стражи: новый ответ без кнопок виден, ответ с кнопками — нет."""
    src = (
        "def fresh():\n"
        "    SkillResult(reply_text='Готово.')\n"
        "    SkillResult(reply_text='Готово.', action_data={'buttons': [{'label': 'Меню'}]})\n"
        "    DiscoveryReply(text='Ок', action_data=None)\n"
        "    DiscoveryReply(text='Ок', action_data={'mode': 'x'})\n"
    )
    total, counts = scan_source(src, "apps/x.py")
    assert total == 4
    assert counts == Counter({"apps/x.py::fresh": 3})


def test_no_new_buttonless_reply_outside_exceptions_and_debt() -> None:
    _total, counts = _scan()
    assert counts, "положительная пара: скан видит ответы без кнопок"
    grown = sorted(
        f"{key}: {n} (долг {DEBT.get(key, 0)})"
        for key, n in counts.items()
        if key not in EXCEPTIONS and n > DEBT.get(key, 0)
    )
    assert grown == [], (
        "новый ответ без кнопок следующего шага (§72): добавь 1–2 кнопки и «Меню» "
        "или внеси функцию в EXCEPTIONS с причиной"
    )


def test_debt_is_not_stale() -> None:
    _total, counts = _scan()
    assert counts, "положительная пара: скан видит ответы без кнопок"
    shrunk = sorted(
        f"{key}: {counts.get(key, 0)} < долг {n}"
        for key, n in DEBT.items()
        if counts.get(key, 0) < n
    )
    assert shrunk == [], "ответ получил кнопки — уменьши DEBT (или сними строку)"


def test_exceptions_still_exist() -> None:
    _total, counts = _scan()
    assert counts, "положительная пара: скан видит ответы без кнопок"
    gone = sorted(key for key in EXCEPTIONS if key not in counts)
    assert gone == [], "исключение больше не нужно — сними его с причиной"
