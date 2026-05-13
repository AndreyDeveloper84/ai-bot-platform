"""Skill base types (DRF-468 / Sprint 3 / D1).

A **skill** is the unit of bot behavior in Phase 0 / Sprint 3+. Each
skill knows two things:

  * ``matches(context)`` — is this skill the right responder for the
    incoming message?
  * ``handle(context)`` — produce a :class:`SkillResult` describing
    what the bot should say and do.

The dispatcher (D1 registry) walks an ordered list of skills and
returns the first match. Per Sprint 3 locked decision, matching is
keyword/phrase-based (no AI intent classifier yet). Sprint 4+ adds
the classifier without changing the skill protocol — skills stay
agnostic to how they were selected.

### SkillResult side-effects

``handle()`` returns a SkillResult; the caller (dispatcher) is
responsible for the side effects:

  * ``reply_text`` — non-empty string the channel adapter sends.
  * ``action_type`` / ``action_data`` — optional structured action
    that the channel adapter renders (Sprint 4+ uses for inline
    keyboards).
  * ``should_send`` — set False when a skill writes its reply through
    a different channel (rare: the handoff skill already handles
    operator notification, the user-facing reply goes through normal
    channel send).
  * ``should_close_conversation`` — flag for the caller to close the
    Conversation row (e.g. "done, bye" interactions in later sprints).
  * ``new_state`` — explicit state transition request. Caller flips
    ``conversation.state`` if non-None.

### Why a Protocol, not an ABC

Skills live across modules (consent, handoff, echo, plus future
third-party in Phase 1). Duck-typing via Protocol means a skill
class doesn't need a base-class import to qualify — only the right
shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

from apps.llm.protocol import ToolCall

if TYPE_CHECKING:
    from apps.conversations.models import Conversation
    from apps.identity.models import BotUser
    from apps.orchestrator.intent_router import IntentDecision


@dataclass(frozen=True)
class SkillContext:
    """The read-only context a skill receives at dispatch time.

    Frozen because skills must not mutate the world via context-side
    effects — they communicate via :class:`SkillResult`.

    ``has_attachments`` carries the boolean fact only — the skill
    layer doesn't peek at individual attachment payloads in Sprint 3
    (channel adapter handles them); the boolean lets the echo skill
    pick the "no echo" fallback for attachment-only turns.

    ``intent`` is the pipeline's step-6 :class:`IntentDecision` for
    this turn (Sprint 6 / DRF-536). Optional for backward-compat with
    Sprint 3 skills (privacy/handoff/echo) that don't read it; required
    in spirit for Sprint 7+ skills (FAQ/booking) that branch on
    ``intent.intent`` and ``intent.needs_rag``.
    """

    conversation: "Conversation"
    bot_user: "BotUser"
    message_text: str
    trace_id: str = ""
    has_attachments: bool = False
    intent: "IntentDecision | None" = None


@dataclass
class SkillResult:
    """What the dispatcher does after a skill's ``handle()`` returns.

    Attributes:
      reply_text: text the channel adapter sends. Empty string is
                  allowed but only when ``should_send=False``.
      action_type: optional structured action label (Sprint 4+).
      action_data: optional structured action payload (Sprint 4+).
      should_send: True (default) → caller sends ``reply_text`` to the
                   user. False → skill already handled outbound itself.
      should_close_conversation: True → caller marks Conversation
                                 inactive after the handle.
      new_state: optional explicit state transition. Caller flips
                 ``conversation.state`` to this value when not None.
      should_handoff: skill is requesting post-dispatch handoff to a
                      human. Pipeline step 10.5 (Sprint 7 / O2) catches
                      this, creates the AdminTask, and short-circuits
                      with the canned handoff reply. The skill MAY also
                      set ``reply_text`` to a softer "переключаю на
                      менеджера…" line — pipeline replaces it with the
                      canned fallback if empty.
      handoff_reason: short slug for AdminTask + observability when
                      ``should_handoff`` is True. Examples:
                      ``"faq_low_confidence"``, ``"booking_unknown_master"``.
      tool_calls_made: function-calls the skill actually invoked during
                       ``handle()``. Persisted on the assistant Message
                       row for replay + audit. Empty for non-tool-using
                       skills (privacy/handoff/echo).
      confidence: skill's self-reported confidence in [0.0, 1.0]. Used
                  by O2 step 10.5 to gate auto-handoff thresholds.
                  ``None`` = skill didn't compute a score (default for
                  Sprint 3 skills that always reply deterministically).
    """

    reply_text: str = ""
    action_type: str = ""
    action_data: dict[str, Any] | None = None
    should_send: bool = True
    should_close_conversation: bool = False
    new_state: str | None = None
    # Free-form skill-metadata bag for logging / events. Not persisted.
    meta: dict[str, Any] = field(default_factory=dict)
    # Sprint 7 / O1 (DRF-559) contract extension — KB-driven skills.
    should_handoff: bool = False
    handoff_reason: str = ""
    tool_calls_made: list[ToolCall] = field(default_factory=list)
    confidence: float | None = None


@runtime_checkable
class Skill(Protocol):
    """Duck-typed skill protocol.

    Implementations expose ``name`` (stable identifier used in events
    + logs), ``matches`` (cheap predicate over the context), and
    ``handle`` (the actual work).
    """

    name: ClassVar[str]

    def matches(self, context: SkillContext) -> bool:  # pragma: no cover - Protocol
        ...

    def handle(self, context: SkillContext) -> SkillResult:  # pragma: no cover - Protocol
        ...
