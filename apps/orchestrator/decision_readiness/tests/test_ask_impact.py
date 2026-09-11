"""ASK IMPACT (§13.3) and the deterministic choice among admissible questions (§13.6).

Canon §8: a question is allowed only if the answer could change admissibility,
ranking, or a required execution parameter. §14 records that all five screens of
DRF-1542 fail this test — not one of those questions could have changed
anything. `test_a_question_that_changes_nothing_is_forbidden` is that case.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.decision_readiness import questions as q
from apps.orchestrator.decision_readiness.candidates import (
    CandidateSetSignature,
    HypotheticalAnswer,
)
from apps.orchestrator.decision_readiness.state import ConversationState, SlotState, SlotValue


class StubProbe:
    """A resolver stand-in. Returns whatever the test says the set would become.

    Lane D owns the real one (E7). Note what this is *not*: it is not wired into
    the engine anywhere, so on a live turn `probe_available` is false and the
    engine blocks. That is the honest state of slice 1, not a gap in it.
    """

    def __init__(self, by_value: dict[str, CandidateSetSignature]) -> None:
        self.by_value = by_value
        self.calls: list[HypotheticalAnswer] = []

    def probe(self, answer: HypotheticalAnswer) -> CandidateSetSignature:
        self.calls.append(answer)
        return self.by_value[answer.value]

    def narrowed_by(self, evidence: object) -> bool:  # pragma: no cover - unused here
        return True


def _signature(
    *,
    digest: str,
    eligible: tuple[str, ...],
    ordered: tuple[str, ...] = (),
    sep: float | None = 0.0,
) -> CandidateSetSignature:
    return CandidateSetSignature(
        digest=digest,
        visible_count=len(eligible),
        recommendation_eligible_count=len(eligible),
        eligible_ids=eligible,
        ordered_ids=ordered or eligible,
        separation=sep,
    )


def _entry(**kwargs: object) -> q.QuestionCatalogEntry:
    defaults: dict[str, object] = {
        "kind": q.QuestionKind.DISCRIMINATION,
        "target_slots": ("price_band",),
        "mode": q.MODE_CONFIRM_ONE,
        "semantics_version": 1,
        "discriminator_key": "price_band",
        "answer_domain": ("low", "high"),
    }
    defaults.update(kwargs)
    return q.QuestionCatalogEntry(**defaults)  # type: ignore[arg-type]


def _state(**slots: SlotValue) -> ConversationState:
    return ConversationState(conversation_id="conv-1", revision=2, slots=dict(slots))


BEFORE = _signature(digest="d0", eligible=("m1", "m2", "m3"))


# --- the rule ----------------------------------------------------------------


def test_a_question_that_narrows_differently_is_allowed() -> None:
    probe = StubProbe(
        {
            "low": _signature(digest="d-low", eligible=("m1",)),
            "high": _signature(digest="d-high", eligible=("m2", "m3")),
        }
    )

    decision = q.ask_allowed(_entry(), state=_state(), candidates=BEFORE, probe=probe)

    assert decision.allowed is True
    assert decision.claim is q.ImpactClaim.ADMISSIBILITY
    assert decision.evidence is not None
    assert decision.evidence.probe_digest_before == "d0"
    assert decision.evidence.probe_digests_after == ("d-low", "d-high")


def test_a_question_that_changes_nothing_is_forbidden() -> None:
    """The formal form of DRF-1542's five screens (§13.3 п.2, §14 B3)."""

    same = _signature(digest="d-same", eligible=("m1", "m2", "m3"))
    probe = StubProbe({"low": same, "high": same})

    decision = q.ask_allowed(_entry(), state=_state(), candidates=BEFORE, probe=probe)

    assert decision.allowed is False
    assert decision.claim is None
    assert decision.suppressed_reason == q.ASK_SUPPRESSED_NO_IMPACT


def test_a_flexible_slot_forbids_the_question_outright() -> None:
    """§13.3 п.1 — the person said "не важно". Asking again asks a settled question."""

    flexible = _state(price_band=SlotValue(state=SlotState.FLEXIBLE, evidence_id="c1"))
    probe = StubProbe(
        {
            "low": _signature(digest="d-low", eligible=("m1",)),
            "high": _signature(digest="d-high", eligible=("m2",)),
        }
    )

    decision = q.ask_allowed(_entry(), state=flexible, candidates=BEFORE, probe=probe)

    assert decision.allowed is False
    assert decision.suppressed_reason == q.ASK_SUPPRESSED_SLOT_FLEXIBLE
    assert probe.calls == []  # not even probed — the answer is already in hand


