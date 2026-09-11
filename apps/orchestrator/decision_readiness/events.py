"""What the PERSON did on this turn — text or tap — and nothing the model wrote.

### Why this type exists at all

`apps/orchestrator/nutrition_global.py:262-286` is the live shape of the defect
this module exists to make impossible:

    arg_key = {"health_screening": "symptom_text", ...}[name]
    text = str(args.get(arg_key) or "").strip()
    ...
    context = _build_context(message_text=text, ...)

`args` are the arguments of a tool call **the model composed**. They are placed
into `message_text` — a field whose name, and every other caller, means "what
the human said". From that point on nothing downstream can tell the two apart:
the skill's veto checks the model's paraphrase against the model's classifier.
That is the mechanism behind DRF-1542 (the same screen five times in a row),
and it is what the canon calls an EVIDENCE ORIGIN violation.

The fix is not a flag on a shared object. A flag can be set wrongly. This is a
separate type that a model-authored string cannot become: there is no
constructor here that takes a tool-call argument, and the two constructors that
do exist each demand a reference only their own channel can produce — a
`message_id` the transport assigned to a human message, or the
`(decision_id, option_id)` pair the surface assigned to a button it rendered.

### Tap and text are the same person, and stay distinguishable

Inventory 10.09 (`LANE_E_INVENTORY_ASK_VS_ACT_2026-09-10.md` §7 п.3) recorded a
live hazard: `apps/channels/max_bot/quick_actions.py:416-472` substitutes a tap
into the text pipeline as if the person had typed the button's caption. That
substitution is convenient and it erases provenance — afterwards nobody can say
whether a slot was closed by a choice from a closed set or by free prose that
happened to match.

So the two kinds are enforced disjoint here (`__post_init__`): an ACTION event
carries `decision_id` + `option_id` and **no** `raw_text`; a TEXT event carries
`raw_text` + `message_id` and **no** decision reference. Substituted text cannot
present itself as a tap, and a tap cannot present itself as prose.

This is deliberately *not* the same axis as question identity. One question
asked in chat and answered by tapping a button has ONE `question_id`
(spec §13.2, SURFACE-INDEPENDENT) — the answer's provenance differs, the
question's identity does not. See `questions.py`.

Spec: `docs/specs/DECISION_READINESS_ENGINE_v1.0.md` §3.1, §6, §14.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class UserEventKind(str, Enum):
    """How the person expressed themselves on this turn.

    Two members, not three: there is no "model said it for them". A third
    member is the readable form of the very substitution §3.1 forbids.
    """

    TEXT = "text"
    ACTION = "action"


class Surface(str, Enum):
    """Where the turn happened. Audit only — never an input to a decision.

    Spec §4 marks `surface` "(только для аудита)" and §13.2 keeps it out of
    `question_id` on purpose: an answer given in the Mini App closes the same
    question in the MAX chat, and the reverse. A surface that reached the
    decision would quietly re-open surface-local copies of one question.
    """

    MAX_CHAT = "max_chat"
    MINIAPP = "miniapp"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SemanticUserEvent:
    """One thing the person did, addressed by the revision it produced.

    `revision` is the `state_revision` of the ConversationState **after** this
    event was applied (`state.py`). It is what makes a confirmation orderable:
    `confirm(signal, event)` (see `evidence.py`) requires
    `event.revision > signal.produced_at_revision`, so a model guess can never
    be "confirmed" by an event that predates it.

    `raw_text` is the veto surface named by spec §14: a skill that wants to
    check what the person actually wrote reads THIS field, never a tool-call
    argument.
    """

    event_id: str
    conversation_id: str
    revision: int
    kind: UserEventKind
    observed_at: datetime
    surface: Surface = Surface.UNKNOWN
    raw_text: str | None = None
    message_id: str | None = None
    decision_id: str | None = None
    option_id: str | None = None

    def __post_init__(self) -> None:
        if self.revision < 1:
            # Revisions start at 1 (`state.py`). Zero would make "before any
            # event" and "produced by the first event" the same number, and
            # `confirm()` compares with a strict `>`.
            raise ValueError(f"SemanticUserEvent.revision must be >= 1, got {self.revision!r}")

        if self.kind is UserEventKind.TEXT:
            if not self.message_id:
                raise ValueError("TEXT event requires message_id — the transport's own reference")
            if self.raw_text is None:
                raise ValueError("TEXT event requires raw_text (may be empty string, not None)")
            if self.decision_id is not None or self.option_id is not None:
                raise ValueError(
                    "TEXT event must not carry (decision_id, option_id): substituted button "
                    "text presenting itself as a tap is the provenance loss of "
                    "quick_actions.py:416-472"
                )
        elif self.kind is UserEventKind.ACTION:
            if not self.decision_id or not self.option_id:
                raise ValueError("ACTION event requires both decision_id and option_id")
            if self.raw_text is not None:
                raise ValueError(
                    "ACTION event must not carry raw_text: a tap answers from a closed set, "
                    "and prose that merely matches a caption is not that answer"
                )
        else:  # pragma: no cover - enum is closed, guarded for future members
            raise ValueError(f"unknown UserEventKind: {self.kind!r}")


def user_text_event(
    *,
    event_id: str,
    conversation_id: str,
    revision: int,
    message_id: str,
    raw_text: str,
    observed_at: datetime,
    surface: Surface = Surface.UNKNOWN,
) -> SemanticUserEvent:
    """Build the TEXT event. `message_id` is the transport's, never the model's."""

    return SemanticUserEvent(
        event_id=event_id,
        conversation_id=conversation_id,
        revision=revision,
        kind=UserEventKind.TEXT,
        observed_at=observed_at,
        surface=surface,
        raw_text=raw_text,
        message_id=message_id,
    )


def user_action_event(
    *,
    event_id: str,
    conversation_id: str,
    revision: int,
    decision_id: str,
    option_id: str,
    observed_at: datetime,
    surface: Surface = Surface.UNKNOWN,
) -> SemanticUserEvent:
    """Build the ACTION event from a decision the surface itself rendered.

    `decision_id` + `option_id` are canon §13.1 identifiers: the surface only
    ever renders options the engine handed it, so this pair cannot be minted by
    anything that did not first receive a `NextBestQuestion`.
    """

    return SemanticUserEvent(
        event_id=event_id,
        conversation_id=conversation_id,
        revision=revision,
        kind=UserEventKind.ACTION,
        observed_at=observed_at,
        surface=surface,
        decision_id=decision_id,
        option_id=option_id,
    )
