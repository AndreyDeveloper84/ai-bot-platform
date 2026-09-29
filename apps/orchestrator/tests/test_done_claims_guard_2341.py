"""DRF-2341 шаг 4 — сторож класса «утверждаем выполненное, источник не знает».

Три живых дефекта одного рода: «Запись отменена», а запись жива (DRF-2337);
«передал администратору», а адресата нет (DRF-2338); «Подтверждено, ждём
вас!», а салон об этом не узнает (DRF-2344). Каждый — утверждение о
состоянии, за которым не стоит прочитанного ответа источника.

### Правило (§2 листа в нынешней редакции)

Ветка, объявившая ``claims_done=True``, обязана назвать подтверждение вида
``источник.ручка:что он ОТВЕТИЛ``, и:

1. **источник — из закрытого списка** (:data:`apps.skills.base.
   CLAIM_EVIDENCE_SOURCES`). Незнакомый источник краснеет: подтверждение,
   собранное у нас, от подтверждения источника иначе не отличить — именно
   так дефект и маскируется;
2. **значение — из закрытой таблицы** :data:`ALLOWED_VALUES`. «Вызов
   состоялся» подтверждением НЕ является: в DRF-2337 вызов состоялся и ушёл
   в никуда. Поэтому ``called``, ``sent``, ``requested`` в таблице
   отсутствуют — и любое незнакомое значение краснеет, а не принимается;
3. **утверждение сверено СО ЗНАЧЕНИЕМ, а не с фактом чтения.** Поправка
   пришла из переписи Mini App: экран прочитал статус ``cancel_requested``
   и всё равно сказал «Запись отменена». Прочитать — мало; в таблице
   поэтому только те значения, которые означают СОСТОЯВШЕЕСЯ действие.

### Ровно два исключения, и оба — с листом

Больше исключений быть не должно. Список, который можно молча пополнить,
перестаёт быть исключением и становится дырой, поэтому его длина закреплена
узлом :meth:`TestTheExceptionsAreCounted.test_exactly_two`.

### Предел сторожа — назван, а не спрятан

* **Mini App не виден вовсе** — у экрана свой механизм и свой лист
  (DRF-2347, ``apps/miniapp/src/lib/claims.ts``): там метку прочитанного
  ставит клиент API, и подделать доказательство нельзя ни по типу, ни на
  ходу. Механизма ДВА на один класс, и сводить их в один не надо: носители
  разные (поле ответа навыка против метки на прочитанном значении). Ссылка
  здесь — чтобы через месяц один из них не сочли лишним;
* **правдивость источника не проверяется**: если каталог ответил «отменено»,
  не отменив, сторож поверит. Это граница метода, а не недоработка;
* **асинхронные подтверждения**: действие, подтверждаемое событием ПОЗЖЕ
  ответа, в одном ходу не проверяется;
* **ветки без признака невидимы**: охват сторожа равен полноте разметки
  (сегодня — 16 веток, перепись в листе);
* **сторож читает ИСХОДНИК**, а не живой ход: вызвать здесь каждую ветку
  нельзя. Поэтому он видит объявление, а не то, что ветка действительно
  сверила. Живой слой — отдельный (``test_no_dead_ends_live_2267``).
"""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

from apps.skills.base import CLAIM_EVIDENCE_SOURCES

#: Файлы, где живёт разметка (перепись DRF-2341, шаги 2–3).
MARKED_FILES: tuple[str, ...] = (
    "apps/skills/water/skill.py",
    "apps/skills/food_clarify/text_entry.py",
    "apps/skills/food_scanner/skill.py",
    "apps/skills/nutrition_anketa/skill.py",
    "apps/skills/welcome/skill.py",
    "apps/persona/memory_commands.py",
    "apps/bookings/callbacks.py",
)

