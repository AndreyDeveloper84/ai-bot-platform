"""`evaluate()` — the readiness state, computed. §11, §10.2, §15, §17.

This is the module the epic is named after: the point where "ask or act" stops
being the language model's decision. The model keeps everything it is good at —
understanding free language, extracting values, phrasing the question,
explaining a recommendation. It loses exactly one right: deciding whether there
is enough evidence to act.

### What makes that structural rather than aspirational

* **No LLM in the contour.** This module imports no provider, and a test walks
  the package's import graph to say so (§17.2). Z2's other half: swap the
  provider for a stand that raises, and `evaluate()` still answers.
* **No clock, no randomness, no global reads** (§17.1). Expiry is applied by the
  adapter (P2), ties break lexicographically (§13.6), and the register arrives
  in the input rather than being fetched.
* **`delegation` is not a parameter of the state function.** `f()` cannot take
  it, so `delegation=HIGH` physically cannot turn `NEEDS_REQUIRED_CONTEXT` into
  `READY` (§10.2). A check at the end can be forgotten; a parameter that was
  never accepted cannot be. Test D6 asserts the signature, not the behaviour.
* **`model_signals` reach exactly one place** — the audit's rejected list. They
  are not passed to the satisfaction function, which has no parameter for them.

### Fail-closed means refusing the question too

§15.1: the unknown is not zero, not false, not a default. Both halves matter,
and the second is the one that gets forgotten: "I don't know, so I'll ask" looks
safe and is precisely the behaviour that produced DRF-1542's five screens.

### What it answers today

With no probe wired (lane D) and no safety producer wired (lane A), a live turn
gets `BLOCKED(SAFETY_UNKNOWN)` or `BLOCKED(READINESS_INPUT_UNAVAILABLE)`. That
is the correct answer, not a stub: a placeholder returning `NORMAL` or a guessed
threshold would be fail-open in a mechanism built entirely around failing
closed. Nothing consumes this engine yet, so nothing changes for anyone.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from apps.orchestrator.decision_readiness import reason_codes as rc
from apps.orchestrator.decision_readiness.candidates import (
    CandidateProbe,
    CandidateSetSignature,
)
from apps.orchestrator.decision_readiness.evidence import ConfirmedEvidence, ModelSignal
from apps.orchestrator.decision_readiness.events import Surface
from apps.orchestrator.decision_readiness.ledger import (
    AskOutcome,
    QuestionLedger,
    ReaskConditions,
    ask_permission,
)
from apps.orchestrator.decision_readiness.policy import ControlledPolicy, Uncalibrated
from apps.orchestrator.decision_readiness.questions import (
    AskReason,
    Candidate,
    ImpactClaim,
    NextBestQuestion,
    QuestionCatalog,
    QuestionKind,
    ask_allowed,
    expected_separation_gain,
    select_next_question,
)
from apps.orchestrator.decision_readiness.required_context import (
    Mode,
    PredicateContext,
    RequiredContextSpec,
    SlotOwner,
    SlotVerdict,
    evaluate_required_context,
    unsatisfied_slots,
)
from apps.orchestrator.decision_readiness.safety_input import SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.state import ConversationState

SPEC_VERSION = "1.0.0"


class ReadinessState(str, Enum):
    """The five states of canon §8. Frozen by the owner; not to be reopened."""

    READY = "ready"
    NEEDS_DISCRIMINATION = "needs_discrimination"
    NEEDS_REQUIRED_CONTEXT = "needs_required_context"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BLOCKED = "blocked"


class Delegation(str, Enum):
    """Raised only by an explicit human act — a DELEGATE option or delegating text
    confirmed as USER_TEXT evidence (§10.1). A `ModelSignal` never raises it: the
    model is not entitled to decide that the person trusted it."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class BlockerType(str, Enum):
    SAFETY_STOP = "safety_stop"
    SAFETY_UNKNOWN = "safety_unknown"
    NO_ADMISSIBLE_CANDIDATES = "no_admissible_candidates"
    CATALOG_NOT_RECOMMENDABLE = "catalog_not_recommendable"
    READINESS_INPUT_UNAVAILABLE = "readiness_input_unavailable"
    ASK_BUDGET_EXHAUSTED = "ask_budget_exhausted"


