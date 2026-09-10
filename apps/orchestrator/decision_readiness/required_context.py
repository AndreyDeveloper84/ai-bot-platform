"""Which slots must be settled before acting, and whether they are — §8.

Two things live here and they are deliberately separate:

* `RequiredContextSpec` — **data**, loaded per `policy_version`. Which slots
  matter, who owns them, under what condition they are required, which origins
  may close them.
* the satisfaction function `verdict()` — §8.3, the point at which EVIDENCE
  ORIGIN stops being a principle and becomes an executable line: it reads
  `ConfirmedEvidence` and does not read `ModelSignal` at all.

### The predicate language is closed on purpose

`required_when` is not a lambda. Spec §8.2 fixes a closed set of terms so the
condition can be both evaluated and **shown in the audit**. A lambda evaluates
and cannot be shown; a rule nobody can read afterwards is a rule nobody can
check. Every term below appears in §8.2; there is no eighth.

### No safety slot is invented here

The safety matrix belongs to the owner and does not exist (spec §16.2, canon §7
"Still open"). While it is empty, the spec contains no slot with
`owner = SAFETY`, and safety produces no required context — §11.4. That is why
this module ships with a genuinely empty default spec rather than a plausible
one. Inventing "reasonable" safety slots would be the same error as inventing a
threshold: indistinguishable afterwards from a decision the owner actually made.

### OD-DR-3 lives in `satisfied_by_origins`

Promoted memory does not by itself close a safety slot. That restriction is a
property of the **slot**, expressed by leaving `PROMOTED_MEMORY` out of that
slot's `satisfied_by_origins` — not a property of the evidence. A slot decides
what may satisfy it; evidence does not decide what it is good enough for.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from apps.orchestrator.decision_readiness.candidates import CandidateSetSignature
from apps.orchestrator.decision_readiness.evidence import (
    CONFIRMABLE_ORIGINS,
    ConfirmedEvidence,
    EvidenceOrigin,
)
from apps.orchestrator.decision_readiness.safety_input import SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.state import ConversationState, SlotState


class Mode(str, Enum):
    """Canon §3 — what the conversation is doing right now."""

    DISCOVERY = "discovery"
    EXECUTION = "execution"


class SlotOwner(str, Enum):
    """Spec §8.1. `SAFETY` is the one group `delegation=HIGH` can never close."""

    SAFETY = "safety"
    EXECUTION = "execution"
    CATALOG = "catalog"
    RANKING = "ranking"


class VolatilityKind(str, Enum):
    STABLE = "stable"
    VOLATILE = "volatile"


@dataclass(frozen=True, slots=True)
class Volatility:
    """How long an answer to this slot stays true.

    `STABLE` answers live to the end of the session (canon §4.1, the 2h
    inactivity TTL). `VOLATILE` answers carry their own seconds — the precedent
    is `apps/orchestrator/booking_context.py:64`, 900s for a time preference,
    because "завтра" stops being true faster than "в Пензе" does.
    """

    kind: VolatilityKind = VolatilityKind.STABLE
    ttl_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.kind is VolatilityKind.VOLATILE and not self.ttl_seconds:
            raise ValueError("VOLATILE volatility requires ttl_seconds")
        if self.kind is VolatilityKind.STABLE and self.ttl_seconds is not None:
            raise ValueError("STABLE volatility must not carry ttl_seconds")


STABLE = Volatility()


# --- §8.2: the closed predicate language -------------------------------------


@dataclass(frozen=True, slots=True)
class PredicateContext:
    """Everything a `required_when` term is allowed to read. Nothing else is in scope.

    There is no `model_signals` field, and that is the point: a condition that
    could read the model's guess would let the model decide what is required.
    """

    state: ConversationState
    candidates: CandidateSetSignature
    mode: Mode
    safety: SafetyResult
    execution_required_params: frozenset[str] = field(default_factory=frozenset)


class Predicate(ABC):
    """A term or combination from §8.2. Evaluable **and** printable."""

    @abstractmethod
    def holds(self, ctx: PredicateContext) -> bool: ...

    @abstractmethod
    def describe(self) -> str:
        """The audit's rendering. §19 requires the condition to be visible, not inferred."""


