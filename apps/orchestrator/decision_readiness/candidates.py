"""What the engine is allowed to know about the candidate set. Input contract only.

**No resolver is designed here.** Ranking, its stages and its formula belong to
lane C / lane D. This module states what the engine receives and what it may
demand — spec §12 writes the requirement on the resolver from this side
deliberately, and says so.

### The engine never sees a raw score

Canon §8: a raw ranking score "may exist, but is not probability/confidence and
is not a public semantic API". So the signature carries order, cardinality,
eligibility flags and one normalised number (`separation`, DRF-1533). Nothing
else. A raw score reaching the engine would be the model-confidence prohibition
(§7 Z1) rebuilt out of resolver parts.

### `catalog_visible` is not `recommendation_eligible`

Canon §14 and OD §40.2 п.5: an unmapped service may be shown, and a person may
book it **by their own explicit choice** — but it may not be recommended. Two
counts, therefore, and a rule for the gap between them:
`visible_count > 0` with `recommendation_eligible_count == 0` is
`BLOCKED(CATALOG_NOT_RECOMMENDABLE)`, not "recommend the visible ones".

`recommendation_eligible_count` is `int | None` on purpose. Spec §12.3: the
**absence** of the flag is `UNKNOWN`, not `true`. A zero and a missing count are
different facts and they must not share a representation — the measurement on
the pilot (OD §40.3(д), §41) found `contraindications` and `short_description`
empty on all 265 services with no verified mapping anywhere, which is exactly
the state in which "absent means yes" would silently recommend everything.

### `separation` may be absent, and absent is not zero

DRF-1533's measurement says `separation` is identically zero on the pilot
today. That is a *measured* zero. A resolver that does not compute the value at
all must report `None`, not `0.0`: the first is "we cannot tell candidates
apart", the second is "we can, and we did, and they are identical". Only the
second is a fact about the catalog.

### `probe` is the resolver's obligation, not the engine's guess

`ask_allowed` (§13.3) is computable only by asking what the candidate set would
look like under a hypothetical answer. Spec §12.2 makes `probe` pure and
reproducible, and makes its absence fail closed:
`availability.probe_available = False` → `BLOCKED(READINESS_INPUT_UNAVAILABLE)`.
Guessing instead of probing is prohibited — a question whose impact was assumed
rather than computed is precisely the five screens of DRF-1542.

Slice 1 ships this as a `Protocol`. No implementation is provided, and today no
resolver satisfies it, so the honest live answer is the block above. Wiring it
is slice E7 and depends on lane D.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from apps.orchestrator.decision_readiness.evidence import ConfirmedEvidence


@dataclass(frozen=True, slots=True)
class CandidateSetSignature:
    """Spec §12.1, as data. Behaviour that needs the resolver lives in `CandidateProbe`.

    `spanning_fields` replaces spec's `spans(field) -> bool` method: the set of
    fields over which the admissible candidates differ is a fact about the set,
    and holding it as data keeps the signature hashable for the idempotency key
    (§17.3) and replayable in the audit (§19).
    """

    digest: str
    visible_count: int
    recommendation_eligible_count: int | None
    ordered_ids: tuple[str, ...] = ()
    separation: float | None = None
    spanning_fields: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.visible_count < 0:
            raise ValueError("visible_count must be >= 0")
        if (
            self.recommendation_eligible_count is not None
            and self.recommendation_eligible_count < 0
        ):
            raise ValueError("recommendation_eligible_count must be >= 0 or None (unknown)")
        if self.separation is not None and not (0.0 <= self.separation <= 1.0):
            raise ValueError(f"separation must be within [0, 1], got {self.separation!r}")

    @property
    def cardinality(self) -> int | None:
        """Size of the admissible set — counted over `recommendation_eligible`.

        `None` when eligibility is unknown, because a cardinality computed over
        "everything visible" would answer a different question than the one the
        engine asks.
        """

        return self.recommendation_eligible_count

    def spans_more_than_one(self, candidate_field: str) -> bool:
        """§8.2 term: do the admissible candidates differ on this field?"""

        return candidate_field in self.spanning_fields

    def digest_fields(self) -> tuple[str, ...]:
        """Stable, ordered projection for the idempotency key (§17.3)."""

        return (
            self.digest,
            str(self.visible_count),
            str(self.recommendation_eligible_count),
            ",".join(self.ordered_ids),
            "none" if self.separation is None else f"{self.separation:.6f}",
            ",".join(sorted(self.spanning_fields)),
        )


@runtime_checkable
class CandidateProbe(Protocol):
    """The resolver's obligation to this engine — spec §12.2.

    Both methods must be pure: same input, same answer, no state changed. The
    engine calls `probe` several times per turn while testing whether a question
    could change anything, and a probe with side effects would make the question
    it is evaluating happen.
    """

    def probe(self, evidence_delta: ConfirmedEvidence) -> CandidateSetSignature:
        """The signature the set *would* have under this hypothetical evidence."""
        ...

    def narrowed_by(self, evidence: ConfirmedEvidence) -> bool:
        """Did applying this evidence actually shrink the admissible set?

        §11.2 grounds `INSUFFICIENT_EVIDENCE` on this rather than on "is there
        any text": a need is grounded when something narrowed, not when
        something was said.
        """
        ...


@dataclass(frozen=True, slots=True)
class ProbeUnavailable:
    """Named absence of a probe. See the module docstring on why guessing is barred."""

    reason: str