class NeedKind(str, Enum):
    EXPLICIT = "explicit"
    SURPRISE_ME = "surprise_me"


@dataclass(frozen=True, slots=True)
class CurrentNeed:
    """What the person is here for.

    `SURPRISE_ME` is a distinct opening semantic (canon §8), not a way around
    thin evidence: it sets the need and changes neither `delegation` nor
    `INSUFFICIENT_EVIDENCE` (§10.1).
    """

    kind: NeedKind = NeedKind.EXPLICIT
    descriptor: str = ""


@dataclass(frozen=True, slots=True)
class Blocker:
    blocker_type: BlockerType
    attribution: str = ""


@dataclass(frozen=True, slots=True)
class Measures:
    """§9 — computed by the resolver (DRF-1533), consumed here.

    `separation` lives on the candidate signature, so only the other two are
    carried here; the output merges all three. Two copies of one number would be
    two numbers that can disagree.

    Both are `| None`, and `None` blocks. A `conflicts` that was never computed
    is not a `conflicts` of zero: §11.5 lets a non-zero value forbid `READY`, so
    reading "not computed" as zero would recommend straight over an unresolved
    refusal — the DRF-1474 defect class, where the bot promises to find the thing
    it just said it does not have.
    """

    completeness: float | None = None
    conflicts: int | None = None


@dataclass(frozen=True, slots=True)
class InputAvailability:
    """§4. Every field defaults to unavailable — absence is not permission."""

    ledger_readable: bool = False
    probe_available: bool = False
    candidates_fresh: bool = False


@dataclass(frozen=True, slots=True)
class ReadinessInput:
    """§4. Preconditions P1–P5 are the caller's; this type carries the evidence of them."""

    state_revision: int
    state: ConversationState
    mode: Mode
    safety: SafetyResult
    candidates: CandidateSetSignature
    policy: ControlledPolicy
    required_context_spec: RequiredContextSpec
    question_ledger: QuestionLedger
    availability: InputAvailability
    measures: Measures = field(default_factory=Measures)
    catalog: QuestionCatalog = field(default_factory=QuestionCatalog)
    evidence: tuple[ConfirmedEvidence, ...] = ()
    model_signals: tuple[ModelSignal, ...] = ()
    delegation: Delegation = Delegation.LOW
    current_need: CurrentNeed | None = None
    probe: CandidateProbe | None = None
    execution_required_params: frozenset[str] = field(default_factory=frozenset)
    reask_conditions: Mapping[str, ReaskConditions] = field(default_factory=dict)
    """Re-ask conditions **per question id**, defaulting to none.

    Per-question and not per-turn, because all five of §13.5's reasons are facts
    about one question: *this* answer was retracted, *this* slot's answer
    expired, *this* question's semantics changed. A single set of conditions
    applied to every question would hand a re-ask to questions nobody ever
    answered — the imprecision out of which the DRF-1542 class comes back.

    An absent entry means "no reason to ask again", which is the fail-closed
    default: `ask_permission` then suppresses the repeat.
    """
    surface: Surface = Surface.UNKNOWN
    spec_version: str = SPEC_VERSION

    @property
    def probe_usable(self) -> bool:
        """Both the flag and the object.

        A flag with no object and an object behind a degraded resolver are each
        half of the truth, and either half alone would let the engine call
        something that cannot answer.
        """

        return self.availability.probe_available and self.probe is not None


@dataclass(frozen=True, slots=True)
class ReadinessOutput:
    """§5. No numeric readiness score is exposed — canon §8 forbids it as a public
    semantic API, and `separation` stays an audit quantity."""

    readiness_state: ReadinessState
    allow_recommend: bool
    reason_codes: tuple[str, ...]
    readiness_key: str
    blockers: tuple[Blocker, ...] = ()
    next_question: NextBestQuestion | None = None
    required_context: dict[str, SlotVerdict] = field(default_factory=dict)
    measures: dict[str, float | int | None] = field(default_factory=dict)
    model_signals_rejected: tuple[dict[str, str], ...] = ()
    spec_version: str = SPEC_VERSION
    policy_version: int = 0


# --- §11: the state function. `delegation` is not among its parameters. ------