@dataclass(frozen=True, slots=True)
class SpansMoreThanOne(Predicate):
    """`candidate_set.spans_more_than_one(<field>)`"""

    candidate_field: str

    def holds(self, ctx: PredicateContext) -> bool:
        return ctx.candidates.spans_more_than_one(self.candidate_field)

    def describe(self) -> str:
        return f"candidate_set.spans_more_than_one({self.candidate_field})"


@dataclass(frozen=True, slots=True)
class CardinalityGreaterThan(Predicate):
    """`candidate_set.cardinality > N`

    Cardinality is counted over `recommendation_eligible`. When eligibility is
    unknown the engine has already blocked (§12.3) before any predicate runs, so
    an unknown here is a caller error rather than a condition to guess at.
    """

    n: int

    def holds(self, ctx: PredicateContext) -> bool:
        cardinality = ctx.candidates.cardinality
        if cardinality is None:
            raise ValueError(
                "cardinality is unknown — recommendation_eligible was absent, which "
                "§12.3 blocks on before required context is evaluated"
            )
        return cardinality > self.n

    def describe(self) -> str:
        return f"candidate_set.cardinality > {self.n}"


@dataclass(frozen=True, slots=True)
class SlotIs(Predicate):
    """`slot(<s>).state == UNKNOWN | KNOWN | FLEXIBLE`"""

    slot: str
    state: SlotState

    def holds(self, ctx: PredicateContext) -> bool:
        return ctx.state.slot(self.slot).state is self.state

    def describe(self) -> str:
        return f"slot({self.slot}).state == {self.state.value.upper()}"


@dataclass(frozen=True, slots=True)
class ModeIs(Predicate):
    """`mode == DISCOVERY | EXECUTION`"""

    mode: Mode

    def holds(self, ctx: PredicateContext) -> bool:
        return ctx.mode is self.mode

    def describe(self) -> str:
        return f"mode == {self.mode.value.upper()}"


@dataclass(frozen=True, slots=True)
class SafetyIs(Predicate):
    """`safety.state == NORMAL | CLARIFY | CAUTION | STOP`"""

    state: SafetyState

    def holds(self, ctx: PredicateContext) -> bool:
        return ctx.safety.state is self.state

    def describe(self) -> str:
        return f"safety.state == {self.state.value.upper()}"


@dataclass(frozen=True, slots=True)
class ExecutionRequires(Predicate):
    """`execution.requires(<param>)`"""

    param: str

    def holds(self, ctx: PredicateContext) -> bool:
        return self.param in ctx.execution_required_params

    def describe(self) -> str:
        return f"execution.requires({self.param})"


@dataclass(frozen=True, slots=True)
class NoRecommendableCandidates(Predicate):
    """`catalog.recommendation_eligible_count == 0`"""

    def holds(self, ctx: PredicateContext) -> bool:
        count = ctx.candidates.recommendation_eligible_count
        if count is None:
            raise ValueError(
                "recommendation_eligible is absent — §12.3 treats that as UNKNOWN and "
                "blocks, rather than letting a predicate read it as zero"
            )
        return count == 0

    def describe(self) -> str:
        return "catalog.recommendation_eligible_count == 0"


@dataclass(frozen=True, slots=True)
class And(Predicate):
    terms: tuple[Predicate, ...]

    def holds(self, ctx: PredicateContext) -> bool:
        return all(term.holds(ctx) for term in self.terms)

    def describe(self) -> str:
        return "(" + " ∧ ".join(term.describe() for term in self.terms) + ")"


@dataclass(frozen=True, slots=True)
class Or(Predicate):
    terms: tuple[Predicate, ...]

    def holds(self, ctx: PredicateContext) -> bool:
        return any(term.holds(ctx) for term in self.terms)

    def describe(self) -> str:
        return "(" + " ∨ ".join(term.describe() for term in self.terms) + ")"


@dataclass(frozen=True, slots=True)
class Not(Predicate):
    term: Predicate

    def holds(self, ctx: PredicateContext) -> bool:
        return not self.term.holds(ctx)

    def describe(self) -> str:
        return f"¬{self.term.describe()}"


@dataclass(frozen=True, slots=True)
class Always(Predicate):
    """Unconditionally required. Present because "no condition" needs a name too."""

    def holds(self, ctx: PredicateContext) -> bool:
        return True

    def describe(self) -> str:
        return "always"


