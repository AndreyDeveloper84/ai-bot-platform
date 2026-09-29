"""`DecisionEvidence` — the record of why, §19. Shape only; nothing is persisted here.

Canon §8 requires every decision to carry `DecisionEvidence` and reason codes for
WHY and for auditability. Persistence belongs to the Ayla backend, next to the
recommendation's own evidence (canon §9.3, §10.1), and spec §19 declines to
design the storage. So this module builds the record and stops.

### Two fields that are not optional

`evidence_considered[].origin` and `model_signals_rejected[]` are mandatory
(§19). The second is the one that is easy to leave out and the one that matters:
without it, a DRF-1542-class defect is indistinguishable from normal operation
in the log. Both cases show a question being asked. Only the rejected list shows
that the model had already "answered" it and was not believed.

### PII

The record carries `source_ref` — identifiers — and never the text of what
anyone said. Utterances live in the conversation history under its own lifetime
(§19). A decision log that quoted people would outlive the retention rules that
apply to the transcript.

### Replayability

A record either replays to the same state or is marked `NOT_REPLAYABLE`. There is
no third option, and specifically no silent difference: `readiness_key` is a hash
of the inputs (§17.3), so a record whose key does not match a re-run was built
from something else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from apps.orchestrator.decision_readiness.engine import (
    Delegation,
    ReadinessInput,
    ReadinessOutput,
)
from apps.orchestrator.decision_readiness.events import Surface
from apps.orchestrator.decision_readiness.required_context import Mode


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """§19's record, as data."""

    readiness_evaluation_id: str
    readiness_key: str
    state_revision: int
    surface: Surface
    mode: Mode
    readiness_state: str
    allow_recommend: bool
    delegation: Delegation
    reason_codes: tuple[str, ...]
    measures: dict[str, Any]
    required_context: tuple[dict[str, Any], ...]
    evidence_considered: tuple[dict[str, Any], ...]
    model_signals_rejected: tuple[dict[str, Any], ...]
    candidates: dict[str, Any]
    safety: dict[str, Any]
    question: dict[str, Any] | None
    spec_version: str
    policy_version: int
    replayable: bool = True
    not_replayable_reason: str | None = field(default=None)


def build(
    request: ReadinessInput,
    output: ReadinessOutput,
    *,
    evaluation_id: str,
) -> DecisionEvidence:
    """Assemble the record from the input and the output that came out of it.

    `evaluation_id` is supplied rather than generated: this module has no clock
    and no randomness either (§17.1), and a record whose id came from a UUID
    generator would make two runs of the same decision differ in the log.
    """

    return DecisionEvidence(
        readiness_evaluation_id=evaluation_id,
        readiness_key=output.readiness_key,
        state_revision=request.state_revision,
        surface=request.surface,
        mode=request.mode,
        readiness_state=output.readiness_state.value,
        allow_recommend=output.allow_recommend,
        delegation=request.delegation,
        reason_codes=output.reason_codes,
        measures=dict(output.measures),
        required_context=tuple(
            {"slot": slot, "verdict": verdict.value}
            for slot, verdict in sorted(output.required_context.items())
        ),
        evidence_considered=tuple(
            {
                "evidence_id": item.evidence_id,
                "slot": item.slot,
                "origin": item.origin.value,
                "source_ref": type(item.source_ref).__name__,
                "confirmed_by": item.confirmed_by,
            }
            for item in sorted(request.evidence, key=lambda e: e.evidence_id)
        ),
        model_signals_rejected=tuple(dict(entry) for entry in output.model_signals_rejected),
        candidates={
            "digest": request.candidates.digest,
            "visible_count": request.candidates.visible_count,
            "eligible_count": request.candidates.recommendation_eligible_count,
            "cardinality": request.candidates.cardinality,
        },
        safety={
            "state": request.safety.state.value,
            "rule_id": request.safety.rule_id,
            "policy_version": request.safety.policy_version,
            "evaluated_at_revision": request.safety.evaluated_at_revision,
        },
        question=(
            {
                "question_id": output.next_question.question_id,
                "kind": output.next_question.kind.value,
                "mode": output.next_question.mode,
                "target_slots": list(output.next_question.target_slots),
                "impact_claim": (
                    output.next_question.impact_claim.value
                    if output.next_question.impact_claim
                    else None
                ),
                "ask_reason": output.next_question.ask_reason.value,
                "ask_reason_mechanism": output.next_question.ask_reason_mechanism,
            }
            if output.next_question
            else None
        ),
        spec_version=output.spec_version,
        policy_version=output.policy_version,
    )


def mark_not_replayable(record: DecisionEvidence, reason: str) -> DecisionEvidence:
    """A record whose inputs no longer reproduce its state says so, in the record.

    §19: a silent divergence must not be possible. The alternative — dropping
    the record, or quietly keeping the old state — is how an audit stops being
    evidence of anything.
    """

    from dataclasses import replace

    return replace(record, replayable=False, not_replayable_reason=reason)