def f(
    *,
    state: ConversationState,
    evidence: tuple[ConfirmedEvidence, ...],
    candidates: CandidateSetSignature,
    safety: SafetyResult,
    spec: RequiredContextSpec,
    availability: InputAvailability,
    state_revision: int,
    mode: Mode,
    measures: Measures,
    probe_usable: bool,
    policy: ControlledPolicy,
    execution_required_params: frozenset[str],
    current_need: CurrentNeed | None,
    probe: CandidateProbe | None,
) -> tuple[ReadinessState, tuple[Blocker, ...], dict[str, SlotVerdict], list[str]]:
    """§11, in the normative order. First rule that fires decides; total.

    Note the parameter list: there is no `delegation`. That absence is the
    DELEGATION CEILING invariant (§10.2), and it is checked by a signature test
    rather than a behavioural one, because a behavioural check passes on a
    version that reads the value and then ignores it.
    """

    codes: list[str] = []

    # 1. Safety first, and hardest.
    if safety.state is SafetyState.STOP:
        codes.append(rc.SAFETY_STOP)
        return (
            ReadinessState.BLOCKED,
            (Blocker(BlockerType.SAFETY_STOP, safety.rule_id or ""),),
            {},
            codes,
        )
    if not safety.is_known:
        # Canon §6: unknown safety-critical semantics fail closed. §11.4: a
        # silent NORMAL is forbidden — not having run is UNKNOWN.
        codes.append(rc.SAFETY_UNKNOWN)
        return (
            ReadinessState.BLOCKED,
            (Blocker(BlockerType.SAFETY_UNKNOWN, "safety was not evaluated"),),
            {},
            codes,
        )

    # 2. Availability, before any arithmetic.
    unavailable = _unavailable_reason(
        availability=availability,
        probe_usable=probe_usable,
        safety=safety,
        state_revision=state_revision,
        candidates=candidates,
        measures=measures,
        policy=policy,
    )
    if unavailable is not None:
        codes.append(rc.BLOCK_READINESS_INPUT_UNAVAILABLE)
        return (
            ReadinessState.BLOCKED,
            (Blocker(BlockerType.READINESS_INPUT_UNAVAILABLE, unavailable),),
            {},
            codes,
        )

    if safety.state is SafetyState.CAUTION:
        codes.append(rc.SAFETY_CAUTION_CONSTRAINED)
    if safety.state is SafetyState.NORMAL:
        # §3 (свод владельца 11.09): «проверка применима, выполнена, значимых
        # сигналов нет» — единственное состояние, за которое можно заявить,
        # что проверка пройдена. Сравнение через `is NORMAL`, а не через
        # `not in {...}`: NOT_APPLICABLE прошёл бы отрицательный список и
        # получил бы заявление о проверке, которой не было. Именно это §3
        # запрещает, и именно на этой строке стоит сторож.
        codes.append(rc.ELIG_SAFETY_CLEARED)

    # 3. Structurally impossible to recommend. Checked before required context:
    #    asking a person to narrow a set that cannot yield a recommendation is
    #    spending their patience on a question that cannot pay out.
    eligible = candidates.recommendation_eligible_count
    assert eligible is not None  # step 2 blocked on the unknown case
    if eligible == 0:
        if candidates.visible_count > 0:
            # Canon §14 / OD §40.2 п.5: showable and bookable by explicit choice,
            # not recommendable. The silent substitution is what is forbidden.
            codes.append(rc.BLOCK_CATALOG_NOT_RECOMMENDABLE)
            blocker = Blocker(
                BlockerType.CATALOG_NOT_RECOMMENDABLE,
                f"{candidates.visible_count} visible, 0 recommendation-eligible",
            )
        else:
            codes.append(rc.BLOCK_NO_ADMISSIBLE_CANDIDATES)
            blocker = Blocker(BlockerType.NO_ADMISSIBLE_CANDIDATES, candidates.digest)
        return ReadinessState.BLOCKED, (blocker,), {}, codes

    # 3a. CLARIFY is required context with a way out (§11.1), not a block.
    predicate_ctx = PredicateContext(
        state=state,
        candidates=candidates,
        mode=mode,
        safety=safety,
        execution_required_params=execution_required_params,
    )
    verdicts = evaluate_required_context(spec, ctx=predicate_ctx, evidence=list(evidence))
    codes.extend(_required_context_codes(verdicts))

    if safety.state is SafetyState.CLARIFY:
        codes.append(rc.SAFETY_CLARIFY_REQUIRED)
        return ReadinessState.NEEDS_REQUIRED_CONTEXT, (), verdicts, codes

    # 4. Required context.
    if unsatisfied_slots(verdicts):
        return ReadinessState.NEEDS_REQUIRED_CONTEXT, (), verdicts, codes

    # 5. Need not yet grounded.
    if not _grounded_need(evidence=evidence, spec=spec, probe=probe, current_need=current_need):
        return ReadinessState.INSUFFICIENT_EVIDENCE, (), verdicts, codes

    # 6. Candidates not tellable apart, or a conflict still standing.
    conflicts = measures.conflicts
    assert conflicts is not None  # step 2 blocked on the unknown case
    if conflicts > 0:
        codes.append(rc.MEASURE_CONFLICTS_PRESENT)
        return ReadinessState.NEEDS_DISCRIMINATION, (), verdicts, codes

    separation = candidates.separation
    tau = policy.tau_separation
    if isinstance(tau, Uncalibrated) or separation is None:
        # Unreachable in practice: step 2 blocks on an uncalibrated threshold.
        # Kept because `f` must be total and because an assertion here would
        # turn a policy gap into a crash on a live turn.
        codes.append(rc.BLOCK_READINESS_INPUT_UNAVAILABLE)
        return (
            ReadinessState.BLOCKED,
            (
                Blocker(
                    BlockerType.READINESS_INPUT_UNAVAILABLE, "separation threshold uncalibrated"
                ),
            ),
            verdicts,
            codes,
        )
    if separation < tau:
        codes.append(rc.MEASURE_SEPARATION_BELOW_TAU)
        return ReadinessState.NEEDS_DISCRIMINATION, (), verdicts, codes

    # 7.
    codes.append(rc.STATE_READY)
    return ReadinessState.READY, (), verdicts, codes


