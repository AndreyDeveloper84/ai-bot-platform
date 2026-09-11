"""`NOT_APPLICABLE` — шестое состояние безопасности (свод владельца 2026-09-11 §3).

### Что оно значит и чем не является

    NORMAL          проверка применима, выполнена, сигналов нет
                    → допустим ELIG_SAFETY_CLEARED
    UNKNOWN         проверка применима, валидного результата нет
                    → fail-closed
    NOT_APPLICABLE  capability не принимает safety-sensitive решений
                    → обычное выполнение БЕЗ заявления о пройденной проверке

Три обязательных инварианта из §3, и на каждый — сторож ниже:

1. **Определяется capability, а не отсутствием поля.** Вердикт обязан назвать,
   какая capability заявила неприменимость; без имени это `UNKNOWN`.
2. **Не выдаёт `ELIG_SAFETY_CLEARED`.** Движок заявляет пройденную проверку
   только за `NORMAL`, и сравнивает через `is NORMAL`, а не через отрицательный
   список — иначе новое состояние прошло бы в список «не запрещённых».
3. **Отсутствие ключа `safety` в хранилище — `UNKNOWN`, никогда
   `NOT_APPLICABLE`.** Декодер не вправе решать за capability.

### Чего здесь нет — названо, чтобы не прочли как покрытое

§3 запрещает `NOT_APPLICABLE` при `requires_health_check`, health/symptom
input, safety-sensitive кандидате и активной safety-relevant персонализации.
Эти условия живут у **производителя** — capability, которая заявляет
неприменимость, — а не у движка, который получает уже готовый вердикт и не
видит ни health input, ни персонализации. Производителя ещё нет:
`SafetyState.NOT_APPLICABLE` объявлен в `declared_states.py` как
declared-without-producer с этой причиной. Сторож на запрещающие условия
встанет с ним.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import reason_codes as rc
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.safety_input import (
    KNOWN_SAFETY_STATES,
    Handoff,
    SafetyResult,
    SafetyState,
)
from apps.orchestrator.decision_readiness.tests.conftest import REVISION, make_input


def _not_applicable() -> SafetyResult:
    return SafetyResult(
        state=SafetyState.NOT_APPLICABLE,
        evaluated_at_revision=REVISION,
        handoff=Handoff.NONE,
        not_applicable_for="catalog.browse",
    )


def _normal() -> SafetyResult:
    return SafetyResult(
        state=SafetyState.NORMAL, evaluated_at_revision=REVISION, handoff=Handoff.NONE
    )


# ─── 1. определяется capability, не отсутствием поля ────────────────────────


def test_not_applicable_must_name_the_capability_that_declared_it() -> None:
    """Заявление без заявителя и есть «отсутствие поля» — то есть UNKNOWN."""
    with pytest.raises(ValueError, match="not_applicable_for"):
        SafetyResult(
            state=SafetyState.NOT_APPLICABLE,
            evaluated_at_revision=REVISION,
            handoff=Handoff.NONE,
        )


@pytest.mark.parametrize("state", sorted(KNOWN_SAFETY_STATES - {SafetyState.NOT_APPLICABLE}))
def test_only_not_applicable_may_name_a_capability(state: SafetyState) -> None:
    """Обратная половина: NORMAL с именем capability — противоречие, не ошибка ввода."""
    with pytest.raises(ValueError, match="only NOT_APPLICABLE"):
        SafetyResult(
            state=state,
            evaluated_at_revision=REVISION,
            handoff=Handoff.NONE,
            not_applicable_for="catalog.browse",
        )


def test_not_applicable_is_a_known_answer_not_an_absence() -> None:
    """Оно известное: capability ответила. Движок не должен блокировать его как UNKNOWN."""
    verdict = _not_applicable()

    assert verdict.is_known is True
    assert SafetyState.NOT_APPLICABLE in KNOWN_SAFETY_STATES


# ─── 2. не выдаёт ELIG_SAFETY_CLEARED — со сторожем в движке ────────────────


def test_normal_clears_safety_and_that_is_the_positive_control() -> None:
    """Сначала — что код вообще выдаётся. Без этого следующий тест зеленел бы
    на движке, который не выдаёт его никогда."""
    output = eng.evaluate(make_input(safety=_normal()))

    assert rc.ELIG_SAFETY_CLEARED in output.reason_codes


def test_not_applicable_never_claims_safety_was_cleared() -> None:
    """§3: «обычное выполнение без заявления о пройденной проверке».

    Проверяется не по одному коду: `NOT_APPLICABLE` не должен вообще
    производить ни одного SAFETY-кода — ни блокирующего, ни очищающего.
    Проверки не было; сказать о ней нечего.
    """
    output = eng.evaluate(make_input(safety=_not_applicable()))

    assert rc.ELIG_SAFETY_CLEARED not in output.reason_codes
    safety_codes = [c for c in output.reason_codes if c.startswith("SAFETY_")]
    assert safety_codes == [], safety_codes


def test_not_applicable_is_not_blocked_as_unknown() -> None:
    """Обе половины §3 разом: не UNKNOWN (не блокирует) и не NORMAL (не очищает).

    Если бы движок сравнивал через `not in {CLARIFY, STOP, UNKNOWN}`, новое
    состояние прошло бы как NORMAL. Если бы через `not in KNOWN`, — как UNKNOWN.
    Ни то, ни другое.
    """
    output = eng.evaluate(make_input(safety=_not_applicable()))

    assert output.readiness_state is not eng.ReadinessState.BLOCKED or all(
        b.type is not eng.BlockerType.SAFETY_UNKNOWN for b in output.blockers
    ), output.blockers


# ─── 3. хранилище: отсутствие ключа — UNKNOWN, не NOT_APPLICABLE ─────────────


def test_a_missing_safety_key_is_unknown_and_never_not_applicable() -> None:
    """§3 запрещает `payload.get("safety") or NOT_APPLICABLE` — вот его предмет.

    Декодер не capability и решать за неё не вправе. Старый блоб без ключа —
    это «не сказали», и единственное честное чтение — UNKNOWN.
    """
    import json

    decoded = state_mod._decode(
        json.dumps({"v": 1, "conversation_id": "c", "revision": 1, "slots": {}})
    )

    assert decoded.safety.state is SafetyState.UNKNOWN
    assert decoded.safety.state is not SafetyState.NOT_APPLICABLE
    assert decoded.safety.not_applicable_for is None


def test_not_applicable_survives_redis_with_its_capability() -> None:
    """Положительный контроль к предыдущему: настоящее NOT_APPLICABLE едет
    через хранилище вместе с именем capability, а не теряет его."""
    import json

    encoded = state_mod._encode(
        state_mod.ConversationState(conversation_id="c", revision=1).with_safety(_not_applicable())
    )
    decoded = state_mod._decode(encoded)

    assert decoded.safety.state is SafetyState.NOT_APPLICABLE
    assert decoded.safety.not_applicable_for == "catalog.browse"
    assert json.loads(encoded)["safety"]["not_applicable_for"] == "catalog.browse"


# ─── сторож на форму сравнения в движке ─────────────────────────────────────


def test_the_engine_clears_safety_by_identity_not_by_exclusion() -> None:
    """Заявление о пройденной проверке обязано стоять за `is SafetyState.NORMAL`.

    Читается дерево разбора, не текст (§37): ищется `Compare` с оператором
    `Is` и правой частью `SafetyState.NORMAL`, в теле которого появляется
    `ELIG_SAFETY_CLEARED`. Отрицательный список (`not in {...}`) впустил бы
    любое новое состояние — так NOT_APPLICABLE и получил бы заявление о
    проверке, которой не было.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(eng))
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Is)
            and isinstance(test.comparators[0], ast.Attribute)
            and test.comparators[0].attr == "NORMAL"
        ):
            continue
        body_names = {
            n.attr
            for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            if isinstance(n, ast.Attribute)
        }
        if "ELIG_SAFETY_CLEARED" in body_names:
            guarded = True
    assert guarded, "ELIG_SAFETY_CLEARED не стоит за `is SafetyState.NORMAL` в engine.py"
