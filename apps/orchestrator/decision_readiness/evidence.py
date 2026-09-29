"""Evidence and its origin — kept apart by type, because a field can be filled wrongly.

### The invariant, and why it is a type and not a flag

EVIDENCE ORIGIN (spec §20): *what the model produced never becomes what the
person said.* The obvious implementation is one class with an `origin` field.
It fails for a boring reason: a field is set by whoever constructs the object,
and the live defect this lane exists to remove is precisely a construction that
set the wrong one. `apps/orchestrator/nutrition_global.py:262-286` puts the
model's tool-call arguments into `message_text` — no flag was lied about there,
the value simply arrived through a channel that means something else.

So there are two types. `ConfirmedEvidence` has no constructor that accepts a
model's output: `MODEL_INFERENCE` is not in `CONFIRMABLE_ORIGINS`, and each of
the four intake functions demands a reference only its own channel can mint —
a `SemanticUserEvent` (which itself refuses to be built from a tool argument),
a backend fetch, or a promoted memory entry.

Python cannot make a class truly unconstructible, and this module does not
pretend otherwise. What it can do, and does:

* `MODEL_INFERENCE` in `ConfirmedEvidence` raises — that one is absolute;
* the `source_ref` must be the reference type that matches the origin, so a
  message id cannot stand in for a backend fetch;
* direct construction outside the four intake functions raises, via a
  module-private token those functions hold. A determined caller can import the
  token; the point is that doing so is a visible, greppable act rather than an
  accident.

### The one legal transition

There is no `ModelSignal → ConfirmedEvidence` conversion. There is
`confirm(signal, confirming_event)`: the person answered, and the answer
carries a revision strictly later than the guess it answers. The result's
origin and `source_ref` come from the **confirming event**, never from the
signal; the signal survives only as `confirmed_by` in the audit trail.

`confirm()` is what makes a pre-filled question honest: the model may propose
"поясница → body_area=lower_back" and the person may close it with one tap.
The value came from the model. The authority came from the tap. Those are
different questions and the record answers both.

### What a `ModelSignal` may do

Exactly one thing (spec §3.1 п.4): help choose **which slot to ask about**. It
never closes a slot, never lifts a block, never raises `delegation`, never
grants the right to ask again. The satisfaction function (§8.3,
`required_context.py`) does not read it at all.

Spec: `docs/specs/DECISION_READINESS_ENGINE_v1.0.md` §3.1, §6, §8.3, §14, §20.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from apps.orchestrator.decision_readiness.events import SemanticUserEvent, UserEventKind


class EvidenceOrigin(str, Enum):
    """Closed enumeration — spec §3.1. Adding a member is a `spec_version` change."""

    USER_TEXT = "user_text"
    USER_ACTION = "user_action"
    DOMAIN_AUTHORITY = "domain_authority"
    PROMOTED_MEMORY = "promoted_memory"
    MODEL_INFERENCE = "model_inference"


CONFIRMABLE_ORIGINS: frozenset[EvidenceOrigin] = frozenset(
    {
        EvidenceOrigin.USER_TEXT,
        EvidenceOrigin.USER_ACTION,
        EvidenceOrigin.DOMAIN_AUTHORITY,
        EvidenceOrigin.PROMOTED_MEMORY,
    }
)


# --- source references: one shape per channel -------------------------------
#
# Separate types, not a dict. A dict would let a message id sit in the field a
# backend fetch is supposed to fill, and nothing downstream would notice.


@dataclass(frozen=True, slots=True)
class MessageRef:
    """USER_TEXT — the transport's own id for a message the person sent."""

    message_id: str


@dataclass(frozen=True, slots=True)
class DecisionRef:
    """USER_ACTION — canon §13.1: a decision the surface rendered, and the option tapped."""

    decision_id: str
    option_id: str