def _unavailable_reason(
    *,
    availability: InputAvailability,
    probe_usable: bool,
    safety: SafetyResult,
    state_revision: int,
    candidates: CandidateSetSignature,
    measures: Measures,
    policy: ControlledPolicy,
) -> str | None:
    """§15.2's availability rows, each answering with its own name.

    One outward state, separate reasons inward: a caller that only sees
    "unavailable" cannot tell a broken register from an uncalibrated threshold,
    and the two need different people to fix them.
    """

    if not availability.ledger_readable:
        return "question ledger is not readable"
    if not probe_usable:
        return "candidate probe is unavailable"
    if not availability.candidates_fresh:
        return "candidate set is stale"
    if safety.evaluated_at_revision is None or safety.evaluated_at_revision < state_revision:
        return (
            f"safety evaluated at revision {safety.evaluated_at_revision} "
            f"but state is at {state_revision}"
        )
    if candidates.recommendation_eligible_count is None:
        # §12.3: absence of the flag is UNKNOWN, not true.
        return "recommendation_eligible is absent, which is unknown rather than yes"
    if measures.conflicts is None:
        return "conflicts was not computed, and an uncomputed conflict count is not zero"
    if isinstance(policy.tau_separation, Uncalibrated):
        # OD-DR-1: calibrate in shadow first. Until then the engine cannot tell
        # READY from NEEDS_DISCRIMINATION, and saying either would be a guess.
        return "tau_separation is uncalibrated (OD-DR-1, DRF-1519)"
    return None


def _required_context_codes(verdicts: dict[str, SlotVerdict]) -> list[str]:
    codes: list[str] = []
    for slot, slot_verdict in sorted(verdicts.items()):
        if slot_verdict is SlotVerdict.UNSATISFIED:
            codes.append(rc.slot_code(rc.REQ_CONTEXT_UNSATISFIED, slot))
        elif slot_verdict is SlotVerdict.SATISFIED_FLEXIBLE:
            codes.append(rc.slot_code(rc.REQ_CONTEXT_SATISFIED_FLEXIBLE, slot))
        elif slot_verdict is SlotVerdict.NOT_REQUIRED:
            codes.append(rc.slot_code(rc.REQ_CONTEXT_NOT_REQUIRED, slot))
    return codes


