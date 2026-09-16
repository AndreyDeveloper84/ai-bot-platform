"""The barrier: RECOMMEND does not cross the package boundary while the flag is off.

Two nodes, and both are required. The negative one — a RECOMMEND is refused —
proves nothing on its own: it would pass just as happily against an input that
never reaches RECOMMEND at all, and a barrier tested that way is an empty
quantifier. So the positive node runs first and on the same fixtures: this input
does produce a RECOMMEND, and ASK and BLOCK go through untouched.

The third thing being guarded is the one easiest to lose later: "off" and
"never configured" resolve to the same outcome and must stay **different
answers** in the audit. A fail-closed value that hides where it came from turns
a missing wire into a decision nobody made.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import decision as dec
from apps.orchestrator.decision_readiness import engine as eng
from apps.orchestrator.decision_readiness import release as rel
from apps.orchestrator.decision_readiness.safety_input import SafetyResult
from apps.orchestrator.decision_readiness.shadow import FlagSource
from apps.orchestrator.decision_readiness.tests.conftest import REVISION, make_input


def _project(**overrides: object) -> dec.StructuredDecision:
    return dec.project(eng.evaluate(make_input(**overrides)), state_revision=REVISION)


@pytest.fixture(autouse=True)
def _unset_release_flag(settings) -> None:
    """Every test starts from the code default: the key does not exist.

    Set explicitly rather than assumed, because a leaked value from another
    module would make the barrier look like it was holding when it was merely
    unconfigured — or the reverse.
    """

    if hasattr(settings, rel.RELEASE_SETTING):
        delattr(settings, rel.RELEASE_SETTING)


# --- the positive node: RECOMMEND is reachable here ---------------------------


def test_this_input_does_reach_recommend() -> None:
    """Positive control. Without it, "RECOMMEND is refused" is vacuous."""

    assert _project().decision_type is dec.DecisionType.RECOMMEND


# --- the negative node: and it does not get out -------------------------------


def test_recommend_does_not_cross_the_boundary_while_the_flag_is_off() -> None:
    decision = _project()

    with pytest.raises(rel.RecommendWithheld) as caught:
        rel.permit_outbound(decision)

    assert caught.value.decision is decision
    assert caught.value.flag.value is False
    assert caught.value.flag.source is FlagSource.READ_DEFAULT


def test_the_refusal_names_the_setting_and_the_decision() -> None:
    """A withheld recommendation that cannot be traced is an outage, not a barrier."""

    decision = _project()

    with pytest.raises(rel.RecommendWithheld) as caught:
        rel.permit_outbound(decision)

    message = str(caught.value)
    assert rel.RELEASE_SETTING in message
    assert decision.decision_id in message


# --- what the barrier is not about --------------------------------------------


def test_ask_passes_untouched() -> None:
    decision = _project(evidence=())

    assert decision.decision_type is dec.DecisionType.ASK
    assert rel.permit_outbound(decision) is decision


def test_block_passes_untouched() -> None:
    decision = _project(safety=SafetyResult.not_evaluated())

    assert decision.decision_type is dec.DecisionType.BLOCK
    assert rel.permit_outbound(decision) is decision


# --- the flag, and the three different "no"s ----------------------------------


def test_recommend_passes_once_the_flag_is_on(settings) -> None:
    settings.DRE_RECOMMEND_RELEASE_ENABLED = "true"
    decision = _project()

    assert rel.permit_outbound(decision) is decision


def test_a_deliberate_off_is_a_decision_and_says_so(settings) -> None:
    settings.DRE_RECOMMEND_RELEASE_ENABLED = "false"

    with pytest.raises(rel.RecommendWithheld) as caught:
        rel.permit_outbound(_project())

    assert caught.value.flag.source is FlagSource.SETTINGS
    assert caught.value.flag.configured is True


def test_an_unreadable_flag_leaves_the_barrier_standing(settings) -> None:
    """`bool("maybe")` is `True`. Coercing here would open the barrier by typo."""

    settings.DRE_RECOMMEND_RELEASE_ENABLED = "maybe"

    with pytest.raises(rel.RecommendWithheld) as caught:
        rel.permit_outbound(_project())

    assert caught.value.flag.source is FlagSource.MALFORMED
    assert caught.value.flag.configured is False
