"""Decision Policy v0 в тени (DRF-1904, срез 6.2 окна «Мозг»).

Условия, каждое своим тестом:
* safety STOP / handoff REQUIRED → ``SAFETY_BOUNDARY`` — ``TestSafetyExclusion``;
* блок входа движка → ``POLICY_INPUT_UNAVAILABLE``, а не ``INSUFFICIENT_CONTEXT`` —
  ``TestInputUnavailableIsNotInsufficientContext``;
* сигнал модели не меняет исход — ``TestModelSignalsDoNotDecide``;
* в каталог до таксономии уходит только ``SAFETY_BOUNDARY`` — ``TestCatalogWall``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator import decision_policy as dp
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyState

INPUT_UNAVAILABLE = ("STATE_BLOCKED", "BLOCK_READINESS_INPUT_UNAVAILABLE")


def _evidence(safety_state="normal", reason_codes=INPUT_UNAVAILABLE, **extra):
    return SimpleNamespace(
        safety={"state": safety_state},
        reason_codes=tuple(reason_codes),
        readiness_state="blocked",
        allow_recommend=False,
        model_signals_rejected=(),
        measures={},
        **extra,
    )


class TestSafetyExclusion:
    def test_stop_is_a_safety_boundary(self):
        verdict = dp.decide(_evidence(SafetyState.STOP.value), handoff=Handoff.REQUIRED)

        assert verdict.result_status is dp.PolicyStatus.SAFETY_BOUNDARY
        assert verdict.reason_codes == (dp.POLICY_SAFETY_STOP, dp.POLICY_HANDOFF_REQUIRED)
        assert verdict.catalog_writable is True

    def test_required_handoff_alone_is_a_boundary(self):
        verdict = dp.decide(_evidence("caution", reason_codes=()), handoff=Handoff.REQUIRED)

        assert verdict.result_status is dp.PolicyStatus.SAFETY_BOUNDARY
        assert verdict.reason_codes == (dp.POLICY_HANDOFF_REQUIRED,)

    def test_boundary_wins_over_an_unavailable_engine_input(self):
        verdict = dp.decide(_evidence("stop"), handoff=Handoff.NONE)

        assert verdict.result_status is dp.PolicyStatus.SAFETY_BOUNDARY

    @pytest.mark.parametrize("state", ["unknown", None, "", "STOP!", "normal?"])
    def test_absent_or_unreadable_verdict_is_not_a_pass(self, state):
        """§23: отсутствие данных безопасности не превращается в подтверждение."""
        verdict = dp.decide(_evidence(state, reason_codes=()))

        assert verdict.result_status is dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE
        assert verdict.reason_codes == (dp.POLICY_SAFETY_VERDICT_UNAVAILABLE,)

    def test_clarify_waits_without_a_boundary(self):
        verdict = dp.decide(_evidence("clarify", reason_codes=()), handoff=Handoff.RECOMMENDED)

        assert verdict.result_status is dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING
        assert verdict.catalog_writable is False


class TestInputUnavailableIsNotInsufficientContext:
    def test_todays_engine_block_is_policy_input_unavailable(self):
        verdict = dp.decide(_evidence("normal"), handoff=Handoff.NONE)

        assert verdict.result_status is dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE
        assert verdict.reason_codes == (dp.POLICY_READINESS_INPUT_UNAVAILABLE,)
        assert verdict.facts_used == ("safety.state", "engine.reason_codes")
        assert verdict.catalog_writable is False

    @pytest.mark.parametrize("state", ["normal", "caution", "not_applicable", "clarify", "unknown"])
    def test_v0_never_produces_insufficient_context(self, state):
        for codes in (INPUT_UNAVAILABLE, (), ("STATE_NEEDS_REQUIRED_CONTEXT",)):
            assert dp.decide(_evidence(state, reason_codes=codes)).result_status.value != (
                "INSUFFICIENT_CONTEXT"
            )

    @pytest.mark.parametrize("state", ["normal", "caution", "not_applicable"])
    def test_answered_safety_and_available_input_is_a_named_gap(self, state):
        verdict = dp.decide(_evidence(state, reason_codes=("STATE_READY",)))

        assert verdict.result_status is dp.PolicyStatus.NBA_SELECTION_PENDING_TAXONOMY
        assert verdict.reason_codes == (dp.POLICY_TAXONOMY_NOT_APPROVED,)
        assert verdict.decision_policy_version == dp.DECISION_POLICY_VERSION


class TestModelSignalsDoNotDecide:
    @pytest.mark.parametrize("state", ["normal", "stop", "clarify", "unknown"])
    def test_rejected_model_signals_and_measures_change_nothing(self, state):
        plain = dp.decide(_evidence(state))
        noisy = SimpleNamespace(
            **{
                **vars(_evidence(state)),
                "model_signals_rejected": ({"signal": "safety_cleared", "reason": "model"},),
                "measures": {"completeness": 1.0, "conflicts": 0},
                "allow_recommend": True,
                "readiness_state": "ready",
            }
        )

        assert dp.decide(noisy) == plain


class TestCatalogWall:
    def test_only_safety_boundary_passes(self):
        assert dp.assert_catalog_writable(dp.PolicyStatus.SAFETY_BOUNDARY) == "SAFETY_BOUNDARY"
        assert dp.assert_catalog_writable("SAFETY_BOUNDARY") == "SAFETY_BOUNDARY"

    @pytest.mark.parametrize(
        "status",
        [
            dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE,
            dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING,
            dp.PolicyStatus.NBA_SELECTION_PENDING_TAXONOMY,
            "POLICY_INPUT_UNAVAILABLE",
            "INSUFFICIENT_CONTEXT",
            "CLEAR_PRIMARY",
            None,
        ],
    )
    def test_everything_else_is_refused(self, status):
        with pytest.raises(dp.NotCatalogWritable):
            dp.assert_catalog_writable(status)

    def test_shadow_statuses_cannot_be_mistaken_for_contract_ones(self):
        shadow_only = {s.value for s in dp.PolicyStatus} - {"SAFETY_BOUNDARY"}

        assert shadow_only == {
            "POLICY_INPUT_UNAVAILABLE",
            "SAFETY_CLARIFICATION_PENDING",
            "NBA_SELECTION_PENDING_TAXONOMY",
        }
        assert not shadow_only & dp.CONTRACT_RESULT_STATUSES
        assert dp.CATALOG_WRITABLE_BEFORE_TAXONOMY <= dp.CONTRACT_RESULT_STATUSES