def _grounded_need(
    *,
    evidence: tuple[ConfirmedEvidence, ...],
    spec: RequiredContextSpec,
    probe: CandidateProbe | None,
    current_need: CurrentNeed | None,
) -> bool:
    """§11.2 — grounded means the admissible set actually narrowed.

    Not "the person said something". A need grounded by the presence of text
    would be grounded by the model restating that text, and `INSUFFICIENT_EVIDENCE`
    would become a matter of taste.

    `SURPRISE_ME` does not ground anything (§10.1): it is an opening semantic,
    not a licence to act on nothing.
    """

    if probe is None:
        return False
    need_slots = spec.need_slots()
    for item in evidence:
        if item.slot in need_slots and probe.narrowed_by(item):
            return True
    return False


# --- §10.2: `g`, defined completely -----------------------------------------


def g(
    state: ReadinessState,
    delegation: Delegation,
    safety: SafetyResult,
) -> tuple[bool, list[str]]:
    """§10.2, verbatim. Delegation may unlock discrimination and nothing else.

    Written as its own function so that the invariant is one readable place: the
    only route to `True` for a non-`READY` state is `NEEDS_DISCRIMINATION` with
    `delegation=HIGH` and safety plainly normal.
    """

    codes: list[str] = []
    if safety.state in {SafetyState.CLARIFY, SafetyState.STOP, SafetyState.UNKNOWN}:
        codes.append(rc.DELEG_CEILING_SAFETY)
        return False, codes
    if state is ReadinessState.READY:
        return True, codes
    if state is ReadinessState.NEEDS_DISCRIMINATION and delegation is Delegation.HIGH:
        codes.append(rc.DELEG_APPLIED_HIGH)
        return True, codes
    if delegation is Delegation.HIGH:
        if state is ReadinessState.NEEDS_REQUIRED_CONTEXT:
            codes.append(rc.DELEG_CEILING_REQUIRED_CONTEXT)
        elif state is ReadinessState.INSUFFICIENT_EVIDENCE:
            codes.append(rc.DELEG_CEILING_INSUFFICIENT_EVIDENCE)
    return False, codes


# --- §17.3: the idempotency key ---------------------------------------------