def test_execution_parameter_needs_no_probe() -> None:
    decision = q.ask_allowed(
        _entry(kind=q.QuestionKind.REQUIRED_CONTEXT, discriminator_key="", answer_domain=()),
        state=_state(),
        candidates=BEFORE,
        probe=StubProbe({}),
        execution_required_params=frozenset({"price_band"}),
    )

    assert decision.allowed is True
    assert decision.claim is q.ImpactClaim.EXECUTION_PARAM


def test_a_known_execution_parameter_is_not_asked_again() -> None:
    known = _state(price_band=SlotValue(state=SlotState.KNOWN, value="low", evidence_id="c1"))

    decision = q.ask_allowed(
        _entry(kind=q.QuestionKind.REQUIRED_CONTEXT, discriminator_key="", answer_domain=()),
        state=known,
        candidates=BEFORE,
        probe=StubProbe({}),
        execution_required_params=frozenset({"price_band"}),
    )

    assert decision.allowed is False


def test_safety_clarification_is_exempt_from_the_impact_rule() -> None:
    """§13.3 п.3 — narrow and explicit. Safety policy decides, not ranking."""

    same = _signature(digest="d-same", eligible=("m1",))
    probe = StubProbe({"yes": same, "no": same})

    decision = q.ask_allowed(
        _entry(
            kind=q.QuestionKind.SAFETY_CLARIFICATION,
            discriminator_key="",
            answer_domain=("yes", "no"),
        ),
        state=_state(),
        candidates=BEFORE,
        probe=probe,
    )

    assert decision.allowed is True
    assert probe.calls == []


def test_ranking_impact_is_not_claimed_when_the_depth_is_not_configured() -> None:
    """`k` is derived from the surface's card count (§16.1), not chosen here.

    Unconfigured means the ranking claim is not made — the direction that asks
    *fewer* questions, because §15.1 makes the refusal to ask the substantive
    half of failing closed. The reason is carried inward so a missing setting is
    never read back as a measured "this changes nothing".
    """

    probe = StubProbe(
        {
            "low": _signature(digest="d-low", eligible=("m1", "m2"), ordered=("m1", "m2")),
            "high": _signature(digest="d-high", eligible=("m1", "m2"), ordered=("m2", "m1")),
        }
    )

    without_depth = q.ask_allowed(_entry(), state=_state(), candidates=BEFORE, probe=probe)
    with_depth = q.ask_allowed(
        _entry(), state=_state(), candidates=BEFORE, probe=probe, ranking_comparison_depth=2
    )

    assert without_depth.allowed is False
    assert without_depth.evidence.ranking_depth_unconfigured is True
    assert with_depth.allowed is True
    assert with_depth.claim is q.ImpactClaim.RANKING


def test_a_domain_of_one_cannot_be_shown_to_change_anything() -> None:
    decision = q.ask_allowed(
        _entry(answer_domain=("low",)), state=_state(), candidates=BEFORE, probe=StubProbe({})
    )

    assert decision.allowed is False
    assert decision.suppressed_reason == q.ASK_SUPPRESSED_NO_IMPACT


# --- §13.6 selection ---------------------------------------------------------


def _allowed(entry: q.QuestionCatalogEntry, gain: float | None = None) -> q.Candidate:
    return q.Candidate(
        entry=entry,
        decision=q.AskDecision(
            allowed=True,
            claim=q.ImpactClaim.ADMISSIBILITY,
            evidence=q.ImpactEvidence(probe_digest_before="d0"),
        ),
        expected_separation_gain=gain,
    )


def test_safety_outranks_everything() -> None:
    chosen = q.select_next_question(
        [
            _allowed(
                _entry(
                    kind=q.QuestionKind.BROADENING, discriminator_key="", answer_domain=("a", "b")
                ),
                gain=0.9,
            ),
            _allowed(
                _entry(kind=q.QuestionKind.SAFETY_CLARIFICATION, discriminator_key=""), gain=0.0
            ),
        ]
    )

    assert chosen is not None
    assert chosen.kind is q.QuestionKind.SAFETY_CLARIFICATION


def test_required_context_outranks_discrimination() -> None:
    chosen = q.select_next_question(
        [
            _allowed(_entry(kind=q.QuestionKind.DISCRIMINATION), gain=0.9),
            _allowed(
                _entry(
                    kind=q.QuestionKind.REQUIRED_CONTEXT,
                    discriminator_key="",
                    target_slots=("city",),
                ),
                gain=0.0,
            ),
        ]
    )

    assert chosen is not None
    assert chosen.kind is q.QuestionKind.REQUIRED_CONTEXT


def test_within_a_kind_the_larger_gain_wins() -> None:
    small = _allowed(_entry(discriminator_key="duration"), gain=0.1)
    large = _allowed(_entry(discriminator_key="price_band"), gain=0.7)

    chosen = q.select_next_question([small, large])

    assert chosen is not None
    assert chosen.question_id == large.entry.question_id