#: Что источник может ОТВЕТИТЬ, чтобы это считалось подтверждением.
#:
#: Закрытая таблица: значение говорит о СОСТОЯВШЕМСЯ действии, а не о том,
#: что мы его попросили. ``called`` / ``sent`` / ``requested`` здесь
#: отсутствуют намеренно — это факт вызова, а не его исход.
ALLOWED_VALUES: frozenset[str] = frozenset(
    {
        "record_id",  # каталог вернул идентификатор записи
        "entry_id",  # каталог вернул идентификатор строки воды
        "log_id",  # каталог вернул идентификатор записи в дневнике
        "weight_kg",  # каталог вернул сохранённый вес (сверен с запрошенным)
        "restore_window",  # каталог вернул окно возврата удалённой записи
        "deleted",  # каталог ответил флагом удаления
        "erased",  # мост памяти ответил исходом стирания
        "recorded",  # наш журнал согласий прочитан обратно
        "2xx",  # источник ответил успехом на изменяющий вызов
        "ok",  # исполнитель вернул прочитанный успешный исход
    }
)

#: Значение-заполнитель: идентификатор, известный только на ходу
#: (``f"{CLAIM_EVIDENCE_ADMIN_TASK}:{task.pk}"``). Это сам идентификатор от
#: источника, то есть сильнейшее из возможных подтверждений.
_RUNTIME_VALUE = re.compile(r"^\{[^{}]+\}$")

#: Ровно два исключения, каждое — со своим листом и причиной.
EXCEPTIONS: dict[str, str] = {
    "apps/bookings/callbacks.py::_handle_confirm": (
        "DRF-2344 — подтверждать нечем: исходящего вызова нет вовсе, ручки "
        "«клиент подтвердил визит» у источника не существует; вопрос у владельца"
    ),
    "apps/bookings/callbacks.py::_cancel_yclients_booking": (
        "DRF-2343 — отмена YClients «по возможности»: вызов делается, но "
        "человеку говорится «отменена» при любом исходе; ждёт слова владельца"
    ),
}


def check_evidence(evidence: str) -> str | None:
    """Претензия к подтверждению, или ``None``, если оно годное.

    Одна функция на обе стороны: ею же проверяются настоящие ветки и
    отрицательные пробы. Два правила, читающие подтверждение по-разному,
    разойдутся молча — это и есть дефект, ради которого всё затеяно.
    """
    if not evidence:
        return "подтверждения нет"
    if ":" not in evidence:
        return f"нет части «что ответил»: {evidence!r}"
    source, value = evidence.split(":", 1)
    if "." not in source:
        return f"источник не назван ручкой: {evidence!r}"
    family = source.split(".", 1)[0]
    if family not in CLAIM_EVIDENCE_SOURCES:
        return f"источник вне закрытого списка: {family!r}"
    if _RUNTIME_VALUE.match(value):
        return None
    if value not in ALLOWED_VALUES:
        return f"значение вне закрытой таблицы: {value!r}"
    return None