@dataclass(frozen=True, slots=True)
class DomainRef:
    """DOMAIN_AUTHORITY — a fact the Ayla backend owns, with when it was fetched.

    `fetched_at` is here because a domain fact is true as of a moment: price,
    availability and capability all move. Expiry is applied by the input adapter
    (spec §4.1 P2), which needs this field to apply it.
    """

    api: str
    entity_id: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryRef:
    """PROMOTED_MEMORY — a memory entry that passed promotion (canon §11).

    OD-DR-3 (CLOSED): promoted memory does **not** by itself close a safety
    slot. That restriction lives per-slot in `RequiredContextSpec.
    satisfied_by_origins` (§8.1), not here — a slot decides which origins may
    satisfy it, and evidence does not decide what it is good enough for.
    """

    memory_entry_id: str


SourceRef = MessageRef | DecisionRef | DomainRef | MemoryRef

_ORIGIN_TO_REF: dict[EvidenceOrigin, type] = {
    EvidenceOrigin.USER_TEXT: MessageRef,
    EvidenceOrigin.USER_ACTION: DecisionRef,
    EvidenceOrigin.DOMAIN_AUTHORITY: DomainRef,
    EvidenceOrigin.PROMOTED_MEMORY: MemoryRef,
}


class _IntakeToken:
    """Held only by this module's intake functions. See the module docstring on
    what this does and does not guarantee."""

    __slots__ = ()


_INTAKE = _IntakeToken()


@dataclass(frozen=True, slots=True)
class ModelSignal:
    """What the model extracted. A candidate value, and nothing more.

    `origin` is a constant of the type, not a parameter: there is no way to
    construct a `ModelSignal` that claims to be anything else.
    """

    signal_id: str
    slot: str
    value: str
    produced_at_revision: int
    extractor_id: str

    @property
    def origin(self) -> EvidenceOrigin:
        return EvidenceOrigin.MODEL_INFERENCE

    def __post_init__(self) -> None:
        if self.produced_at_revision < 1:
            raise ValueError("ModelSignal.produced_at_revision must be >= 1")


@dataclass(frozen=True, slots=True)
class ConfirmedEvidence:
    """A settled fact about one slot, with the channel that settled it.

    Built only through the four intake functions below and `confirm()`.
    """

    evidence_id: str
    slot: str
    value: str
    origin: EvidenceOrigin
    source_ref: SourceRef
    observed_at: datetime
    captured_at_revision: int
    confirmed_by: str | None = None
    issued_by: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.issued_by is not _INTAKE:
            raise ValueError(
                "ConfirmedEvidence must be built through an intake function "
                "(from_user_text / from_user_action / from_domain_authority / "
                "from_promoted_memory) or confirm(). Direct construction skips the "
                "origin-to-reference check that makes EVIDENCE ORIGIN structural."
            )
        if self.origin not in CONFIRMABLE_ORIGINS:
            raise ValueError(
                f"{self.origin.value} is not a confirmable origin. A model's inference "
                "is a ModelSignal; there is no conversion from one to the other."
            )
        expected = _ORIGIN_TO_REF[self.origin]
        if not isinstance(self.source_ref, expected):
            raise ValueError(
                f"origin {self.origin.value} requires a {expected.__name__}, "
                f"got {type(self.source_ref).__name__}"
            )
        if self.captured_at_revision < 1:
            raise ValueError("ConfirmedEvidence.captured_at_revision must be >= 1")


# --- the four intake functions, one per confirmable origin -------------------


def from_user_text(
    *,
    evidence_id: str,
    slot: str,
    value: str,
    event: SemanticUserEvent,
) -> ConfirmedEvidence:
    """The person wrote it. The reference is the transport's message id.

    Takes a `SemanticUserEvent` rather than a bare string on purpose: a string
    is what a tool call produces, and an event is not.
    """

    if event.kind is not UserEventKind.TEXT:
        raise ValueError(f"from_user_text requires a TEXT event, got {event.kind.value}")
    assert event.message_id is not None  # guaranteed by SemanticUserEvent.__post_init__
    return ConfirmedEvidence(
        evidence_id=evidence_id,
        slot=slot,
        value=value,
        origin=EvidenceOrigin.USER_TEXT,
        source_ref=MessageRef(message_id=event.message_id),
        observed_at=event.observed_at,
        captured_at_revision=event.revision,
        issued_by=_INTAKE,
    )


