"""The Safety Engine's verdict, in the shape the rest of the system reads.

Lane A slice A-1. Today five live call sites ask `pre_check` whether an
inbound message is safe, and get back a four-valued `SafetyVerdict` that
nobody else in the system speaks. The consumer — DecisionReadiness — needs
canonical states, and it needs them attached to the revision they were
computed for. This module is the translation, and the place where the parts
`pre_check` cannot express get named rather than invented.

### Why a producer-side type and not the consumer's one directly

`decision_readiness.safety_input.SafetyResult` is consumer-shaped: it carries
exactly what the readiness algorithm reads. A producer needs more — **when**
the verdict was reached and **what reached it** — because slice A-3 has to
prove that a reset was an ACTION and not the absence of one (owner §123), and
a guard for that cannot be built on a record that does not say when it was
made.

So :class:`SafetyAssessment` is the full answer and
:func:`to_readiness_input` is the projection of it the consumer reads. Two
types for one fact is a real cost, paid deliberately: the alternative is a
consumer type that grows producer-only fields nobody downstream reads.

### The gap that WAS here, and how it closed (11.09.2026)

Owner §127: `STOP` from a crisis and `STOP` from an ordinary policy refusal
are **different promises to the person**, and must not be indistinguishable.

When this module was written, `SafetyResult` had no field for that promise, so
the distinction existed on this side of the boundary and was lost crossing it.
That was recorded here and pinned by a test rather than worked around — a
silent loss looks like working code.

`SafetyResult.handoff` landed in DecisionReadiness, the test flipped, and the
paragraph is kept as history rather than deleted: it says why the field exists
and what breaks if someone removes it.

One consequence is worth carrying forward. There is now **one** `Handoff`
enum, imported from the consumer, not a parallel one defined here. Two enums
for one fact do not stay equal — and the drift does not begin with a fourth
member, it begins when `NONE` means "not required" on one side and "not
decided yet" on the other. Same members, divergent meaning, and no test on the
member list can see it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from apps.orchestrator.decision_readiness.safety_input import (
    Handoff,
    SafetyResult,
    SafetyState,
)
from apps.orchestrator.safety.pre_check import SafetyVerdict


#: Which `pre_check` verdict becomes which canonical state and promise.
#:
#: The two `STOP` rows are the whole point of §127. Both stop the action; they
#: are different sentences to a person. A crisis reply that arrived as an
#: ordinary refusal would be the worst failure this surface has.
_MAPPING: dict[SafetyVerdict, tuple[SafetyState, Handoff]] = {
    SafetyVerdict.ALLOW: (SafetyState.NORMAL, Handoff.NONE),
    SafetyVerdict.CLARIFY: (SafetyState.CLARIFY, Handoff.NONE),
    SafetyVerdict.BLOCK: (SafetyState.STOP, Handoff.NONE),
    SafetyVerdict.HANDOFF: (SafetyState.STOP, Handoff.REQUIRED),
}

#: States and promises that the contract declares and **no rule produces**.
#:
#: `CAUTION` is owner §126, verbatim: keep it in the contract, unreachable,
#: and *«документировать явно: текущий policy set производит 0 CAUTION
#: rules»*. Fictional rules to fill the enum are forbidden — they would lie
#: with data rather than with documentation, and a state with a producer is
#: indistinguishable from a reachable one.
#:
#: `RECOMMENDED` is in this list by the SAME reasoning, applied where nobody
#: pointed. The owner was asked about `CAUTION`; the mapping above produces
#: `NONE` and `REQUIRED` and never `RECOMMENDED`. Declaring the discipline
#: only where it was requested would mean waiting to be told twice.
UNREACHABLE_TODAY: tuple[str, ...] = (
    "SafetyState.CAUTION — 0 rules produce it (owner §126)",
    "Handoff.RECOMMENDED — 0 rules produce it (same discipline)",
)

#: Names the BUCKET a verdict came from, not an individual rule.
#:
#: `pre_check` groups regexes under a verdict and gives the individual
#: patterns no identifiers, so there is nothing finer to name. A synthesised
#: per-pattern id would read like a real rule reference and point at nothing —
#: the id would promise more than the mechanism has.
_RULE_ID_PREFIX = "pre_check.regex"

_POLICY_VERSION_CACHE: dict[str, str] = {}


def policy_version() -> str:
    """A digest of the pattern set actually in force.

    Not a hand-bumped constant: operators extend safety through
    ``settings.SAFETY_PATTERNS``, and a version that only changes when someone
    remembers to change it would say a stored verdict came from a policy it
    did not. This changes by itself, which is the only way it stays true.
    """
    from apps.orchestrator.safety.pre_check import _verdict_patterns

    patterns = _verdict_patterns()
    material = "\n".join(
        f"{verdict}:{pattern}"
        for verdict in sorted(patterns)
        for pattern in sorted(patterns[verdict])
    )
    cached = _POLICY_VERSION_CACHE.get(material)
    if cached is None:
        cached = f"pre_check-{hashlib.sha256(material.encode()).hexdigest()[:12]}"
        _POLICY_VERSION_CACHE[material] = cached
    return cached


def reset_policy_version_cache() -> None:
    """Test hook — the pattern set is read from settings and tests change it."""
    _POLICY_VERSION_CACHE.clear()


@dataclass(frozen=True, slots=True)
class SafetyAssessment:
    """One safety verdict, with enough provenance to be checked later.

    ``triggered`` is the field that keeps A-3 honest. Without it, "no rule
    fired" and "no rules ran" collapse into ``NORMAL`` — which is the fourth
    of the four ways `pre_check` answers «можно» without having answered, and
    the one that survives a broken pattern set silently.

    ``evaluated_at`` and ``evaluated_at_revision`` answer *when*; ``source``
    answers *by what*. Owner §123 makes a reset an ACTION rather than the
    absence of one, and a guard for that needs a record that says when the
    verdict was made and by which evaluation — otherwise the guard is built
    on a guess.

    Raw patterns are deliberately absent. They are not personal data, but
    A-2 persists this into ``ConversationState``, and regexes have no business
    living in a conversation.
    """

    state: SafetyState
    handoff: Handoff
    evaluated_at_revision: int
    evaluated_at: datetime
    source: str
    triggered: bool
    rule_id: str | None = None
    required_slots: tuple[str, ...] = ()
    forbidden_capabilities: tuple[str, ...] = ()
    policy_version_id: str = ""

    def __post_init__(self) -> None:
        if self.evaluated_at_revision < 1:
            raise ValueError("evaluated_at_revision must be >= 1")
        if self.evaluated_at.tzinfo is None:
            # A naive timestamp cannot be compared against a TTL across a
            # deployment boundary, and §123's TTL is two hours of wall clock.
            raise ValueError("evaluated_at must be timezone-aware")
        if self.state is SafetyState.UNKNOWN:
            raise ValueError(
                "SafetyAssessment is the record of an evaluation that HAPPENED. "
                "«did not run» is SafetyResult.not_evaluated(), which is UNKNOWN "
                "and says so by name — building it here would let an absence of "
                "an answer carry a timestamp and a revision, and look like one."
            )


def assess(
    verdict_result,
    *,
    state_revision: int,
    source: str = "pre_check",
    now: datetime | None = None,
) -> SafetyAssessment:
    """Translate a `pre_check` result into the canonical verdict.

    ``verdict_result`` is :class:`apps.orchestrator.safety.pre_check.SafetyResult`
    — the producer's own type, whose name collides with the consumer's. The
    collision is not introduced here and is not this slice's to fix; the
    parameter is named for its role rather than its type so the two do not get
    confused at call sites.
    """
    verdict = verdict_result.verdict
    state, handoff = _MAPPING[verdict]
    matched = tuple(getattr(verdict_result, "matched_patterns", ()) or ())
    return SafetyAssessment(
        state=state,
        handoff=handoff,
        evaluated_at_revision=state_revision,
        evaluated_at=now or datetime.now(timezone.utc),
        source=source,
        # A fired rule is what makes NORMAL an answer rather than a default.
        triggered=bool(matched),
        rule_id=f"{_RULE_ID_PREFIX}:{verdict.value}" if matched else None,
        policy_version_id=policy_version(),
    )


def to_readiness_input(assessment: SafetyAssessment) -> SafetyResult:
    """Project the assessment onto what DecisionReadiness reads.

    ``handoff`` is passed through, not defaulted. The difference matters more
    than it looks: ``SafetyResult`` refuses a known state with no handoff, so
    forgetting the argument fails loudly — but writing ``or Handoff.NONE``
    would satisfy that check while turning "the producer did not say" back
    into "not required", one floor below where anyone looks.

    Nothing here substitutes for a missing value, and nothing here catches an
    exception. A ``try/except`` around the mapping would be the worse version
    of the same mistake: it would convert "we could not map this" into "nobody
    evaluated", and the consumer's own guard cannot catch that — ``UNKNOWN``
    with no revision is its *legal* pair, so a swallowed error arrives wearing
    the shape of an honest absence.
    """
    return SafetyResult(
        state=assessment.state,
        handoff=assessment.handoff,
        evaluated_at_revision=assessment.evaluated_at_revision,
        rule_id=assessment.rule_id,
        # Passed as-is, not `or None`. The value is always set by `assess`,
        # so the fallback covered nothing — and an `or` in this function is
        # exactly the shape a promise-defaulting bug would take. Keeping
        # the adapter free of them lets the guard be blanket instead of
        # per-field, which is one fewer thing to remember.
        policy_version=assessment.policy_version_id,
        required_slots=assessment.required_slots,
        forbidden_capabilities=assessment.forbidden_capabilities,
    )


__all__ = [
    "UNREACHABLE_TODAY",
    "Handoff",
    "SafetyAssessment",
    "assess",
    "policy_version",
    "reset_policy_version_cache",
    "to_readiness_input",
]
