"""What the engine receives from the Safety Engine. Consumer side only.

**No safety policy is designed here.** The matrix of safety signals belongs to
the owner and does not exist yet (spec §16.2, canon §7 "Still open"). This
module is the shape of the answer, and the rule for what to do when there
isn't one.

### Why there is no default

Spec §11.4, in as many words: a silent default of `NORMAL` is **forbidden**.
The Safety Engine may only answer `NORMAL` when it actually ran. Not having run
is `UNKNOWN`, and `UNKNOWN` gives `BLOCKED(SAFETY_UNKNOWN)` — canon §6, unknown
safety-critical semantics fail closed.

That is why `SafetyResult` has no default state and why the way to say "this
was never evaluated" is `SafetyResult.not_evaluated()`, which is `UNKNOWN` and
says so by name. An absent evaluation with a name behaves differently from an
absent evaluation without one: the first blocks, the second silently permits.

Today, on `883b7539`, no producer is wired to this input at all. That means the
engine's honest answer on a live turn is `BLOCKED(SAFETY_UNKNOWN)`. That is the
correct behaviour and not a placeholder — a stub returning `NORMAL` would be
fail-open in a mechanism built entirely around failing closed. Wiring the real
producer is slice E8 and depends on lane A.

### `CLARIFY` is not `BLOCKED`

Spec §11.1: `CLARIFY` maps to `NEEDS_REQUIRED_CONTEXT`, not `BLOCKED`. Both
forbid recommending, so it is not a product fork; `NEEDS_REQUIRED_CONTEXT` is
chosen because it is the only one of the two that describes a way out — ask the
safety question. Records carrying it are still separable in the audit by
`reason ∋ SAFETY_CLARIFY_REQUIRED` and `required_context.owner == SAFETY`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SafetyState(str, Enum):
    """Canon §7.1's four states, plus the one that means "we did not get an answer".

    `UNKNOWN` is not a fifth policy outcome — it is the absence of an outcome,
    given a name so that it can be acted on (rule 4, discipline of absence).
    """

    NORMAL = "normal"
    CLARIFY = "clarify"
    CAUTION = "caution"
    STOP = "stop"
    UNKNOWN = "unknown"


KNOWN_SAFETY_STATES: frozenset[SafetyState] = frozenset(
    {SafetyState.NORMAL, SafetyState.CLARIFY, SafetyState.CAUTION, SafetyState.STOP}
)


@dataclass(frozen=True, slots=True)
class SafetyResult:
    """The Safety Engine's verdict for one revision.

    `evaluated_at_revision` carries precondition P3: safety must have been
    computed for **this** `state_revision`. A verdict computed for an older one
    is stale input, and §15.2 blocks on it rather than using it — a safety
    answer from before the person's last message is an answer to a different
    question.

    It lives on this type rather than in `InputAvailability` (where spec §4
    lists it) because §11's algorithm reads it as `safety.evaluated_at_revision`
    and because a verdict and the revision it was computed for are one fact, not
    two that must be kept in step by hand.
    """

    state: SafetyState
    evaluated_at_revision: int | None
    rule_id: str | None = None
    policy_version: str | None = None
    required_slots: tuple[str, ...] = ()
    forbidden_capabilities: tuple[str, ...] = ()

    @classmethod
    def not_evaluated(cls) -> SafetyResult:
        """The Safety Engine did not run. `UNKNOWN`, and named as such."""

        return cls(state=SafetyState.UNKNOWN, evaluated_at_revision=None)

    def __post_init__(self) -> None:
        if self.state in KNOWN_SAFETY_STATES and self.evaluated_at_revision is None:
            raise ValueError(
                f"SafetyResult({self.state.value}) claims a verdict without saying which "
                "revision it was computed for. A verdict with no revision cannot be checked "
                "for staleness (P3), and an unstale-able NORMAL is the silent default §11.4 "
                "forbids."
            )
        if self.evaluated_at_revision is not None and self.evaluated_at_revision < 1:
            raise ValueError("SafetyResult.evaluated_at_revision must be >= 1")

    @property
    def is_known(self) -> bool:
        return self.state in KNOWN_SAFETY_STATES

    def digest_fields(self) -> tuple[str, ...]:
        """Stable, ordered projection for the idempotency key (§17.3)."""

        return (
            self.state.value,
            str(self.evaluated_at_revision),
            self.rule_id or "",
            self.policy_version or "",
            ",".join(sorted(self.required_slots)),
            ",".join(sorted(self.forbidden_capabilities)),
        )


@dataclass(frozen=True, slots=True)
class SafetyUnavailable:
    """Marker for "the producer itself could not be reached".

    Distinct from `SafetyResult.not_evaluated()` only in the audit: both block,
    and both must. Keeping them apart means a transport outage and a policy that
    declined to answer do not arrive under one name — the failure mode rule 4
    calls "имя состояния по умолчанию обвиняет источник".
    """

    reason: str
    detail: str = field(default="")
