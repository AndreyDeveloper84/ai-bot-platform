"""DecisionReadiness — the decision "ask or act" stops being the model's to make.

Epic DRF-1629, lane E of the controlled pilot (CP-5). Specification:
`docs/specs/DECISION_READINESS_ENGINE_v1.0.md`.

Today three global authorities decide whether Ayla asks or acts, and all three
are language models: the MAX concierge (`concierge.py:1163` + `discovery.py:460`),
the catalog chat, and the booking skill's Phase 1. The owner's ruling on
10.09.2026, verbatim: *"Это нельзя сделать по-настоящему надёжным одними
хорошими промптами."*

The model stays, and stays useful — it understands free language, extracts
values, phrases the question, explains the recommendation. One right is taken
from it: deciding whether there is enough evidence to act.

**Where this lives.** Spec §6 places the computation in `ayla-ai-core`;
OD-DR-4 (CLOSED) names `ai-bot-platform` the P0 owner with Redis storage
(`MEASUREMENT_DECISION_READINESS_CURRENT.md:552-556`). §6 describes the target
placement, OD-DR-4 describes P0. Moving it there later is its own work.

**Slice 1** (this one) is the vertical without consumers:
`SemanticUserEvent → ConversationState → DecisionReadiness → StructuredDecision
→ ASK / RECOMMEND / BLOCK`. Consumers — MAX, catalog chat, booking skill —
migrate one at a time afterwards, and each old LLM path is then blocked by a
test rather than merely left uncalled.
"""

from __future__ import annotations

from apps.orchestrator.decision_readiness.candidates import (
    CandidateProbe,
    CandidateSetSignature,
    ProbeUnavailable,
)
from apps.orchestrator.decision_readiness.evidence import (
    CONFIRMABLE_ORIGINS,
    ConfirmedEvidence,
    DecisionRef,
    DomainRef,
    EvidenceOrigin,
    MemoryRef,
    MessageRef,
    ModelSignal,
    confirm,
    from_domain_authority,
    from_promoted_memory,
    from_user_action,
    from_user_text,
)
from apps.orchestrator.decision_readiness.events import (
    SemanticUserEvent,
    Surface,
    UserEventKind,
    user_action_event,
    user_text_event,
)
from apps.orchestrator.decision_readiness.required_context import (
    EMPTY_SPEC,
    Mode,
    PredicateContext,
    RequiredContextSpec,
    RequiredSlot,
    SlotOwner,
    SlotVerdict,
    evaluate_required_context,
    unsatisfied_slots,
    verdict,
)
from apps.orchestrator.decision_readiness.safety_input import (
    KNOWN_SAFETY_STATES,
    SafetyResult,
    SafetyState,
)
from apps.orchestrator.decision_readiness.state import (
    ConversationState,
    LoadResult,
    SlotState,
    SlotValue,
    StateExpiry,
    StateLifecycle,
)

__all__ = [
    "CONFIRMABLE_ORIGINS",
    "EMPTY_SPEC",
    "KNOWN_SAFETY_STATES",
    "CandidateProbe",
    "CandidateSetSignature",
    "ConfirmedEvidence",
    "ConversationState",
    "DecisionRef",
    "DomainRef",
    "EvidenceOrigin",
    "LoadResult",
    "MemoryRef",
    "MessageRef",
    "Mode",
    "ModelSignal",
    "PredicateContext",
    "ProbeUnavailable",
    "RequiredContextSpec",
    "RequiredSlot",
    "SafetyResult",
    "SafetyState",
    "SemanticUserEvent",
    "SlotOwner",
    "SlotState",
    "SlotValue",
    "SlotVerdict",
    "StateExpiry",
    "StateLifecycle",
    "Surface",
    "UserEventKind",
    "confirm",
    "evaluate_required_context",
    "from_domain_authority",
    "from_promoted_memory",
    "from_user_action",
    "from_user_text",
    "unsatisfied_slots",
    "user_action_event",
    "user_text_event",
    "verdict",
]
