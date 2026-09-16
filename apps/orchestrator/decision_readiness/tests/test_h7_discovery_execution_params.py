"""H7 — в DISCOVERY не спрашивать execution-параметры. Сторож ВИДИМОСТИ, не запрет.

### Что этот сторож делает и чего НЕ делает

Он **не вводит запрет**. Обойти его по-прежнему можно, просто не заполнив поле —
ровно как сегодня. Он краснеет, **когда кто-то заполнит**, и отвечает на сценарий,
названный владельцем: «первый, кто заполнит `execution_required_params`, снимет
ограничение, **не зная, что снимает**». Из зелёного сторожа «H7 обеспечен» не
следует, и формулировать его так нельзя.

### Почему запрет нельзя поставить по устройству — замер, не мнение

Режим разговора физически не доходит до выбора вопроса:
`select_next_question(candidates)` принимает ОДИН аргумент, `ask_allowed()` берёт
`execution_required_params`, но не режим, а `PredicateContext` строится в
`engine.py:333` и уходит только в `evaluate_required_context`. Механизм «режим
решает, обязателен ли слот» существует и проверен — `ModeIs(Mode.EXECUTION)` при
`mode=DISCOVERY` даёт `NOT_REQUIRED` (`test_required_context.py`) — но живёт он на
стороне required-context, а не на стороне выбора вопроса. Провести режим дальше
значит править сигнатуры движка; это отдельная работа и отдельное решение.

Осторожно, ловушка имени: в `questions.py` тоже есть слово `mode`, и это
`CLARIFICATION_MODES` (`confirm_one` / `choose_many` / `free`) — способ уточнения,
а НЕ `Mode.DISCOVERY | EXECUTION`. Сторож «по mode» сослался бы не на тот род.

### Единственная точка, которую стеречь

`ReadinessInput` в боевом коде собирается ровно один раз — `dr_shadow.build_live_input`
(`dr_shadow.py:126`), и он жёстко ставит `mode=Mode.DISCOVERY`, а
`execution_required_params` не передаёт вовсе. Прежнего сторожа над этой функцией
нет: единственное упоминание в тестах — комментарий про `ledger_readable`.

### Реестр: шестёрка владельца сегодня непереводима в слоты

Закрытый перечень владельца — `budget / district / rating / time / slot / master`.
Слоты же в этом контуре **не закрытое множество**: `ConversationState.slots` —
свободный словарь, имена приходят ключами JSON (`state._decode`), а `with_slot()`
имеет один боевой «вызов» — собственное определение. Поэтому про каждое слово
честный ответ ровно один — **НЕ УСТАНОВЛЕНО**, и реестр говорит это вслух, а не
молчит. Три исхода на строку, и «нет слота» отличается от «не установлено».
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from apps.orchestrator.decision_readiness import required_context as rc

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
APPS = REPO_ROOT / "apps"

FIELD = "execution_required_params"


class SlotEvidence:
    """Три исхода, и «нет» отличается от «не знаю» (§ три состояния отчёта)."""

    SLOT_EXISTS = "slot_exists"
    NO_SLOT = "no_slot"
    NOT_ESTABLISHED = "not_established"


#: Слово владельца → чем оно выражено в контуре, и почему так записано.
#: NOT_ESTABLISHED здесь не лень, а устройство: имена слотов в боевом коде не
#: появляются литералами вовсе, поэтому «слота нет» доказать нечем.
H7_REGISTRY: tuple[tuple[str, str, str], ...] = (
    (
        "budget",
        SlotEvidence.NOT_ESTABLISHED,
        "литерал есть в пакете, но имя слота приходит из данных",
    ),
    (
        "district",
        SlotEvidence.NOT_ESTABLISHED,
        "0 вхождений во всём apps; отсутствие литерала — не отсутствие слота",
    ),
    ("rating", SlotEvidence.NOT_ESTABLISHED, "живёт полем каталога, не слотом разговора"),
    ("time", SlotEvidence.NOT_ESTABLISHED, "живёт доменным понятием брони, не слотом"),
    ("slot", SlotEvidence.NOT_ESTABLISHED, "слово занято слотом расписания — другой род"),
    ("master", SlotEvidence.NOT_ESTABLISHED, "доменная сущность, не слот разговора"),
)


def _is_production(path: pathlib.Path) -> bool:
    return "tests" not in path.parts and not path.name.startswith("test_")


def _creates_non_empty(value: ast.expr) -> bool:
    """Создание непустого множества — против ПРОБРОСА уже пришедшего значения.

    Различитель по форме узла, а не по написанию: `Name` и `Attribute` — это
    проброс (`engine.py` так передаёт параметр дальше), а литеральная коллекция
    или `frozenset({...})` с элементами — создание. Без этого различия сторож
    краснел бы на собственной проводке движка с первого дня.
    """

    if isinstance(value, (ast.Name, ast.Attribute)):
        return False
    if isinstance(value, ast.Call):
        func = getattr(value.func, "id", None) or getattr(value.func, "attr", "")
        if func != "frozenset" or not value.args:
            return False
        inner = value.args[0]
        return isinstance(inner, (ast.Set, ast.List, ast.Tuple)) and bool(inner.elts)
    if isinstance(value, (ast.Set, ast.List, ast.Tuple)):
        return bool(value.elts)
    return False


def _fills(paths: list[pathlib.Path]) -> list[str]:
    found: list[str] = []
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover — чужой сломанный файл не наш предмет
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.keyword)
                and node.arg == FIELD
                and _creates_non_empty(node.value)
            ):
                found.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}")
    return sorted(found)


def _python_files(production: bool) -> list[pathlib.Path]:
    return [p for p in APPS.rglob("*.py") if _is_production(p) is production]


# --- сам сторож ---------------------------------------------------------------


def test_no_production_code_fills_execution_required_params() -> None:
    """H7 сегодня держится на том, что поле никто не заполняет. Это и стережём."""

    assert _fills(_python_files(production=True)) == [], (
        "кто-то заполнил execution_required_params в боевом коде. Это снимает запрет "
        "владельца H7 «в DISCOVERY не спрашивать бюджет/район/рейтинг/время/слот/мастера, "
        "если человек не сказал этого сам». Прочитайте H7 прежде, чем красить тест зелёным."
    )


# --- контроль: сторож, который ничего не ловит, зелен и пуст -------------------


def test_the_scanner_would_actually_catch_a_filling() -> None:
    """Положительный контроль на синтетике — форма соседа по пакету."""

    snippet = ast.parse(
        "ReadinessInput(execution_required_params=frozenset({'price_band'}))\n"
        "PredicateContext(execution_required_params={'city'})\n"
    )
    caught = [
        node
        for node in ast.walk(snippet)
        if isinstance(node, ast.keyword) and node.arg == FIELD and _creates_non_empty(node.value)
    ]

    assert len(caught) == 2


def test_the_scanner_finds_the_two_fillings_that_exist_in_the_test_tree() -> None:
    """Эмпирический контроль: в тестах поле заполняют, и сканер обязан это видеть.

    Если однажды окажется 0 — сканер ослеп, а не дерево очистилось.
    """

    assert len(_fills(_python_files(production=False))) >= 2


def test_the_scanner_does_not_mistake_forwarding_for_filling() -> None:
    """`engine.py` передаёт параметр дальше трижды. Проброс — не заполнение."""

    forwarding = ast.parse(
        "evaluate(execution_required_params=execution_required_params)\n"
        "ask_allowed(execution_required_params=request.execution_required_params)\n"
        "f(execution_required_params=frozenset())\n"
    )
    caught = [
        node
        for node in ast.walk(forwarding)
        if isinstance(node, ast.keyword) and node.arg == FIELD and _creates_non_empty(node.value)
    ]

    assert caught == []


# --- точка, которую стережём, названа по имени --------------------------------


def test_the_only_production_builder_is_still_the_shadow_one() -> None:
    """Появился второй сборщик входа — H7 надо перечитать, а не дописать сюда."""

    builders = sorted(
        # `as_posix()`, а не `str()`: на Windows `relative_to` даёт обратные слэши,
        # и утверждение с прямыми было бы зелёным в Linux-CI и красным здесь —
        # проверкой платформы вместо проверки предмета.
        f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}"
        for path in _python_files(production=True)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "ReadinessInput"
    )

    assert builders == ["apps/orchestrator/dr_shadow.py:126"], builders


# --- реестр -------------------------------------------------------------------


def test_the_registry_covers_the_owners_closed_list_exactly() -> None:
    """Перечень закрытый: шесть слов, ни одним больше и ни одним меньше."""

    assert [word for word, _, _ in H7_REGISTRY] == [
        "budget",
        "district",
        "rating",
        "time",
        "slot",
        "master",
    ]


@pytest.mark.parametrize(("word", "outcome", "reason"), H7_REGISTRY)
def test_every_registry_row_has_one_of_three_outcomes_and_a_reason(
    word: str, outcome: str, reason: str
) -> None:
    """«Не установлено» — законный ответ; ответ без причины — нет."""

    assert outcome in {
        SlotEvidence.SLOT_EXISTS,
        SlotEvidence.NO_SLOT,
        SlotEvidence.NOT_ESTABLISHED,
    }
    assert reason.strip(), word


def test_mode_still_does_not_reach_question_selection() -> None:
    """Посылка сторожа. Изменится — сторож обязан быть переписан, а не подправлен.

    `ModeIs` существует и работает, но на стороне required-context. У выбора
    вопроса режима нет; именно поэтому запрет здесь невыразим по устройству.
    """

    from apps.orchestrator.decision_readiness import questions as q

    assert "mode" not in q.select_next_question.__annotations__
    assert "mode" not in q.ask_allowed.__annotations__
    assert hasattr(rc, "ModeIs")
