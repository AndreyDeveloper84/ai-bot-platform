"""DRF-2885 — тройка безопасности для действий с планом берётся из хода.

Каталог требует у действия с планом состояние, версию политики и ревизию и
только хранит их. Его два требования: вердикт и ревизия — из одного хода;
ревизия в разговоре не убывает. Ноль или прошлую ревизию вместо настоящей
слать нельзя — нет тройки, нет действия.

* t1 — без теневого контура ревизия открывается здесь, безусловно;
* t2 — состояние уходит каталожным словарём, верхним регистром;
* t3 — два хода: ревизия растёт, каждый вердикт — при своей;
* t4 — теневой контур уже записал ход: вторая ревизия не открывается;
* t5 — оборванный ход, ход без разговора, вердикт без сырого результата,
  сбой хранилища — ``None``;
* t6 — версия политики — отпечаток действующего гейта, не литерал.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator import dr_shadow
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.safety import assessment
from apps.orchestrator.safety.gate import evaluate_inbound
from apps.orchestrator.safety.plan_turn import PlanTurnSafety, plan_turn_safety
from apps.orchestrator.safety.record import record_verdict

CONV = SimpleNamespace(id="conv-plan-turn-2885")
PLAIN = "хочу план на неделю"
#: Фраза из «расплывчато про здоровье»: гейт пропускает, вердикт — CLARIFY.
VAGUE = "почему болит спина после массажа"
CRISIS = "я не хочу жить"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


def _revision() -> int | None:
    return state_mod.peek_revision(CONV.id)


def test_t1_the_revision_is_opened_here_without_the_shadow_contour() -> None:
    assert _revision() is None  # до хода ревизии нет вовсе

    got = plan_turn_safety(CONV, evaluate_inbound(PLAIN))

    assert isinstance(got, PlanTurnSafety)
    assert got.evaluated_at_revision == _revision()
    # Прочитано тем же путём, каким читает потребитель: вердикт лежит в
    # состоянии разговора при этой же ревизии.
    stored = state_mod.load(CONV.id).state
    assert stored is not None
    assert stored.revision == got.evaluated_at_revision
    assert stored.safety is not None
    assert stored.safety.evaluated_at_revision == got.evaluated_at_revision


@pytest.mark.parametrize(("phrase", "expected"), [(PLAIN, "NORMAL"), (VAGUE, "CLARIFY")])
def test_t2_the_state_is_the_catalogs_word_in_upper_case(phrase: str, expected: str) -> None:
    outcome = evaluate_inbound(phrase)
    assert outcome.allowed is True  # ход продолжается — иначе тройки бы не было

    got = plan_turn_safety(CONV, outcome)

    assert got is not None
    assert got.safety_state == expected
    assert got.as_wire() == {
        "safety_state": expected,
        "safety_policy_version": got.safety_policy_version,
        "evaluated_at_revision": got.evaluated_at_revision,
    }


def test_t3_two_turns_the_revision_grows_and_each_verdict_keeps_its_own() -> None:
    first = plan_turn_safety(CONV, evaluate_inbound(PLAIN))
    second = plan_turn_safety(CONV, evaluate_inbound(VAGUE))

    assert first is not None and second is not None
    assert second.evaluated_at_revision > first.evaluated_at_revision
    assert (first.safety_state, second.safety_state) == ("NORMAL", "CLARIFY")


def test_t4_a_turn_already_recorded_by_the_shadow_contour_is_not_opened_twice() -> None:
    outcome = evaluate_inbound(PLAIN)
    # То, что делает теневой контур в начале хода, когда он включён.
    dr_shadow._open_turn_revision(CONV.id)
    recorded = record_verdict(CONV.id, outcome.result, source="pre_check")
    before = _revision()

    got = plan_turn_safety(CONV, outcome, recorded=recorded)

    assert got is not None
    assert got.evaluated_at_revision == before
    assert _revision() == before  # вторая ревизия за ход не открыта

    # Положительная пара: без переданной записи тот же вызов ревизию открывает.
    again = plan_turn_safety(CONV, outcome)
    assert again is not None
    assert again.evaluated_at_revision > before


def test_t5_a_short_circuited_turn_has_no_triple_and_opens_nothing() -> None:
    outcome = evaluate_inbound(CRISIS)
    assert outcome.allowed is False  # ход обрывается кризисным ответом

    assert plan_turn_safety(CONV, outcome) is None
    assert _revision() is None


def test_t5_no_conversation_no_raw_result_no_triple() -> None:
    outcome = evaluate_inbound(PLAIN)

    assert plan_turn_safety(None, outcome) is None
    assert plan_turn_safety(CONV, None) is None
    assert (
        plan_turn_safety(CONV, SimpleNamespace(allowed=True, result=None, verdict="allow")) is None
    )
    assert _revision() is None
    # Положительная пара: с тем же вердиктом и разговором тройка есть.
    assert plan_turn_safety(CONV, outcome) is not None


def test_t5_a_broken_store_gives_none_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> FakeRedis:
        raise RuntimeError("redis is down")

    assert plan_turn_safety(CONV, evaluate_inbound(PLAIN)) is not None  # с живым хранилищем — есть
    monkeypatch.setattr(state_mod, "_redis_client", boom)

    assert plan_turn_safety(CONV, evaluate_inbound(PLAIN)) is None


def test_t6_the_policy_version_is_the_gates_fingerprint() -> None:
    got = plan_turn_safety(CONV, evaluate_inbound(PLAIN))

    assert got is not None
    assert got.safety_policy_version == assessment.policy_version()
    assert got.safety_policy_version.startswith("pre_check-")
    assert 0 < len(got.safety_policy_version) <= 64  # каталог режет на 64 знаках