def test_an_unmeasured_gain_does_not_win() -> None:
    """Absent is not "best". `None` sorts as zero, not as an improvement."""

    measured = _allowed(_entry(discriminator_key="price_band"), gain=0.3)
    unmeasured = _allowed(_entry(discriminator_key="duration"), gain=None)

    chosen = q.select_next_question([unmeasured, measured])

    assert chosen is not None
    assert chosen.question_id == measured.entry.question_id


def test_ties_break_on_the_shorter_domain_then_on_the_id() -> None:
    short = _allowed(_entry(discriminator_key="a", answer_domain=("x", "y")), gain=0.5)
    long_ = _allowed(_entry(discriminator_key="b", answer_domain=("x", "y", "z", "w")), gain=0.5)

    chosen = q.select_next_question([long_, short])
    assert chosen is not None
    assert chosen.question_id == short.entry.question_id

    # Same kind, same gain, same domain size -> lexicographic id, and the answer
    # must not depend on the order the candidates arrived in.
    a = _allowed(_entry(discriminator_key="a"), gain=0.5)
    b = _allowed(_entry(discriminator_key="b"), gain=0.5)
    expected = min(a.entry.question_id, b.entry.question_id)

    assert q.select_next_question([a, b]).question_id == expected  # type: ignore[union-attr]
    assert q.select_next_question([b, a]).question_id == expected  # type: ignore[union-attr]


def test_nothing_admissible_means_no_question() -> None:
    refused = q.Candidate(
        entry=_entry(),
        decision=q.AskDecision(
            allowed=False,
            claim=None,
            evidence=q.ImpactEvidence(probe_digest_before="d0"),
            suppressed_reason=q.ASK_SUPPRESSED_NO_IMPACT,
        ),
    )

    assert q.select_next_question([refused]) is None
    assert q.select_next_question([]) is None


def test_the_chosen_question_carries_its_slots_canonically_ordered() -> None:
    chosen = q.select_next_question(
        [_allowed(_entry(target_slots=("onset_context", "body_area")), gain=0.1)]
    )

    assert chosen is not None
    assert chosen.target_slots == ("body_area", "onset_context")


# --- truncation --------------------------------------------------------------


def test_escape_and_delegate_survive_truncation() -> None:
    """Canon §13.5. A list cut by priority alone drops them exactly when it is
    longest, which is when a person most needs a way out."""

    options = tuple(
        q.SemanticOption(f"o{i}", q.OptionRole.CHOICE, f"a{i}", presentation_priority=10 - i)
        for i in range(6)
    ) + (
        q.SemanticOption("esc", q.OptionRole.ESCAPE, "other", presentation_priority=-100),
        q.SemanticOption("del", q.OptionRole.DELEGATE, "choose_for_me", presentation_priority=-100),
    )

    kept = q.truncate_options(options, limit=4)

    assert len(kept) == 4
    roles = {o.role for o in kept}
    assert q.OptionRole.ESCAPE in roles
    assert q.OptionRole.DELEGATE in roles


def test_truncation_is_deterministic() -> None:
    options = tuple(
        q.SemanticOption(f"o{i}", q.OptionRole.CHOICE, f"a{i}", presentation_priority=1)
        for i in range(5)
    )

    assert q.truncate_options(options, limit=3) == q.truncate_options(options, limit=3)


def test_truncation_refuses_a_meaningless_limit() -> None:
    with pytest.raises(ValueError, match="limit must be"):
        q.truncate_options((), limit=0)


# --- expected gain -----------------------------------------------------------


def test_expected_gain_is_none_when_separation_is_not_computed() -> None:
    """Not zero. A resolver that never measured is not a catalog with no spread."""

    probe = StubProbe({"low": _signature(digest="a", eligible=("m1",), sep=None)})
    unmeasured = _signature(digest="d0", eligible=("m1", "m2"), sep=None)

    assert (
        q.expected_separation_gain(
            _entry(answer_domain=("low",)), candidates=unmeasured, probe=probe
        )
        is None
    )


def test_expected_gain_is_the_best_available_improvement() -> None:
    probe = StubProbe(
        {
            "low": _signature(digest="a", eligible=("m1",), sep=0.8),
            "high": _signature(digest="b", eligible=("m2",), sep=0.4),
        }
    )
    before = _signature(digest="d0", eligible=("m1", "m2"), sep=0.1)

    gain = q.expected_separation_gain(_entry(), candidates=before, probe=probe)

    assert gain is not None
    assert abs(gain - 0.7) < 1e-9