def _claims(rel: str) -> list[tuple[str, str]]:
    """``(файл::функция, подтверждение)`` — по дереву кода, а не по соседству.

    Имя ветки берётся у ОХВАТЫВАЮЩЕЙ функции, а не у ближайшей строки выше:
    первая версия читала соседнюю константу и называла запись воды
    ``_AYLA_DOWN_FALLBACK``. Имя попадает и в ключи исключений, и в текст
    падения, поэтому оно обязано быть правдой, а не совпадением.

    Константы в подтверждении разворачиваются настоящим импортом: переименуют
    — узел упадёт, а не позеленеет на совпавшей строке. Значение, известное
    только на ходу (``{task.pk}``), остаётся заполнителем: это сам
    идентификатор от источника.
    """
    root = Path(__file__).resolve().parents[3]
    module = importlib.import_module(rel[:-3].replace("/", "."))
    tree = ast.parse((root / rel).read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    stack: list[str] = []

    def evidence_of(call: ast.Call) -> str | None:
        for kw in call.keywords:
            if kw.arg != "claims_done_evidence":
                continue
            node = kw.value
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                return node.value
            if isinstance(node, ast.JoinedStr):
                parts: list[str] = []
                for piece in node.values:
                    if isinstance(piece, ast.Constant):
                        parts.append(str(piece.value))
                    elif isinstance(piece, ast.FormattedValue):
                        parts.append(_formatted(piece.value, module))
                return "".join(parts)
        return None

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            claims = any(
                kw.arg == "claims_done"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
                for kw in node.keywords
            )
            if claims:
                evidence = evidence_of(node)
                assert evidence is not None, f"{rel}: утверждение без объявленного подтверждения"
                out.append((f"{rel}::{'.'.join(stack) or '<module>'}", evidence))
            self.generic_visit(node)

    Visitor().visit(tree)
    return out


def _formatted(node: ast.expr, module: object) -> str:
    """Подстановка внутри f-строки: константа модуля — значением, иначе заполнитель."""
    if isinstance(node, ast.Name) and node.id.isupper():
        return str(getattr(module, node.id))
    return "{" + ast.unparse(node) + "}"


# ── сторож ────────────────────────────────────────────────────────────────


class TestEveryClaimIsProved:
    def test_marked_branches_name_a_readable_outcome(self) -> None:
        """Каждое утверждение доказано — или названо исключением с листом."""
        broken: list[str] = []
        checked = 0
        for rel in MARKED_FILES:
            for name, evidence in _claims(rel):
                if name in EXCEPTIONS:
                    continue
                checked += 1
                problem = check_evidence(evidence)
                if problem:
                    broken.append(f"{name}: {problem}")
        assert checked >= 14, f"положительная пара: сторож видит ветки — {checked}"
        assert broken == [], (
            "утверждение о выполненном без прочитанного ответа источника: "
            "либо сверьте исход со значением, либо назовите лист и внесите "
            f"ветку в EXCEPTIONS с причиной. {broken}"
        )

    def test_the_declared_exceptions_still_exist(self) -> None:
        """Исключение, которое больше не нужно, — это забытая строка."""
        names = {name for rel in MARKED_FILES for name, _ in _claims(rel)}
        assert names, "положительная пара: скан видит размеченные ветки"
        gone = sorted(set(EXCEPTIONS) - names)
        assert gone == [], f"исключение без ветки — снимите его: {gone}"


class TestTheExceptionsAreCounted:
    def test_exactly_two(self) -> None:
        """Список, который можно молча пополнить, — уже не исключение, а дыра.

        Третье исключение означает четвёртый дефект класса: он обсуждается с
        главным окном, а не добавляется строкой.
        """
        assert len(EXCEPTIONS) == 2, sorted(EXCEPTIONS)
        for name, reason in EXCEPTIONS.items():
            assert re.search(r"DRF-\d+", reason), f"{name}: исключение без листа"


class TestTheRuleFailsBothWays:
    """Отрицательная проба с двух сторон — иначе правило вырождается."""

    def test_no_evidence_is_red(self) -> None:
        assert check_evidence("") == "подтверждения нет"

    @pytest.mark.parametrize(
        "evidence",
        [
            "local.uuid4:generated",  # собрано у нас — источник не из списка
            "our.own.store:saved",  # то же, другой облик
            "ayla.meals.log:called",  # вызов состоялся ≠ исход прочитан
            "ayla.meals.log:sent",
            "catalogue.appointments.cancel:requested",  # «попросили», не «сделано»
        ],
    )
    def test_fabricated_or_intentional_evidence_is_red(self, evidence: str) -> None:
        """Подтверждение, собранное у нас, и «мы позвали» — оба красные.

        Без второй половины правило выродилось бы в «поле непустое», а его
        удовлетворяет любой наш собственный идентификатор — то есть ровно
        тот способ, которым дефект и маскируется.
        """
        assert check_evidence(evidence) is not None, evidence

    def test_a_real_branch_turns_red_when_its_evidence_is_faked(self) -> None:
        """Сторож краснеет на НАСТОЯЩЕЙ ветке, если подменить подтверждение.

        Живого дефекта в размеченных ветках уже не осталось — мы их закрыли,
        а два оставшихся объявлены исключениями. Поэтому проба искусственная:
        берём подтверждение настоящей ветки и заменяем его на собранное у
        нас. Сторож, который родился зелёным, ничего не доказывает.
        """
        real = [ev for rel in MARKED_FILES for _, ev in _claims(rel) if ev]
        assert real, "положительная пара: настоящие подтверждения есть"
        assert all(check_evidence(ev) is None for ev in real)

        faked = "local.generated:" + real[0].split(":", 1)[1]
        assert check_evidence(faked) is not None, faked
