"""The canonical safety verdict — Lane A slice A-1.

Three things are worth more than the mapping table here, and they are the
three these tests spend their length on.

**What is unreachable stays named.** Owner §126 forbids inventing rules to
fill an enum, and requires the emptiness to be written down. A test that only
checked the four reachable rows would go green the day somebody quietly wires
a fifth — and the note saying "0 rules produce this" would become a lie
nobody edited.

**"Nothing fired" must not equal "nothing ran".** That is the fourth of the
four ways `pre_check` answers «можно» without having answered. It is the one
that survives a broken pattern set silently, and the reason ``triggered``
exists at all.

**The policy version has to move by itself.** «It is a digest of the pattern
set» is a claim about the code; «a pattern was added and the version changed»
is an observation about behaviour. Only the second one survives a refactor.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.orchestrator.decision_readiness.safety_input import SafetyResult, SafetyState
from apps.orchestrator.safety import pre_check as pre_check_mod
from apps.orchestrator.safety.assessment import (
    UNREACHABLE_TODAY,
    HandoffRequirement,
    SafetyAssessment,
    assess,
    policy_version,
    reset_policy_version_cache,
    to_readiness_input,
)
from apps.orchestrator.safety.pre_check import SafetyVerdict

_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_caches():
    reset_policy_version_cache()
    pre_check_mod.reset_cache()
    yield
    reset_policy_version_cache()
    pre_check_mod.reset_cache()


def _result(verdict: SafetyVerdict, matched: list[str] | None = None):
    return pre_check_mod.SafetyResult(
        verdict=verdict, matched_patterns=matched if matched is not None else ["x"]
    )


class TestTheTwoStopsAreDifferentPromises:
    """Owner §127: a crisis refusal and a policy refusal are not one thing."""

    def test_a_crisis_requires_a_person(self):
        a = assess(_result(SafetyVerdict.HANDOFF), state_revision=1, now=_NOW)

        assert a.state is SafetyState.STOP
        assert a.handoff is HandoffRequirement.REQUIRED

    def test_an_ordinary_refusal_does_not(self):
        a = assess(_result(SafetyVerdict.BLOCK), state_revision=1, now=_NOW)

        assert a.state is SafetyState.STOP
        assert a.handoff is HandoffRequirement.NONE

    def test_the_state_alone_cannot_tell_them_apart(self):
        """The reason ``handoff`` has to exist as its own field.

        Both are ``STOP``. A consumer reading only the state sees one
        situation where there are two, and would answer a person in crisis
        with a policy line.
        """
        crisis = assess(_result(SafetyVerdict.HANDOFF), state_revision=1, now=_NOW)
        refusal = assess(_result(SafetyVerdict.BLOCK), state_revision=1, now=_NOW)

        assert crisis.state == refusal.state
        assert crisis.handoff != refusal.handoff


class TestTheHandoffPromiseIsLostAtTheBoundary:
    """A named gap, not a silent one.

    ``SafetyResult`` has no field for ``handoff``, so the distinction proven
    above does not survive the projection. This is pinned rather than worked
    around: a silent loss looks like working code, and the difference between
    the two promises is exactly what §127 says must never be lost.

    **When DecisionReadiness grows the field, this class flips** — visibly,
    with its reason — and the gap paragraph in ``assessment`` goes with it.
    """

    def test_the_consumer_type_has_nowhere_to_put_it(self):
        assert "handoff" not in SafetyResult.__dataclass_fields__, (
            "SafetyResult grew a handoff field — carry assessment.handoff into "
            "to_readiness_input, make sure it enters digest_fields (two different "
            "promises must not share an idempotency key), and rewrite this class "
            "to assert the promise survives"
        )

    def test_the_promise_is_gone_even_though_the_cause_survives(self):
        """The precise shape of the loss, which is narrower than it first looks.

        A first reading says «two different promises collide into one record».
        That is NOT what happens, and the difference matters enough to pin:
        ``rule_id`` carries the bucket, so a crisis and a refusal DO arrive
        distinguishable and DO digest differently.

        What is lost is the promise itself. ``rule_id`` is evidence about the
        CAUSE and is read by someone who knows the rule catalogue;
        ``handoff`` is a statement about the NEXT STEP and has to be readable
        without it. A consumer deciding "do I call a human" would have to
        parse rule identifiers to find out — which is exactly the coupling
        §127's separate field exists to remove.

        So the cost is not collision. It is that the answer is only reachable
        by knowing things the consumer should not need to know.
        """
        crisis = to_readiness_input(
            assess(_result(SafetyVerdict.HANDOFF), state_revision=1, now=_NOW)
        )
        refusal = to_readiness_input(
            assess(_result(SafetyVerdict.BLOCK), state_revision=1, now=_NOW)
        )

        assert crisis.state is refusal.state is SafetyState.STOP
        # They are separable — through the cause, not the promise.
        assert crisis.rule_id != refusal.rule_id
        assert crisis.digest_fields() != refusal.digest_fields()
        # And there is no field that answers "call a human?" directly.
        assert not hasattr(crisis, "handoff")

    def test_the_separation_holds_by_accident_and_would_stop_holding(self):
        """Named limit on the line above.

        The digests differ only because the two promises happen to come from
        different buckets today. Two verdicts sharing a bucket but carrying
        different promises would digest alike — and nothing in the contract
        forbids that, because the contract has no notion of a promise here.

        Simulated rather than asserted about the real mapping: the point is
        what the TYPE permits, not what today's rules happen to do.
        """
        same_bucket_crisis = SafetyAssessment(
            state=SafetyState.STOP,
            handoff=HandoffRequirement.REQUIRED,
            evaluated_at_revision=1,
            evaluated_at=_NOW,
            source="pre_check",
            triggered=True,
            rule_id="pre_check.regex:block",
        )
        same_bucket_refusal = SafetyAssessment(
            state=SafetyState.STOP,
            handoff=HandoffRequirement.NONE,
            evaluated_at_revision=1,
            evaluated_at=_NOW,
            source="pre_check",
            triggered=True,
            rule_id="pre_check.regex:block",
        )

        assert same_bucket_crisis.handoff != same_bucket_refusal.handoff
        assert (
            to_readiness_input(same_bucket_crisis).digest_fields()
            == to_readiness_input(same_bucket_refusal).digest_fields()
        ), "if this ever stops being true, the boundary gained a promise field"


class TestNothingFiredIsNotNothingRan:
    def test_a_clean_message_records_that_rules_ran_and_found_nothing(self):
        a = assess(_result(SafetyVerdict.ALLOW, matched=[]), state_revision=1, now=_NOW)

        assert a.state is SafetyState.NORMAL
        assert a.triggered is False
        assert a.rule_id is None

    def test_a_match_records_which_bucket_fired(self):
        a = assess(_result(SafetyVerdict.CLARIFY, matched=["p1"]), state_revision=1, now=_NOW)

        assert a.triggered is True
        assert a.rule_id == "pre_check.regex:clarify"

    def test_the_bucket_id_does_not_pretend_to_name_a_rule(self):
        """Named limit: individual patterns have no identifiers upstream.

        An id that looked like a per-rule address would point at nothing, and
        whoever followed it would find that out late.
        """
        a = assess(_result(SafetyVerdict.BLOCK, matched=["p1", "p2"]), state_revision=1, now=_NOW)

        assert a.rule_id is not None
        assert a.rule_id.startswith("pre_check.regex:")
        assert "p1" not in a.rule_id and "p2" not in a.rule_id


class TestTheVerdictCarriesEnoughToBeCheckedLater:
    """A-3 has to prove a reset was an ACTION. That needs a record with a time."""

    def test_it_says_when_and_by_what(self):
        a = assess(_result(SafetyVerdict.ALLOW, matched=[]), state_revision=7, now=_NOW)

        assert a.evaluated_at_revision == 7
        assert a.evaluated_at == _NOW
        assert a.source == "pre_check"

    def test_a_naive_timestamp_is_refused(self):
        """§123's TTL is two hours of wall clock; a naive stamp cannot be
        compared across a deployment boundary."""
        with pytest.raises(ValueError, match="timezone-aware"):
            SafetyAssessment(
                state=SafetyState.NORMAL,
                handoff=HandoffRequirement.NONE,
                evaluated_at_revision=1,
                evaluated_at=datetime(2026, 9, 11, 12, 0),
                source="pre_check",
                triggered=False,
            )

    def test_an_absent_evaluation_cannot_be_dressed_as_one(self):
        """``UNKNOWN`` means nobody ran. Letting it carry a timestamp and a
        revision would make an absence look like an answer — the silent
        default §11.4 forbids, wearing provenance."""
        with pytest.raises(ValueError, match="did not run"):
            SafetyAssessment(
                state=SafetyState.UNKNOWN,
                handoff=HandoffRequirement.NONE,
                evaluated_at_revision=1,
                evaluated_at=_NOW,
                source="pre_check",
                triggered=False,
            )

    def test_no_raw_patterns_travel_with_the_verdict(self):
        """A-2 persists this into the conversation. Regexes have no business
        living there, and the smaller the record the less there is to leak."""
        a = assess(
            _result(SafetyVerdict.HANDOFF, matched=[r"\bубью\s+себя\b"]),
            state_revision=1,
            now=_NOW,
        )

        blob = " ".join(str(getattr(a, f)) for f in a.__slots__)
        assert blob.strip(), "the assessment rendered to nothing — nothing was searched"
        assert "pre_check.regex:handoff" in blob, "the blob does not carry this verdict"
        assert "убью" not in blob


class TestWhatIsUnreachableStaysNamed:
    """Owner §126, and the same discipline applied where nobody pointed."""

    def test_no_mapping_row_produces_caution(self):
        from apps.orchestrator.safety.assessment import _MAPPING

        assert _MAPPING, "the mapping is empty — nothing below proves anything"
        produced = {state for state, _ in _MAPPING.values()}
        assert SafetyState.CAUTION not in produced, (
            "a rule now produces CAUTION — remove it from UNREACHABLE_TODAY, "
            "and make sure it is a real controlled rule with provenance and "
            "version rather than one written to fill the enum (§126)"
        )

    def test_no_mapping_row_produces_recommended(self):
        from apps.orchestrator.safety.assessment import _MAPPING

        produced = {handoff for _, handoff in _MAPPING.values()}
        assert HandoffRequirement.RECOMMENDED not in produced, (
            "a rule now produces RECOMMENDED — update UNREACHABLE_TODAY"
        )

    def test_both_emptinesses_are_written_down(self):
        """The note is the deliverable §126 asks for.

        Without it the next reader sees four values in an enum and concludes
        the system distinguishes four cases.
        """
        note = " ".join(UNREACHABLE_TODAY)

        assert "CAUTION" in note
        assert "RECOMMENDED" in note
        assert "0 rules" in note

    def test_every_producer_verdict_has_a_row(self):
        """A verdict with no row would raise KeyError on a live turn — the
        safety path is the wrong place to find out a mapping is partial."""
        from apps.orchestrator.safety.assessment import _MAPPING

        assert set(_MAPPING) == set(SafetyVerdict)


class TestThePolicyVersionMovesByItself:
    """«It is a digest of the pattern set» is a claim; this is the observation."""

    def test_adding_a_pattern_changes_the_version(self, settings):
        before = policy_version()

        settings.SAFETY_PATTERNS = {SafetyVerdict.BLOCK.value: [r"\bновый-шаблон\b"]}
        reset_policy_version_cache()
        after = policy_version()

        assert before != after, (
            "an operator extended the policy and the version did not move — a "
            "stored verdict would name a policy it was not produced by"
        )

    def test_the_same_pattern_set_gives_the_same_version(self, settings):
        settings.SAFETY_PATTERNS = {SafetyVerdict.BLOCK.value: [r"\bодин\b"]}
        reset_policy_version_cache()
        first = policy_version()
        reset_policy_version_cache()
        second = policy_version()

        assert first == second, "the version is not stable for one policy"

    def test_the_version_reaches_the_consumer(self):
        projected = to_readiness_input(
            assess(_result(SafetyVerdict.ALLOW, matched=[]), state_revision=1, now=_NOW)
        )

        assert projected.policy_version
        assert projected.policy_version.startswith("pre_check-")


class TestAnUnmappableVerdictFailsLoudly:
    """The defence that would look like caution and behave like a lie.

    A future ``except`` inside :func:`assess` — or a
    ``_MAPPING.get(verdict, something)`` — would turn "we do not know how to
    map this" into "nobody evaluated". That is worse than it sounds, because
    the consumer's own guard cannot catch it: ``UNKNOWN`` with no revision is
    its **legal** pair, so a swallowed error arrives wearing the exact shape
    of an honest absence.

    Today the mapping is a bare subscript and raises. This pins that, so the
    day somebody makes it "safer" the test says what the safety cost is.
    """

    def test_it_raises_instead_of_manufacturing_an_absence(self):
        class _UnknownVerdict:
            value = "invented"

        class _Result:
            verdict = _UnknownVerdict()
            matched_patterns = ["p"]

        with pytest.raises(KeyError):
            assess(_Result(), state_revision=1, now=_NOW)

    def test_the_mapping_is_not_a_lookup_with_a_fallback(self):
        """Reading the call site, not the behaviour.

        ``_MAPPING[verdict]`` and ``_MAPPING.get(verdict, X)`` behave
        identically on every input the enum can produce — the difference only
        shows on the input that should never arrive. So it is asserted on the
        source, where the difference is visible.
        """
        import inspect

        source = inspect.getsource(assess)

        assert "_MAPPING[" in source, "the mapping stopped being a strict lookup"
        assert "_MAPPING.get(" not in source, (
            "a fallback appeared in the mapping — an unmappable verdict would "
            "become a manufactured verdict instead of an error"
        )
