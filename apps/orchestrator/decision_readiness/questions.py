"""Question identity, admissibility and choice — §13.

### `question_id` is the identity of a question, not of a string

This is the piece the lane was warned about twice, so it is worth stating
plainly: **one question asked in the chat and answered by tapping a button must
have one `question_id`.** If tapping produced a different identity from typing,
a person would answer with a tap and the text branch would ask again — which is
DRF-1542 rebuilt out of channels instead of turns.

So the id is derived (§13.2) from `kind`, the **sorted set** of target slots,
the discriminator key and `semantics_version` — and from nothing else:

* not from the wording, or a model rephrasing the same question would read as a
  new one;
* not from the option order or locale;
* not from `surface`, so an answer given in the Mini App closes the question in
  the MAX chat and the reverse (SURFACE-INDEPENDENT, §13.2);
* not from `policy_version` — moving a threshold does not buy the right to ask
  again (§18.1).

It *does* depend on the slot set: asking about `body_area + onset_context` and
asking about `body_area` alone are different questions. A narrowed question is a
different question.

### Admissibility is computed, not judged

§13.3, canon §8: a question is allowed only if the answer could change
admissibility, ranking, or a required execution parameter. `ask_allowed()`
computes that by probing (`candidates.CandidateProbe`), never by opinion. All
five screens of DRF-1542 fail this test — none of those questions could have
changed anything.

Three consequences worth naming, all from §13.3:

1. a `FLEXIBLE` slot forbids the question outright — the person already said
   "не важно" and asking again is asking a settled question;
2. if every answer in the domain leaves the set and the order identical, the
   question is forbidden even when it feels sensible;
3. `SAFETY_CLARIFICATION` is exempt, narrowly and explicitly: its admissibility
   is safety policy's to decide, not ranking's.

### The three modes are the ones the code already has

`confirm_one` / `choose_many` / `free` — `apps/orchestrator/discovery.py`
(§1.1). No fourth mechanism is introduced. The values are literals here rather
than an import because `discovery` pulls in the LLM contour and the engine must
not import it (§17.2); a test asserts the two agree, which is the guard that
would catch them drifting apart.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from apps.orchestrator.decision_readiness.candidates import (
    CandidateProbe,
    CandidateSetSignature,
    HypotheticalAnswer,
)
from apps.orchestrator.decision_readiness.state import ConversationState, SlotState

# §1.1 — the values live in `apps.orchestrator.discovery` as
# CLARIFICATION_MODE_CONFIRM_ONE / _CHOOSE_MANY / _FREE. Kept as literals; see
# the module docstring on why they are not imported.
MODE_CONFIRM_ONE = "confirm_one"
MODE_CHOOSE_MANY = "choose_many"
MODE_FREE = "free"

CLARIFICATION_MODES: frozenset[str] = frozenset({MODE_CONFIRM_ONE, MODE_CHOOSE_MANY, MODE_FREE})


class QuestionKind(str, Enum):
    """§13.1. The order of these four also fixes selection priority (§13.6)."""

    SAFETY_CLARIFICATION = "safety_clarification"
    REQUIRED_CONTEXT = "required_context"
    DISCRIMINATION = "discrimination"
    BROADENING = "broadening"


_KIND_PRIORITY: dict[QuestionKind, int] = {
    QuestionKind.SAFETY_CLARIFICATION: 0,
    QuestionKind.REQUIRED_CONTEXT: 1,
    QuestionKind.DISCRIMINATION: 2,
    QuestionKind.BROADENING: 3,
}


class OptionRole(str, Enum):
    """Canon §13.1, verbatim — seven roles, no eighth.

    `DELEGATE` is "не знаю / выбери сам" and is the only thing that may raise
    `delegation` (canon §13.2, spec §10.1). `ESCAPE` is "Other" — the offered
    options do not contain the answer. Canon §13.5: neither may disappear when
    the option list is truncated.
    """

    CHOICE = "choice"
    DELEGATE = "delegate"
    ESCAPE = "escape"
    CONFIRM = "confirm"
    ACTION = "action"
    REACTION = "reaction"
    CONSTRAINT_RESOLUTION = "constraint_resolution"


class PresentationRequirement(str, Enum):
    """Canon §13.5."""

    REQUIRED = "required"
    PREFERRED = "preferred"
    OPTIONAL = "optional"


class ImpactClaim(str, Enum):
    """§13.1 — what the answer could change. The claim carries its evidence."""

    ADMISSIBILITY = "admissibility"
    RANKING = "ranking"
    EXECUTION_PARAM = "execution_param"


class AskReason(str, Enum):
    """§13.5 — the closed list. Extending it is a `spec_version` change.

    The model computes none of these. `ask_reason` is derived by the engine from
    the ledger and the probe; a `ModelSignal` appears in none of the five
    re-ask conditions.
    """

    FIRST_ASK = "first_ask"
    REASK_ANSWER_RETRACTED = "reask_answer_retracted"
    REASK_ANSWER_EXPIRED = "reask_answer_expired"
    REASK_SEMANTICS_CHANGED = "reask_semantics_changed"
    REASK_CANDIDATE_SET_CHANGED = "reask_candidate_set_changed"
    REASK_SAFETY_REEVALUATION = "reask_safety_reevaluation"


REASK_REASONS: frozenset[AskReason] = frozenset(
    reason for reason in AskReason if reason is not AskReason.FIRST_ASK
)


@dataclass(frozen=True, slots=True)
class SemanticOption:
    """Canon §13.1. `label_hint` is a hint for rendering, never the semantic value."""

    option_id: str
    role: OptionRole
    semantic_action: str
    label_hint: str = ""
    presentation_priority: int = 0
    presentation_requirement: PresentationRequirement = PresentationRequirement.OPTIONAL
    reason_code: str | None = None


def truncate_options(
    options: Sequence[SemanticOption], *, limit: int
) -> tuple[SemanticOption, ...]:
    """Shorten a list without ever dropping the way out.

    Canon §13.5: escape and delegation options must not disappear accidentally
    during truncation. "Accidentally" is the operative word — a list cut by
    priority alone will drop them exactly when it is longest, which is exactly
    when a person most needs them. So they are kept first and the rest fill the
    remaining room.

    OD §40.3(г) raised the visible-option ceiling to seven; the ceiling is the
    caller's to pass, because it is a surface fact. The non-disappearance rule
    is not.
    """

    if limit < 1:
        raise ValueError("truncate_options: limit must be >= 1")

    protected = [o for o in options if o.role in {OptionRole.ESCAPE, OptionRole.DELEGATE}]
    rest = [o for o in options if o not in protected]
    rest.sort(key=lambda o: (-o.presentation_priority, o.option_id))

    room = max(limit - len(protected), 0)
    kept = rest[:room] + protected
    kept.sort(key=lambda o: (-o.presentation_priority, o.option_id))
    return tuple(kept)


# --- §13.2: the stable id ----------------------------------------------------

_FIELD_SEPARATOR = "\x1f"


def question_id(
    *,
    kind: QuestionKind,
    target_slots: Sequence[str],
    discriminator_key: str,
    semantics_version: int,
) -> str:
    """§13.2, literally. Sixteen hex characters of a SHA-256 over four fields.

    `discriminator_key` is the axis of discrimination from the catalog
    (`"price_band"`, `"duration"`, …) and is the empty string for
    `REQUIRED_CONTEXT`, which discriminates nothing.
    """

    payload = _FIELD_SEPARATOR.join(
        (
            kind.value,
            ",".join(sorted(target_slots)),
            discriminator_key,
            str(semantics_version),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class QuestionCatalogEntry:
    """One approved question. The catalog is data, versioned by `semantics_version`.

    `answer_domain` is the closed set of answers (`D(S)` in §13.3). For
    `mode="free"` it is the declared set of answer *classes*, not of strings —
    §13.3 says so, and a free-text question with no declared classes cannot be
    probed and therefore cannot be shown to change anything.
    """

    kind: QuestionKind
    target_slots: tuple[str, ...]
    mode: str
    semantics_version: int
    discriminator_key: str = ""
    answer_domain: tuple[str, ...] = ()
    options: tuple[SemanticOption, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in CLARIFICATION_MODES:
            raise ValueError(
                f"unknown clarification mode {self.mode!r}; §1.1 fixes three and there is no fourth"
            )
        if not self.target_slots:
            raise ValueError("a question with no target slot cannot be answered into anything")
        if self.kind is QuestionKind.REQUIRED_CONTEXT and self.discriminator_key:
            raise ValueError(
                "REQUIRED_CONTEXT discriminates nothing; §13.2 gives it an empty "
                "discriminator_key, and a non-empty one would silently change its id"
            )

    @property
    def question_id(self) -> str:
        return question_id(
            kind=self.kind,
            target_slots=self.target_slots,
            discriminator_key=self.discriminator_key,
            semantics_version=self.semantics_version,
        )


@dataclass(frozen=True, slots=True)
class QuestionCatalog:
    """The approved questions for one `policy_version`.

    Z3 (§7): a question whose id is not in here is not askable at all — the
    engine blocks rather than asking it. That is what stops "the model phrased
    something reasonable" from becoming a question the ledger has never seen.
    """

    entries: tuple[QuestionCatalogEntry, ...] = ()

    def by_id(self) -> dict[str, QuestionCatalogEntry]:
        return {entry.question_id: entry for entry in self.entries}

    def get(self, qid: str) -> QuestionCatalogEntry | None:
        return self.by_id().get(qid)

    def contains(self, qid: str) -> bool:
        return qid in self.by_id()


EMPTY_CATALOG = QuestionCatalog()


# --- §13.3: the admission rule ----------------------------------------------


@dataclass(frozen=True, slots=True)
class ImpactEvidence:
    """§13.1 `impact_evidence` — the probe digests behind the claim.

    An impact claim with no digests behind it is an opinion. §19 requires the
    record to be replayable, and this is the part of it that makes a question's
    justification checkable after the fact.
    """

    probe_digest_before: str
    probe_digests_after: tuple[str, ...] = ()
    ranking_depth_unconfigured: bool = False


@dataclass(frozen=True, slots=True)
class AskDecision:
    """Whether this question may be asked, why, and on what evidence."""

    allowed: bool
    claim: ImpactClaim | None
    evidence: ImpactEvidence
    suppressed_reason: str | None = None


ASK_SUPPRESSED_NO_IMPACT = "ASK_SUPPRESSED_NO_IMPACT"
ASK_SUPPRESSED_SLOT_FLEXIBLE = "ASK_SUPPRESSED_SLOT_FLEXIBLE"


def ask_allowed(
    entry: QuestionCatalogEntry,
    *,
    state: ConversationState,
    candidates: CandidateSetSignature,
    probe: CandidateProbe,
    execution_required_params: frozenset[str] = frozenset(),
    ranking_comparison_depth: int | None = None,
) -> AskDecision:
    """§13.3, computed. Canon §8's rule, in the only form that can be checked.

    `ranking_comparison_depth` is `k` — the number of cards the surface will
    show (§16.1 calls it derived, not decided). When it is not configured,
    `changes_ranking` is **not** computed and the claim is not made. That
    direction is deliberate: guessing a depth would only ever permit more
    questions, and §15.1 says the refusal to ask is the substantive half of
    failing closed. The reason travels outward under one name and inward as
    `ranking_depth_unconfigured`, so a missing configuration is never read as a
    measured "this question changes nothing".
    """

    before = ImpactEvidence(probe_digest_before=candidates.digest)

    # §13.3 п.1 — the person already said "any". Asking again asks a settled
    # question, and settles it a second time with a worse answer.
    for slot in entry.target_slots:
        if state.slot(slot).state is SlotState.FLEXIBLE:
            return AskDecision(
                allowed=False,
                claim=None,
                evidence=before,
                suppressed_reason=ASK_SUPPRESSED_SLOT_FLEXIBLE,
            )

    # §13.3 п.3 — narrow, explicit exemption. Safety policy decides whether its
    # clarification is needed; ranking has no standing over it.
    if entry.kind is QuestionKind.SAFETY_CLARIFICATION:
        return AskDecision(allowed=True, claim=None, evidence=before)

    # changes_execution_param — no probe needed, and no domain either.
    for slot in entry.target_slots:
        if slot in execution_required_params and state.slot(slot).state is SlotState.UNKNOWN:
            return AskDecision(allowed=True, claim=ImpactClaim.EXECUTION_PARAM, evidence=before)

    if len(entry.answer_domain) < 2:
        # One answer (or none declared) cannot differ from another, so neither
        # admissibility nor ranking can be shown to move.
        return AskDecision(
            allowed=False,
            claim=None,
            evidence=before,
            suppressed_reason=ASK_SUPPRESSED_NO_IMPACT,
        )

    slot = entry.target_slots[0]
    probed = [
        probe.probe(HypotheticalAnswer(slot=slot, value=value)) for value in entry.answer_domain
    ]
    evidence = ImpactEvidence(
        probe_digest_before=candidates.digest,
        probe_digests_after=tuple(signature.digest for signature in probed),
        ranking_depth_unconfigured=ranking_comparison_depth is None,
    )

    eligible_sets = {tuple(sorted(signature.eligible_ids)) for signature in probed}
    if len(eligible_sets) > 1:
        return AskDecision(allowed=True, claim=ImpactClaim.ADMISSIBILITY, evidence=evidence)

    if ranking_comparison_depth is not None:
        prefixes = {signature.ordered_ids[:ranking_comparison_depth] for signature in probed}
        if len(prefixes) > 1:
            return AskDecision(allowed=True, claim=ImpactClaim.RANKING, evidence=evidence)

    return AskDecision(
        allowed=False,
        claim=None,
        evidence=evidence,
        suppressed_reason=ASK_SUPPRESSED_NO_IMPACT,
    )


# --- §13.1 / §13.6: the question chosen -------------------------------------


@dataclass(frozen=True, slots=True)
class NextBestQuestion:
    """§13.1. The question the engine settled on, with its justification attached."""

    question_id: str
    kind: QuestionKind
    target_slots: tuple[str, ...]
    mode: str
    semantics_version: int
    options: tuple[SemanticOption, ...] = ()
    impact_claim: ImpactClaim | None = None
    impact_evidence: ImpactEvidence | None = None
    ask_reason: AskReason = AskReason.FIRST_ASK


@dataclass(frozen=True, slots=True)
class Candidate:
    """One admissible question awaiting the §13.6 ordering."""

    entry: QuestionCatalogEntry
    decision: AskDecision
    ask_reason: AskReason = AskReason.FIRST_ASK
    expected_separation_gain: float | None = None


def _sort_key(candidate: Candidate) -> tuple[int, float, int, str]:
    # Gain is negated so that "more gain" sorts first. An uncomputable gain
    # (separation absent — see `candidates.py`) sorts as zero rather than as a
    # win: an unmeasured improvement is not an improvement.
    gain = candidate.expected_separation_gain or 0.0
    return (
        _KIND_PRIORITY[candidate.entry.kind],
        -gain,
        len(candidate.entry.answer_domain),
        candidate.entry.question_id,
    )


def select_next_question(candidates: Sequence[Candidate]) -> NextBestQuestion | None:
    """§13.6 — a total order, no randomness. Same input, same question.

    Priority by `kind`, then by expected separation gain, then by the smaller
    answer domain (a shorter path to an answer), then lexicographically by
    `question_id`. The last tiebreak exists so that two otherwise identical
    questions never depend on dictionary order.
    """

    admissible = [c for c in candidates if c.decision.allowed]
    if not admissible:
        return None

    best = sorted(admissible, key=_sort_key)[0]
    entry = best.entry
    return NextBestQuestion(
        question_id=entry.question_id,
        kind=entry.kind,
        target_slots=tuple(sorted(entry.target_slots)),
        mode=entry.mode,
        semantics_version=entry.semantics_version,
        options=entry.options,
        impact_claim=best.decision.claim,
        impact_evidence=best.decision.evidence,
        ask_reason=best.ask_reason,
    )


def expected_separation_gain(
    entry: QuestionCatalogEntry,
    *,
    candidates: CandidateSetSignature,
    probe: CandidateProbe,
) -> float | None:
    """§13.6 п.2 — `max_d probe(S=d).separation − candidates.separation`.

    `None` when the resolver does not compute `separation` at all. Absent is not
    zero: a gain of zero says the question was measured and does not help, and
    `None` says nothing was measured. Only the first is a fact.
    """

    if candidates.separation is None or not entry.answer_domain:
        return None

    slot = entry.target_slots[0]
    gains = []
    for value in entry.answer_domain:
        probed = probe.probe(HypotheticalAnswer(slot=slot, value=value))
        if probed.separation is None:
            return None
        gains.append(probed.separation - candidates.separation)
    return max(gains) if gains else None
