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

if TYPE_CHECKING:
    from apps.conversations.models import Conversation
    from apps.identity.models import BotUser


@dataclass(frozen=True)
class SkillContext:
    """The read-only context a skill receives at dispatch time.

    Frozen because skills must not mutate the world via context-side
    effects — they communicate via :class:`SkillResult`.

    ``has_attachments`` carries the boolean fact only — the skill
    layer doesn't peek at individual attachment payloads in Sprint 3
    (channel adapter handles them); the boolean lets the echo skill
    pick the "no echo" fallback for attachment-only turns.
    """

    conversation: "Conversation"
    bot_user: "BotUser"
    message_text: str
    trace_id: str = ""
    has_attachments: bool = False


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
    """

    reply_text: str = ""
    action_type: str = ""
    action_data: dict[str, Any] | None = None
    should_send: bool = True
    should_close_conversation: bool = False
    new_state: str | None = None
    # Free-form skill-metadata bag for logging / events. Not persisted.
    meta: dict[str, Any] = field(default_factory=dict)


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
