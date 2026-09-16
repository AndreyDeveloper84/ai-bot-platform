"""The barrier: a RECOMMEND does not leave this package until the contour passes.

### What this is not

Not a filter over a payload. `project()` already separates the outcomes
correctly — `ASK` carries `PromptSemantics` and options, and a `RECOMMEND`
carries **no content at all**: an id, a revision, reason codes and the state.
There is nothing in a RECOMMEND to redact. What is missing is a *holder*.

### What is missing, measured

On `dev` `8bf99f7c` four readers of `allow_recommend` live outside this package
(`dr_shadow.py` and three test modules) and every one of them **records**; not
one **refuses**. `dr_shadow.py:249` goes further and already computes the
violation — `path_recommended_without_readiness = cards_shown > 0 and not
allow_recommend`, commented «путь показал мастеров там, где движок не был вправе
рекомендовать» — and writes it to the shadow line. The violation is observed and
permitted. This module is the refusal that observation implies.

### Why here, and why now

This package's `__init__` says slice 1 is "the vertical without consumers", and
that consumers migrate one at a time, "each old LLM path is then blocked by a
test rather than merely left uncalled". The absence of a holder is the declared
shape of slice 1, not a defect found in it. This is that block, placed **before**
the first consumer instead of after — a consumer that imports `project` from the
package surface finds the barrier on the same surface.

### Why a flag of its own, and not the safety gate

Releasing RECOMMEND is a property of the **contour**: the owner's S1/F0
acceptance suite either passes or it does not, once. `safety.gate.evaluate_inbound`
is the per-turn verdict about **one inbound message**, and it deliberately lets
`CLARIFY` and `ALLOW` through (`allowed=False` only on `HANDOFF` and `BLOCK`).
Hanging the barrier on `SafetyGateOutcome.allowed` would lift it for every safe
message. So the release is its own setting, off until somebody turns it on, and
`read_default` stays distinguishable from `malformed` in the audit even though
both resolve to the same thing: the barrier stands.

### Why it raises rather than returning something

`project()` raises when the §5 output invariant breaks, "to keep a broken
invariant loud instead of turning it into a silently empty turn". A barrier that
returned `None` would make a withheld recommendation indistinguishable from an
empty turn; one that rewrote RECOMMEND into ASK would invent a question the
policy never chose. It refuses out loud, naming which flag closed it and where
that flag's value came from.
"""

from __future__ import annotations

from apps.orchestrator.decision_readiness.decision import DecisionType, StructuredDecision
from apps.orchestrator.decision_readiness.shadow import FlagReading, read_flag

#: The setting that releases RECOMMEND to consumers. Absent by default and off
#: by default, in that order — the same shape as `SHADOW_SETTING`, for the same
#: reason: a package that released itself by being installed would be acting on
#: a contour nobody agreed to release.
RELEASE_SETTING = "DRE_RECOMMEND_RELEASE_ENABLED"


class RecommendWithheld(Exception):
    """A RECOMMEND reached the package boundary while the release flag was off.

    Carries both the decision and the flag reading, because "why was this
    withheld" has two different answers — nobody has set the flag, or somebody
    set it to something unreadable — and only the first of those is a decision.
    """

    def __init__(self, decision: StructuredDecision, flag: FlagReading) -> None:
        super().__init__(
            f"RECOMMEND withheld: {RELEASE_SETTING} resolved to off "
            f"(source={flag.source.value}); decision_id={decision.decision_id}, "
            f"readiness_state={decision.readiness_state.value}"
        )
        self.decision = decision
        self.flag = flag


def release_flag() -> FlagReading:
    """Read the release flag, and say where the answer came from.

    Never raises: reading the barrier's own flag must not be the thing that
    breaks a turn. An unreadable value resolves to off, which keeps the barrier
    standing — a flag nobody can read must not be the thing that opens one.
    """

    return read_flag(RELEASE_SETTING)


def permit_outbound(decision: StructuredDecision) -> StructuredDecision:
    """Let a decision cross the package boundary, or refuse a RECOMMEND out loud.

    `ASK` and `BLOCK` always pass. The barrier is about **acting** on the
    contour's behalf, not about asking a question or declining to act, and a
    barrier that also held back questions would stop the engine from doing the
    one thing it is already trusted to do.

    Returns the decision unchanged — the same object, not a copy — so a caller
    cannot come to depend on the barrier normalising anything on the way through.
    """

    if decision.decision_type is not DecisionType.RECOMMEND:
        return decision

    flag = release_flag()
    if flag.value:
        return decision

    raise RecommendWithheld(decision, flag)