# --- §8.1: the spec, as data -------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequiredSlot:
    """One row of the table. Data loaded by `policy_version`, not code."""

    slot: str
    owner: SlotOwner
    required_when: Predicate
    question_id: str
    satisfied_by_origins: frozenset[EvidenceOrigin] = field(
        default_factory=lambda: frozenset(CONFIRMABLE_ORIGINS)
    )
    flexible_allowed: bool = True
    volatility: Volatility = STABLE

    def __post_init__(self) -> None:
        rogue = self.satisfied_by_origins - CONFIRMABLE_ORIGINS
        if rogue:
            raise ValueError(
                f"slot {self.slot!r}: {sorted(o.value for o in rogue)} cannot satisfy anything. "
                "A model inference is not evidence (§3.1)."
            )
        if not self.satisfied_by_origins:
            raise ValueError(
                f"slot {self.slot!r}: satisfied_by_origins is empty — the slot could never "
                "be closed, which is a blocked conversation dressed as a policy row"
            )


@dataclass(frozen=True, slots=True)
class RequiredContextSpec:
    """The table for one `policy_version`.

    The default is genuinely empty. On `883b7539` the safety matrix does not
    exist and the two discrimination thresholds are uncalibrated (OD-DR-1), so
    an out-of-the-box table would be a set of guesses that later becomes
    indistinguishable from owner decisions.
    """

    policy_version: int
    slots: tuple[RequiredSlot, ...] = ()

    def __iter__(self) -> Iterable[RequiredSlot]:
        return iter(self.slots)

    def need_slots(self) -> frozenset[str]:
        """Slots whose evidence can ground a need (§11.2)."""

        return frozenset(
            row.slot for row in self.slots if row.owner in {SlotOwner.CATALOG, SlotOwner.EXECUTION}
        )


EMPTY_SPEC = RequiredContextSpec(policy_version=0)


# --- §8.3: the satisfaction function ----------------------------------------


class SlotVerdict(str, Enum):
    SATISFIED_KNOWN = "satisfied_known"
    SATISFIED_FLEXIBLE = "satisfied_flexible"
    UNSATISFIED = "unsatisfied"
    NOT_REQUIRED = "not_required"


def verdict(
    row: RequiredSlot,
    *,
    ctx: PredicateContext,
    evidence: Sequence[ConfirmedEvidence],
) -> SlotVerdict:
    """§8.3, and the line where EVIDENCE ORIGIN becomes executable.

    `evidence` is `ConfirmedEvidence` only. There is no `model_signals`
    parameter — a signal covering every required slot leaves every one of them
    `UNSATISFIED`, which is the required test of §3.1.

    Expiry is not checked here. Precondition P2 puts that on the input adapter
    and §17.1 forbids the engine a clock; evidence arriving here is evidence
    that survived the filter.
    """

    if not row.required_when.holds(ctx):
        return SlotVerdict.NOT_REQUIRED

    for item in evidence:
        if item.slot == row.slot and item.origin in row.satisfied_by_origins:
            return SlotVerdict.SATISFIED_KNOWN

    slot_value = ctx.state.slot(row.slot)
    if row.flexible_allowed and slot_value.state is SlotState.FLEXIBLE:
        # "Не важно" only counts when there is a record of the person saying it.
        # A FLEXIBLE slot with no evidence behind it is a flexibility somebody
        # else decided on — most plausibly the extractor — and §3.1 п.4 does not
        # let a signal close a slot by any route, including this one.
        if slot_value.evidence_id is not None:
            return SlotVerdict.SATISFIED_FLEXIBLE

    return SlotVerdict.UNSATISFIED


def evaluate_required_context(
    spec: RequiredContextSpec,
    *,
    ctx: PredicateContext,
    evidence: Sequence[ConfirmedEvidence],
) -> dict[str, SlotVerdict]:
    """Every row's verdict, keyed by slot. Ordered for determinism (§17.1)."""

    return {
        row.slot: verdict(row, ctx=ctx, evidence=evidence)
        for row in sorted(spec.slots, key=lambda r: r.slot)
    }


def unsatisfied_slots(verdicts: dict[str, SlotVerdict]) -> tuple[str, ...]:
    """Canonically sorted, so the same input always names them in the same order."""

    return tuple(sorted(slot for slot, v in verdicts.items() if v is SlotVerdict.UNSATISFIED))
