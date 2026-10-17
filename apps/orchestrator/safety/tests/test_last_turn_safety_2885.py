"""DRF-2885 — вердикт последнего хода для действия с планом вне хода.

Экран Mini App сохраняет план без реплики в чат; своего вердикта у нажатия
нет, и он несёт вердикт ПОСЛЕДНЕГО хода разговора с его ревизией. Чтобы
«стоп» минуту назад не обходился кнопкой на экране, при включённом механизме
плана вердикт пишется на КАЖДОМ ходе — и на оборванном воротами тоже.

Запись
* w1 — механизм включён, теневой контур выключен: ход записан;
* w2 — оба выключены: записи нет (ноль работы на живом ходе);
* w3 — оборванный воротами ход записан блокирующим вердиктом.

Чтение
* r1 — последний ход «нормально» → его тройка;
* r2 — после «нормально» прошёл оборванный ход → читается «стоп», не прежнее;
* r3 — разговора не было → ``None``;
* r4 — последний ход ревизию открыл, а вердикт не записал → ``None``,
  прежний вердикт за нынешний не выдаётся;
* r5 — состояние истекло → ``None``;
* r6 — сбой хранилища → ``None``, не исключение.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator import dr_shadow
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.safety.gate import evaluate_inbound
from apps.orchestrator.safety.plan_turn import PlanTurnSafety, last_turn_safety

CONV = SimpleNamespace(id="conv-last-turn-2885")
PLAIN = "хочу план на неделю"
CRISIS = "я не хочу жить"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _plan_engine_on_shadow_off(settings) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    settings.DRE_SHADOW_ENABLED = False


def _turn(text: str):
    return dr_shadow.record_turn_safety(CONV, evaluate_inbound(text))


# ─── запись ──────────────────────────────────────────────────────────────


def test_w1_the_turn_is_recorded_for_the_plan_engine_without_the_shadow_contour() -> None:
    recorded = _turn(PLAIN)

    assert recorded is not None
    stored = state_mod.load(CONV.id).state
    assert stored is not None
    assert stored.safety.evaluated_at_revision == stored.revision


def test_w2_nothing_is_recorded_with_both_flags_off(settings) -> None:
    assert _turn(PLAIN) is not None  # положительный контроль: с флагом запись есть
    before = state_mod.peek_revision(CONV.id)
    settings.PLAN_ENGINE_ENABLED = False

    assert _turn(PLAIN) is None
    assert state_mod.peek_revision(CONV.id) == before


def test_w3_a_turn_cut_short_by_the_gate_is_recorded_as_blocking() -> None:
    outcome = evaluate_inbound(CRISIS)
    assert outcome.allowed is False  # ход действительно оборван воротами

    recorded = dr_shadow.record_turn_safety(CONV, outcome)

    assert recorded is not None
    assert str(recorded.assessment.state.value).upper() == "STOP"


# ─── чтение ──────────────────────────────────────────────────────────────


def test_r1_the_last_normal_turn_gives_its_own_triple() -> None:
    _turn(PLAIN)

    got = last_turn_safety(CONV.id)

    assert isinstance(got, PlanTurnSafety)
    assert got.safety_state == "NORMAL"
    assert got.evaluated_at_revision == state_mod.peek_revision(CONV.id)
    assert got.safety_policy_version.startswith("pre_check-")


def test_r2_a_blocking_turn_after_a_normal_one_is_what_the_screen_reads() -> None:
    _turn(PLAIN)
    normal = last_turn_safety(CONV.id)
    assert normal is not None and normal.safety_state == "NORMAL"

    _turn(CRISIS)
    got = last_turn_safety(CONV.id)

    assert got is not None
    assert got.safety_state == "STOP"
    assert got.evaluated_at_revision > normal.evaluated_at_revision


def test_r3_no_conversation_state_no_triple() -> None:
    assert last_turn_safety(CONV.id) is None


def test_r4_a_turn_that_opened_a_revision_without_a_verdict_hides_the_older_one() -> None:
    _turn(PLAIN)
    assert last_turn_safety(CONV.id) is not None  # прежний вердикт был читаем

    dr_shadow._open_turn_revision(CONV.id)  # ход начался, вердикт не записан

    assert last_turn_safety(CONV.id) is None


def test_r5_an_expired_state_gives_no_triple(fake_redis: FakeRedis) -> None:
    _turn(PLAIN)
    assert last_turn_safety(CONV.id) is not None

    # Два часа без хода: запись состояния истекла, счётчик ревизий жив.
    fake_redis.expire_key(state_mod._state_key(CONV.id))
    assert state_mod.load(CONV.id).lifecycle is state_mod.StateLifecycle.EXPIRED

    assert last_turn_safety(CONV.id) is None


def test_r6_a_broken_store_gives_none_not_an_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    _turn(PLAIN)
    assert last_turn_safety(CONV.id) is not None

    def _boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr(state_mod, "load", _boom)

    assert last_turn_safety(CONV.id) is None
