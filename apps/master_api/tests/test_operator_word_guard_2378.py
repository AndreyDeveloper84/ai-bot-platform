"""Сторож класса: слова «оператор» нет в строках, которые доезжают до человека.

Решение владельца (§77 п. 27 каноничного реестра, 24.09): **роли «оператор»
в системе не существует** — ни группы, ни роли, ни поля. Одним словом
названы три разные вещи: группа ``Ayla Operations``, флаг
``User.is_platform_admin`` в каталоге (это НЕ право привязки) и пустой по
умолчанию ``HANDOFF_DUTY_OPERATORS`` (он про клиентские передачи). Роли
салона — ``receptionist / admin / owner / master``. Значит «обратитесь к
оператору» отправляло человека к адресату, которого нет.

### Почему сторож по ПРИЗНАКУ, а не по списку адресов

Лист называл семь мест. Поиск по признаку нашёл девять. Узел, написанный
по списку из семи, прошёл бы зелёным — и слово осталось бы на экране.
Сторож, слепой к тому, чего нет в его списке, — это ровно тот класс, за
который мы ловим всех остальных, поэтому здесь ищется **слово в любом
русском литерале** перечисленных модулей, а не совпадение с перечнем строк.

### Что НЕ ловится, и это намеренно

* ``OPERATOR_VERIFIED`` и прочая латиница — провенанс различает
  «подтвердил человек» и «проставило правило», он остаётся;
* комментарии и докстринги — они не текст для человека, а объяснение для
  читателя кода, и слово в них уместно (вот этот файл — пример);
* ``apps/admin_api`` целиком не берётся: там слово стоит за настоящей
  очередью передач (``handoff``), у неё оператор — это дежурный человек, а
  не выдуманная роль салона. Сюда включён только один его модуль —
  ``salon_readiness``, чей текст читает салон.

Предел назван вслух: строки, собираемые из кусков в рантайме, сторож не
увидит — он читает литералы исходника. Сегодня таких в охваченных модулях
нет (проверено при заведении, 24.09.2026).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: Модули, чьи строки доезжают до человека — мастера или салона.
WATCHED = (
    "apps/master_api/views.py",
    "apps/master_api/services/onboarding_readiness.py",
    "apps/admin_api/services/salon_readiness.py",
)

#: Слово в любом падеже и числе, отдельным словом, с заглавной или без.
OPERATOR_RE = re.compile(r"(?<![А-Яа-яЁё])[Оо]ператор[а-яё]*(?![А-Яа-яЁё])")


def _visible_strings(source: str) -> list[str]:
    """Строковые литералы модуля без докстрингов.

    ``ast`` вместо регулярного выражения: он сам отличает докстринг от
    строки-значения, и комментарии в дерево не попадают вовсе.
    """

    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_word_without_a_role_is_absent_from_user_facing_strings() -> None:
    """Ни в одном наблюдаемом модуле слова нет в строке для человека."""

    offenders: list[tuple[str, str]] = []
    checked = 0
    for rel in WATCHED:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        strings = _visible_strings(source)
        # Утверждение о НАЛИЧИИ идёт первым: пустой разбор (переименовали
        # файл, сменили путь) дал бы зелёный на ровном месте.
        assert strings, f"{rel}: строк не найдено вовсе — проверь путь"
        checked += len(strings)
        offenders.extend((rel, text) for text in strings if OPERATOR_RE.search(text))

    assert checked > 50, "охват подозрительно мал — сторож читает не то"
    assert offenders == [], f"слово, за которым нет роли: {offenders}"


def test_guard_can_fail() -> None:
    """Проба на самого сторожа: подложенная строка ловится."""

    source = 'MSG = "Привязку выполнит оператор."\n'
    assert any(OPERATOR_RE.search(t) for t in _visible_strings(source))


def test_provenance_is_not_touched() -> None:
    """Внутреннее слово остаётся: латиницу шаблон не берёт."""

    assert OPERATOR_RE.search("OPERATOR_VERIFIED") is None


def test_docstrings_are_out_of_scope() -> None:
    """Объяснение для читателя кода — не текст для человека."""

    source = '"""Раньше это делал оператор."""\nMSG = "Профиль пока не подключён."\n'
    strings = _visible_strings(source)
    # Наличие первым: значение разобрано и вернулось. Без этого пустой
    # список (сломался разбор) доказывал бы «слова нет» на ровном месте.
    assert strings == ["Профиль пока не подключён."]
    assert not any(OPERATOR_RE.search(t) for t in strings)
