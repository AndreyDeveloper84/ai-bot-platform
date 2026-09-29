"""`StructuredDecision` — the readiness verdict, projected into ASK / RECOMMEND / BLOCK.

This is the bottom of the owner's diagram and the end of slice 1's vertical.
What it is *not* is a renderer: canon §13 keeps `ayla-ai-core` and the surface
adapter apart on purpose, so nothing here knows about MAX buttons, and the
`label_hint` on an option is a hint rather than the semantic value.

### Why the projection is separate from `evaluate()`

Five states, three outcomes. The mapping is small enough to inline and important
enough not to: `NEEDS_DISCRIMINATION` with `allow_recommend = True` becomes
RECOMMEND, and the same state with `allow_recommend = False` becomes ASK. Those
two lines are the visible face of the DELEGATION CEILING, and they are worth
being able to point at.

### `decision_id` is derived, not generated

Canon §13.4 puts `decision_id` and `based_on_state_revision` in every decision so
that a stale callback can be classified. A random id would work for that and
would break §17.1: the same input must produce the same output, byte for byte.
So the id is a hash of the readiness key, the outcome and the question — which
also means a retried delivery of the same decision carries the same id rather
than a new one.

### What a BLOCK does not decide

§15.3 is explicit that what the person sees instead is the owner's open question
(already raised in DRF-1542, and §22's second question), not this document's.
This module therefore produces a BLOCK with its blockers and reason codes, and
no wording, no fallback and no CTA. The one thing fixed normatively: on `STOP`
there is no diagnosis and no "book it anyway" (canon §7).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum

from apps.orchestrator.decision_readiness.engine import (
    Blocker,
    ReadinessOutput,
    ReadinessState,
)
from apps.orchestrator.decision_readiness.questions import (
    NextBestQuestion,
    SemanticOption,
    truncate_options,
)


class DecisionType(str, Enum):
    ASK = "ask"
    RECOMMEND = "recommend"
    BLOCK = "block"


@dataclass(frozen=True, slots=True)
class PromptSemantics:
    """What the question means, for the model to phrase — never the phrasing itself.

    Spec §6 lets the LLM render approved semantics naturally (LLM_ALLOWED) and
    forbids it adding an option that controlled logic did not supply. Handing
    over a `question_id`, a kind and a slot set — rather than a sentence — is
    what keeps that boundary where it is.
    """

    question_id: str
    kind: str
    target_slots: tuple[str, ...]
    mode: str
    semantics_version: int


@dataclass(frozen=True, slots=True)
class StructuredDecision:
    """Canon §13.1's shape, unchanged."""

    decision_id: str
    based_on_state_revision: int
    decision_type: DecisionType
    reason_codes: tuple[str, ...]
    prompt_semantics: PromptSemantics | None = None
    options: tuple[SemanticOption, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    readiness_state: ReadinessState = ReadinessState.BLOCKED
    policy_version: int = 0


def _decision_id(readiness_key: str, decision_type: DecisionType, question_id: str) -> str:
    payload = "\x1f".join((readiness_key, decision_type.value, question_id))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def project(
    output: ReadinessOutput,
    *,
    state_revision: int,
    option_limit: int = 7,
) -> StructuredDecision:
    """Five states to three outcomes.

    `option_limit` defaults to seven because OD §40.3(г) is where the owner put
    the ceiling; it stays a parameter because the real limit is a surface fact
    (canon §13.5's "Still open": verified MAX platform limits). Whatever the
    limit, escape and delegation survive it — `truncate_options` sees to that,
    and that rule is not the surface's to relax.
    """

    if output.readiness_state is ReadinessState.BLOCKED:
        return StructuredDecision(
            decision_id=_decision_id(output.readiness_key, DecisionType.BLOCK, ""),
            based_on_state_revision=state_revision,
            decision_type=DecisionType.BLOCK,
            reason_codes=output.reason_codes,
            blockers=output.blockers,
            readiness_state=output.readiness_state,
            policy_version=output.policy_version,
        )

    if output.allow_recommend:
        # READY, or NEEDS_DISCRIMINATION unlocked by an explicit delegation.
        return StructuredDecision(
            decision_id=_decision_id(output.readiness_key, DecisionType.RECOMMEND, ""),
            based_on_state_revision=state_revision,
            decision_type=DecisionType.RECOMMEND,
            reason_codes=output.reason_codes,
            readiness_state=output.readiness_state,
            policy_version=output.policy_version,
        )

    question = output.next_question
    if question is None:
        # The §5 output invariant says this cannot happen: a non-blocked state
        # that may not recommend must carry a question. Raising rather than
        # inventing an outcome keeps a broken invariant loud instead of turning
        # it into a silently empty turn.
        raise ValueError(
            f"readiness_state={output.readiness_state.value} with allow_recommend=False "
            "and no next_question — the §5 output invariant is broken"
        )

    return _ask(output, question, state_revision, option_limit)


def _ask(
    output: ReadinessOutput,
    question: NextBestQuestion,
    state_revision: int,
    option_limit: int,
) -> StructuredDecision:
    return StructuredDecision(
        decision_id=_decision_id(output.readiness_key, DecisionType.ASK, question.question_id),
        based_on_state_revision=state_revision,
        decision_type=DecisionType.ASK,
        reason_codes=output.reason_codes,
        prompt_semantics=PromptSemantics(
            question_id=question.question_id,
            kind=question.kind.value,
            target_slots=question.target_slots,
            mode=question.mode,
            semantics_version=question.semantics_version,
        ),
        options=truncate_options(question.options, limit=option_limit) if question.options else (),
        readiness_state=output.readiness_state,
        policy_version=output.policy_version,
    )


@dataclass(frozen=True, slots=True)
class DecisionSummary:
    """A one-line projection for logs. Carries codes, never wording (canon §13.5)."""

    decision_type: DecisionType
    readiness_state: ReadinessState
    question_id: str | None = None
    blocker_types: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, decision: StructuredDecision) -> DecisionSummary:
        return cls(
            decision_type=decision.decision_type,
            readiness_state=decision.readiness_state,
            question_id=decision.prompt_semantics.question_id
            if decision.prompt_semantics
            else None,
            blocker_types=tuple(sorted(b.blocker_type.value for b in decision.blockers)),
        )