def from_user_action(
    *,
    evidence_id: str,
    slot: str,
    value: str,
    event: SemanticUserEvent,
) -> ConfirmedEvidence:
    """The person tapped an option the surface had rendered (canon §13.1)."""

    if event.kind is not UserEventKind.ACTION:
        raise ValueError(f"from_user_action requires an ACTION event, got {event.kind.value}")
    assert event.decision_id is not None and event.option_id is not None
    return ConfirmedEvidence(
        evidence_id=evidence_id,
        slot=slot,
        value=value,
        origin=EvidenceOrigin.USER_ACTION,
        source_ref=DecisionRef(decision_id=event.decision_id, option_id=event.option_id),
        observed_at=event.observed_at,
        captured_at_revision=event.revision,
        issued_by=_INTAKE,
    )


def from_domain_authority(
    *,
    evidence_id: str,
    slot: str,
    value: str,
    api: str,
    entity_id: str,
    fetched_at: datetime,
    at_revision: int,
) -> ConfirmedEvidence:
    """The Ayla backend owns this fact — availability, price, capability (spec §6)."""

    return ConfirmedEvidence(
        evidence_id=evidence_id,
        slot=slot,
        value=value,
        origin=EvidenceOrigin.DOMAIN_AUTHORITY,
        source_ref=DomainRef(api=api, entity_id=entity_id, fetched_at=fetched_at),
        observed_at=fetched_at,
        captured_at_revision=at_revision,
        issued_by=_INTAKE,
    )


def from_promoted_memory(
    *,
    evidence_id: str,
    slot: str,
    value: str,
    memory_entry_id: str,
    promoted_at: datetime,
    at_revision: int,
) -> ConfirmedEvidence:
    """A memory entry that passed promotion (canon §11).

    Whether it may satisfy a given slot is the slot's decision — OD-DR-3 keeps
    safety slots out of its reach. See `MemoryRef`.
    """

    return ConfirmedEvidence(
        evidence_id=evidence_id,
        slot=slot,
        value=value,
        origin=EvidenceOrigin.PROMOTED_MEMORY,
        source_ref=MemoryRef(memory_entry_id=memory_entry_id),
        observed_at=promoted_at,
        captured_at_revision=at_revision,
        issued_by=_INTAKE,
    )


# --- the one legal transition ------------------------------------------------


def confirm(
    signal: ModelSignal,
    confirming_event: SemanticUserEvent,
    *,
    evidence_id: str,
) -> ConfirmedEvidence:
    """The person answered the guess. The answer, not the guess, becomes evidence.

    Requires `confirming_event.revision > signal.produced_at_revision`: an event
    that predates the guess cannot have been a reply to it. Without the strict
    comparison, replaying an earlier turn would "confirm" anything the model
    later invented — the shape of DRF-1542's fourth and fifth screens, where the
    model re-supplied `symptom_text` and the flow treated it as new input.

    The result's origin and `source_ref` are the event's. `confirmed_by` keeps
    the signal id so an audit can still see that a model proposed the value.
    """

    if confirming_event.revision <= signal.produced_at_revision:
        raise ValueError(
            f"confirming event (revision {confirming_event.revision}) does not follow "
            f"the signal (revision {signal.produced_at_revision}) — it cannot be a reply to it"
        )

    if confirming_event.kind is UserEventKind.TEXT:
        assert confirming_event.message_id is not None
        origin = EvidenceOrigin.USER_TEXT
        source_ref: SourceRef = MessageRef(message_id=confirming_event.message_id)
    else:
        assert confirming_event.decision_id is not None and confirming_event.option_id is not None
        origin = EvidenceOrigin.USER_ACTION
        source_ref = DecisionRef(
            decision_id=confirming_event.decision_id,
            option_id=confirming_event.option_id,
        )

    return ConfirmedEvidence(
        evidence_id=evidence_id,
        slot=signal.slot,
        value=signal.value,
        origin=origin,
        source_ref=source_ref,
        observed_at=confirming_event.observed_at,
        captured_at_revision=confirming_event.revision,
        confirmed_by=signal.signal_id,
        issued_by=_INTAKE,
    )
