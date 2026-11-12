"""§174 реестра (DRF-2877) — нажатие несёт вердикт последнего текстового хода.

Находка приёмки 11.11.2026: после тревожной фразы нажатие ``cb:plan:step``
чеканило свой вердикт по тексту кнопки — «нормально» — и затирало «стоп»,
так что шаг плана открывался. Решение владельца 12.11: нажатие — не
высказывание, своего вердикта не пишет и несёт вердикт последнего текста.

Опознание
* k1 — нажатие MAX (``callback_id``) и нажатие в чате Mini App (текст ``cb:…``)
  опознаются; обычный текст — нет.

Запись
* w1 — текстовый ход пишет вердикт; пропущенное воротами нажатие — нет;
  остановленное воротами нажатие пишет (строже, а не мягче).

Наследование
* а — тревожная фраза, затем нажатие → «стоп» той же ревизии;
* в — после тревоги спокойный текст → нажатие снова «нормально», ревизия выше;
* г — текстовых ходов не было → вердикта нет (``None``), действие не шлётся;
* н — нажатие ревизии не открывает: состояние разговора после него прежнее.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator import dr_shadow
from apps.orchestrator.decision_readiness import state as state_mod
from apps.orchestrator.decision_readiness.tests.fakes import FakeRedis
from apps.orchestrator.safety.gate import evaluate_inbound
from apps.orchestrator.safety.plan_turn import (
    inherited_turn_safety,
    is_structural_turn,
    records_its_own_verdict,
)

CONV = SimpleNamespace(id="conv-structural-174")
CALM = "хочу план на неделю"
EMERGENCY = "давит в груди"
STEP_TAP = "cb:plan:step:5a5a5a5a:0"


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    monkeypatch.setattr(state_mod, "_redis_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _plan_engine_on_shadow_off(settings) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    settings.DRE_SHADOW_ENABLED = False


def _event(text: str, raw: object = None) -> SimpleNamespace:
    return SimpleNamespace(text=text, raw=raw)


def _turn(text: str, *, raw: object = None) -> None:
    """Один ход так, как его ведёт глобальный обработчик MAX."""
    outcome = evaluate_inbound(text)
    if records_its_own_verdict(
        structural=is_structural_turn(_event(text, raw)), gate_outcome=outcome
    ):
        dr_shadow.record_turn_safety(CONV, outcome)


# ─── опознание ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (_event(STEP_TAP, {"callback_id": "cb-1"}), True),  # нажатие в MAX
        (_event(STEP_TAP, {"source": "miniapp"}), True),  # быстрый ответ чата Mini App
        (_event("  " + STEP_TAP, None), True),
        (_event("что-то совсем другое", {"callback_id": "cb-2"}), True),  # MAX решает по id
        (_event(CALM, {"source": "miniapp"}), False),
        (_event(CALM, None), False),
        (_event("", {"callback_id": ""}), False),
    ],
)
def test_k1_a_tap_is_recognised_in_max_and_in_the_miniapp_chat(event, expected: bool) -> None:
    assert is_structural_turn(event) is expected


# ─── запись ──────────────────────────────────────────────────────────────


def test_w1_text_writes_a_passed_tap_does_not_a_stopped_tap_does() -> None:
    passed = evaluate_inbound(STEP_TAP)
    stopped = evaluate_inbound(EMERGENCY)
    assert passed.allowed is True and stopped.allowed is False  # ворота настоящие

    assert records_its_own_verdict(structural=False, gate_outcome=passed) is True
    assert records_its_own_verdict(structural=True, gate_outcome=passed) is False
    assert records_its_own_verdict(structural=True, gate_outcome=stopped) is True


@pytest.mark.parametrize("verdict", ["clarify", "caution", "", "что-то новое"])
def test_w2_a_tap_passed_with_anything_but_a_clean_allow_writes_its_verdict(verdict: str) -> None:
    """Ревью #2472: ``cb:`` руками с уточняющим вердиктом не теряет его."""
    outcome = SimpleNamespace(allowed=True, verdict=verdict)

    assert records_its_own_verdict(structural=True, gate_outcome=outcome) is True
    assert (
        records_its_own_verdict(
            structural=True, gate_outcome=SimpleNamespace(allowed=True, verdict="allow")
        )
        is False
    )


# ─── наследование ────────────────────────────────────────────────────────


def test_a_after_an_alarming_phrase_a_tap_carries_stop() -> None:
    _turn(CALM)
    _turn(EMERGENCY)
    stop_revision = state_mod.peek_revision(CONV.id)

    _turn(STEP_TAP, raw={"callback_id": "cb-3"})
    got = inherited_turn_safety(CONV)

    assert got is not None
    assert got.safety_state == "STOP"
    assert got.evaluated_at_revision == stop_revision


def test_v_a_calm_text_after_the_alarm_opens_the_step_again() -> None:
    _turn(EMERGENCY)
    stopped = inherited_turn_safety(CONV)
    assert stopped is not None and stopped.safety_state == "STOP"

    _turn(CALM)
    _turn(STEP_TAP, raw={"source": "miniapp"})
    got = inherited_turn_safety(CONV)

    assert got is not None
    assert got.safety_state == "NORMAL"
    assert got.evaluated_at_revision > stopped.evaluated_at_revision


def test_g_no_text_turn_no_verdict() -> None:
    _turn(STEP_TAP, raw={"callback_id": "cb-4"})

    assert inherited_turn_safety(CONV) is None
    assert inherited_turn_safety(None) is None


def test_n_a_tap_opens_no_revision() -> None:
    _turn(CALM)
    before = state_mod.peek_revision(CONV.id)

    _turn(STEP_TAP, raw={"callback_id": "cb-5"})
    _turn(STEP_TAP, raw={"source": "miniapp"})

    assert state_mod.peek_revision(CONV.id) == before