def readiness_key(request: ReadinessInput) -> str:
    """§17.3. The same input gives the same key, and the same key the same answer.

    `state_revision` is in the key, which is what makes a stale callback
    distinguishable from a current one (canon §13.4).
    """

    evidence_digest = "|".join(
        sorted(f"{e.evidence_id}:{e.slot}:{e.origin.value}" for e in request.evidence)
    )
    ledger_digest = "|".join(
        sorted(
            f"{entry.qid}:{entry.asked_at_revision}:{entry.ask_count}"
            for entry in request.question_ledger.entries
        )
    )
    payload = "\x1f".join(
        (
            request.spec_version,
            str(request.policy.policy_version),
            str(request.state_revision),
            evidence_digest,
            "|".join(request.candidates.digest_fields()),
            "|".join(request.safety.digest_fields()),
            ledger_digest,
            request.delegation.value,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- the public entry point --------------------------------------------------


def evaluate(request: ReadinessInput) -> ReadinessOutput:
    """The whole decision, in one pure function.

    No LLM call, no clock, no randomness, no global state. Given the same
    `ReadinessInput` it returns a byte-identical `ReadinessOutput`, including the
    chosen `question_id` and the order of `reason_codes` — the property DRF-1519
    named as the proof that a model's self-assessment has actually been replaced.
    """

    state, blockers, verdicts, codes = f(
        state=request.state,
        evidence=request.evidence,
        candidates=request.candidates,
        safety=request.safety,
        spec=request.required_context_spec,
        availability=request.availability,
        state_revision=request.state_revision,
        mode=request.mode,
        measures=request.measures,
        probe_usable=request.probe_usable,
        policy=request.policy,
        execution_required_params=request.execution_required_params,
        current_need=request.current_need,
        probe=request.probe,
    )

    allow_recommend, delegation_codes = g(state, request.delegation, request.safety)
    codes.extend(delegation_codes)

    rejected = _rejected_signals(request, verdicts)
    if rejected:
        codes.append(rc.EVID_MODEL_SIGNAL_NOT_EVIDENCE)

    question: NextBestQuestion | None = None
    if state in {
        ReadinessState.NEEDS_REQUIRED_CONTEXT,
        ReadinessState.NEEDS_DISCRIMINATION,
        ReadinessState.INSUFFICIENT_EVIDENCE,
    }:
        question, question_codes, budget_blocker = _next_question(request, state, verdicts)
        codes.extend(question_codes)
        if budget_blocker is not None:
            state = ReadinessState.BLOCKED
            blockers = (budget_blocker,)
            question = None

    codes.append(_state_code(state))

    return ReadinessOutput(
        readiness_state=state,
        allow_recommend=allow_recommend,
        reason_codes=rc.canonical(codes),
        readiness_key=readiness_key(request),
        blockers=blockers,
        next_question=question,
        required_context=verdicts,
        measures={
            "separation": request.candidates.separation,
            "completeness": request.measures.completeness,
            "conflicts": request.measures.conflicts,
        },
        model_signals_rejected=rejected,
        spec_version=request.spec_version,
        policy_version=request.policy.policy_version,
    )


_STATE_CODES: dict[ReadinessState, str] = {
    ReadinessState.READY: rc.STATE_READY,
    ReadinessState.NEEDS_DISCRIMINATION: rc.STATE_NEEDS_DISCRIMINATION,
    ReadinessState.NEEDS_REQUIRED_CONTEXT: rc.STATE_NEEDS_REQUIRED_CONTEXT,
    ReadinessState.INSUFFICIENT_EVIDENCE: rc.STATE_INSUFFICIENT_EVIDENCE,
    ReadinessState.BLOCKED: rc.STATE_BLOCKED,
}


def _state_code(state: ReadinessState) -> str:
    return _STATE_CODES[state]


def _rejected_signals(
    request: ReadinessInput, verdicts: dict[str, SlotVerdict]
) -> tuple[dict[str, str], ...]:
    """§19 — what was turned down must be as visible as what was accepted.

    Without this list a DRF-1542-class defect is indistinguishable from normal
    operation in the audit: both show a question being asked, and only this
    shows that the model had "already answered" it.
    """

    rejected = []
    for signal in request.model_signals:
        verdict = verdicts.get(signal.slot)
        if verdict is None or verdict is SlotVerdict.UNSATISFIED:
            rejected.append(
                {
                    "slot": signal.slot,
                    "extractor_id": signal.extractor_id,
                    "reason": rc.EVID_MODEL_SIGNAL_NOT_EVIDENCE,
                }
            )
    return tuple(sorted(rejected, key=lambda item: (item["slot"], item["extractor_id"])))


def _next_question(
    request: ReadinessInput,
    state: ReadinessState,
    verdicts: dict[str, SlotVerdict],
) -> tuple[NextBestQuestion | None, list[str], Blocker | None]:
    """Pick the question, or say why there will not be one.

    Three outcomes, and the third is the one the spec's output invariant leaves
    implicit: a state that requires a question, a budget that is not exhausted,
    and still nothing admissible. That happens when the policy table and the
    catalog disagree — a slot declared required whose question is missing (Z3)
    or provably changes nothing (§13.3). It is an input problem, and it is named
    as one rather than dressed up as an exhausted budget.
    """

    codes: list[str] = []
    assert request.probe is not None  # step 2 of `f` blocked on its absence

    wanted = _wanted_kinds(state)
    candidates: list[Candidate] = []
    suppressed_by_budget = False

    for entry in request.catalog.entries:
        if entry.kind not in wanted:
            continue

        decision = ask_allowed(
            entry,
            state=request.state,
            candidates=request.candidates,
            probe=request.probe,
            execution_required_params=request.execution_required_params,
            ranking_comparison_depth=request.policy.ranking_comparison_depth,
        )
        if not decision.allowed:
            if decision.suppressed_reason:
                codes.append(decision.suppressed_reason)
            continue

        permission = ask_permission(
            entry.question_id,
            ledger=request.question_ledger,
            policy=request.policy,
            conditions=request.reask_conditions.get(entry.question_id) or ReaskConditions(),
        )
        if permission.outcome is AskOutcome.BUDGET_EXHAUSTED:
            suppressed_by_budget = True
            continue
        if not permission.allowed:
            codes.append(rc.ASK_SUPPRESSED_ALREADY_ASKED)
            continue

        if decision.claim is not None:
            codes.append(_CLAIM_CODES[decision.claim])
        if permission.reason is not None and permission.reason is not AskReason.FIRST_ASK:
            # One coarse code outward (§13.5's five are frozen), the mechanism
            # inward: `REASK_ANSWER_EXPIRED` covers both a volatile slot's own
            # ttl_seconds and a session that ended, and a month from now "why are
            # we re-asking" has to be answerable.
            codes.append(_REASK_CODES[permission.reason])

        candidates.append(
            Candidate(
                entry=entry,
                decision=decision,
                ask_reason=permission.reason or AskReason.FIRST_ASK,
                ask_reason_mechanism=(
                    conditions.expiry_mechanism.value
                    if (conditions := request.reask_conditions.get(entry.question_id))
                    and conditions.expiry_mechanism
                    else None
                ),
                expected_separation_gain=expected_separation_gain(
                    entry, candidates=request.candidates, probe=request.probe
                ),
            )
        )

    question = select_next_question(candidates)
    if question is not None:
        return question, codes, None

    if suppressed_by_budget:
        codes.append(rc.BLOCK_ASK_BUDGET_EXHAUSTED)
        return (
            None,
            codes,
            Blocker(
                BlockerType.ASK_BUDGET_EXHAUSTED,
                "every admissible question is past its ask budget",
            ),
        )

    codes.append(rc.BLOCK_READINESS_INPUT_UNAVAILABLE)
    unmet = ", ".join(unsatisfied_slots(verdicts)) or "none recorded"
    return (
        None,
        codes,
        Blocker(
            BlockerType.READINESS_INPUT_UNAVAILABLE,
            f"state {state.value} requires a question but the catalog offers none "
            f"(unsatisfied slots: {unmet})",
        ),
    )


_CLAIM_CODES: dict[ImpactClaim, str] = {
    ImpactClaim.ADMISSIBILITY: rc.ASK_ALLOWED_ADMISSIBILITY,
    ImpactClaim.RANKING: rc.ASK_ALLOWED_RANKING,
    ImpactClaim.EXECUTION_PARAM: rc.ASK_ALLOWED_EXECUTION_PARAM,
}

_REASK_CODES: dict[AskReason, str] = {
    AskReason.REASK_ANSWER_RETRACTED: rc.ASK_REASK_ANSWER_RETRACTED,
    AskReason.REASK_ANSWER_EXPIRED: rc.ASK_REASK_ANSWER_EXPIRED,
    AskReason.REASK_SEMANTICS_CHANGED: rc.ASK_REASK_SEMANTICS_CHANGED,
    AskReason.REASK_CANDIDATE_SET_CHANGED: rc.ASK_REASK_CANDIDATE_SET_CHANGED,
    AskReason.REASK_SAFETY_REEVALUATION: rc.ASK_REASK_SAFETY_REEVALUATION,
}


def _wanted_kinds(state: ReadinessState) -> frozenset[QuestionKind]:
    """§11.3 — which kind of question each state calls for."""

    if state is ReadinessState.NEEDS_REQUIRED_CONTEXT:
        return frozenset({QuestionKind.SAFETY_CLARIFICATION, QuestionKind.REQUIRED_CONTEXT})
    if state is ReadinessState.NEEDS_DISCRIMINATION:
        return frozenset({QuestionKind.DISCRIMINATION})
    if state is ReadinessState.INSUFFICIENT_EVIDENCE:
        # The widest useful question, not the sharpest one: the set has not been
        # narrowed by anything yet, so there is nothing to discriminate between.
        return frozenset({QuestionKind.BROADENING})
    return frozenset()


def safety_owned_slots(spec: RequiredContextSpec) -> tuple[str, ...]:
    """The slots `delegation=HIGH` can never close (§8.1). Empty while the safety
    matrix does not exist (§11.4)."""

    return tuple(sorted(row.slot for row in spec.slots if row.owner is SlotOwner.SAFETY))
