"""DRF-1937 — какой вход держит готовность: код, а не одно имя на всех.

Наружу движок отдаёт один код ``BLOCK_READINESS_INPUT_UNAVAILABLE``, а причина
жила текстом в ``Blocker.attribution`` и до строки тени не доходила. Здесь:

* каждое из семи условий §15.2 даёт свой код — ``TestEachConditionHasItsCode``;
* все недоступные входы — по порядку движка — ``TestAllInEngineOrder``;
* вердикт движка и список тени — из одного источника: текст блокера
  ``evaluate`` совпадает с первым элементом списка — ``TestOneSource``;
* незнакомое условие в тени — ``UNCLASSIFIED``, текста нет —
  ``TestShadowCodes``.
"""

from __future__ import annotations

import pytest

from apps.orchestrator import dr_shadow
from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness.policy import ControlledPolicy
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.tests.conftest import REVISION, candidates, make_input

#: Условие → вход, у которого сломано ровно оно.
CASES: list[tuple[str, dict[str, object]]] = [
    (
        eng.INPUT_LEDGER_UNREADABLE,
        {"availability": eng.InputAvailability(probe_available=True, candidates_fresh=True)},
    ),
    (
        eng.INPUT_PROBE_UNAVAILABLE,
        {"availability": eng.InputAvailability(ledger_readable=True, candidates_fresh=True)},
    ),
    (
        eng.INPUT_CANDIDATES_STALE,
        {"availability": eng.InputAvailability(ledger_readable=True, probe_available=True)},
    ),
    (
        eng.INPUT_SAFETY_REVISION_BEHIND,
        {
            "safety": SafetyResult(
                state=SafetyState.NORMAL, evaluated_at_revision=REVISION - 1, handoff=Handoff.NONE
            )
        },
    ),
    (eng.INPUT_ELIGIBILITY_UNKNOWN, {"candidates": candidates(recommendation_eligible_count=None)}),
    (
        eng.INPUT_CONFLICTS_NOT_COMPUTED,
        {"measures": eng.Measures(completeness=1.0, conflicts=None)},
    ),
    (
        eng.INPUT_TAU_UNCALIBRATED,
        {"policy": ControlledPolicy(policy_version=1, ranking_comparison_depth=3)},
    ),
]


def _found(request: eng.ReadinessInput) -> tuple[tuple[str, str], ...]:
    return eng.unavailable_inputs(
        availability=request.availability,
        probe_usable=request.probe_usable,
        safety=request.safety,
        state_revision=request.state_revision,
        candidates=request.candidates,
        measures=request.measures,
        policy=request.policy,
    )


class TestEachConditionHasItsCode:
    def test_the_dictionary_is_seven_codes_in_engine_order(self):
        assert eng.UNAVAILABLE_INPUT_CODES == tuple(code for code, _ in CASES)
        assert len(set(eng.UNAVAILABLE_INPUT_CODES)) == 7

    def test_a_complete_input_has_none(self):
        assert _found(make_input()) == ()

    @pytest.mark.parametrize(("code", "override"), CASES, ids=[c for c, _ in CASES])
    def test_one_broken_input_gives_exactly_its_code(self, code, override):
        found = _found(make_input(**override))

        assert [c for c, _ in found] == [code]


class TestAllInEngineOrder:
    def test_everything_broken_lists_all_seven_in_order(self):
        broken: dict[str, object] = {}
        for _code, override in CASES:
            if "availability" in override:
                continue
            broken.update(override)
        broken["availability"] = eng.InputAvailability()
        broken["probe"] = None

        assert [c for c, _ in _found(make_input(**broken))] == list(eng.UNAVAILABLE_INPUT_CODES)


class TestOneSource:
    @pytest.mark.parametrize(("code", "override"), CASES, ids=[c for c, _ in CASES])
    def test_the_engine_blocker_is_the_first_of_the_list(self, code, override):
        request = make_input(**override)
        output = eng.evaluate(request)

        assert output.readiness_state is eng.ReadinessState.BLOCKED
        assert output.blockers[0].blocker_type is eng.BlockerType.READINESS_INPUT_UNAVAILABLE
        assert output.blockers[0].attribution == _found(request)[0][1]


class TestShadowCodes:
    def test_codes_for_a_request_first_and_all(self):
        request = make_input(**dict(CASES[0][1]), policy=CASES[-1][1]["policy"])

        field = dr_shadow.unavailable_input_field(request)

        assert field == {
            "first": eng.INPUT_LEDGER_UNREADABLE,
            "all": [eng.INPUT_LEDGER_UNREADABLE, eng.INPUT_TAU_UNCALIBRATED],
        }

    def test_nothing_unavailable_is_named_as_empty(self):
        assert dr_shadow.unavailable_input_field(make_input()) == {"first": None, "all": []}

    def test_an_unknown_condition_is_unclassified_and_carries_no_text(self, monkeypatch):
        monkeypatch.setattr(
            eng,
            "unavailable_inputs",
            lambda **_kw: (
                ("NEW_CONDITION", "some engine sentence"),
                (eng.INPUT_TAU_UNCALIBRATED, "x"),
            ),
        )

        field = dr_shadow.unavailable_input_field(make_input())

        assert field == {
            "first": dr_shadow.UNAVAILABLE_INPUT_UNCLASSIFIED,
            "all": [dr_shadow.UNAVAILABLE_INPUT_UNCLASSIFIED, eng.INPUT_TAU_UNCALIBRATED],
        }
        assert "sentence" not in str(field)

    def test_a_failing_read_is_a_code_not_a_raise(self, monkeypatch):
        def _boom(**_kw):
            raise RuntimeError("engine down")

        monkeypatch.setattr(eng, "unavailable_inputs", _boom)

        assert dr_shadow.unavailable_input_field(make_input()) == {"error": "RuntimeError"}
