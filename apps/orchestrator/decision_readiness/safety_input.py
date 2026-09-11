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


class Handoff(str, Enum):
    """What the verdict promises about the next step — §127, frozen at three.

    > **Кризисный handoff не должен быть неотличим от обычного policy refusal.**

    `BLOCK` and `HANDOFF` are not collapsed: both can stop an action, and they
    make **different promises to the person**. `rule_id` is evidence about the
    cause; this is a promise about what happens next, and it has to be readable
    without knowing the rules.

    Three values, not a boolean, and §127 is explicit about why: `NONE` means
    "not required", **not** "the field was absent". Absence has its own home —
    `SafetyState.UNKNOWN`, which already means the engine did not run. A verdict
    that exists carries a handoff; a verdict that does not exist carries
    nothing, and `__post_init__` enforces both directions.
    """

    NONE = "none"
    RECOMMENDED = "recommended"
    REQUIRED = "required"


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
    handoff: Handoff | None = None
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
        if self.state in KNOWN_SAFETY_STATES and self.handoff is None:
            raise ValueError(
                f"SafetyResult({self.state.value}) states a verdict without a handoff. "
                "§127 keeps BLOCK and HANDOFF apart because they promise the person "
                "different things; a verdict that does not say which promise it makes "
                "leaves the surface to guess, and NONE is a decision, not a default."
            )
        if self.state not in KNOWN_SAFETY_STATES and self.handoff is not None:
            raise ValueError(
                "a verdict that was never computed cannot promise a next step: "
                f"state={self.state.value} with handoff={self.handoff.value}"
            )

    @property
    def is_known(self) -> bool:
        return self.state in KNOWN_SAFETY_STATES

    def digest_fields(self) -> tuple[str, ...]:
        """Stable, ordered projection for the idempotency key (§17.3)."""

        return (
            self.state.value,
            # §127. Not because two promises would otherwise collide today:
            # `rule_id` is in this projection too and the two promises happen to
            # arrive from different rule buckets
            # (`pre_check.regex:handoff` against `pre_check.regex:block`), so
            # their digests already differ. That separation is a **coincidence
            # of today's catalogue**, not a contract: two verdicts from one
            # bucket with different promises would digest identically, and
            # nothing here forbids it, because the projection had no notion of a
            # promise. This line gives it one.
            self.handoff.value if self.handoff else "",
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
