"""G4 routing question — [OD-BOT §164], one persisted state for every surface.

The registered contract (owner ruling 18.09, immutable record
``docs/safety/reviews/OWNER_RULINGS_S1_AI_CLINICAL_PRE_REVIEW_2026-09-18.md``):
an ambiguous G4 message («немеет рука иногда», «слабость в правой руке иногда»
— numbness / limb weakness with no sudden onset) gets exactly ONE routing
question; any positive sign or its recent-resolved equivalent → STOP; UNKNOWN →
the restriction stays. No diagnostic questionnaire, no second question, no
clearance by words.

Two states, deliberately separate:

* the CONVERSATIONAL question — :mod:`apps.orchestrator.open_question`, binding,
  B13 two-hour TTL: «the bot knows it asked, and the next reply is this
  question's»;
* the DURABLE restriction — :mod:`apps.orchestrator.safety.s1_restriction`, on
  the BotUser, no TTL: «health-sensitive recommendation / booking are blocked
  until a registered resolution». [§162]: a TTL is never clearance, so the
  question may expire and be put again — the restriction does not move; a
  NEW conversation of the same identity is still restricted.

What every surface calls (chat skill on MAX / Telegram, the global concierge,
the Mini App goal gate):

* :func:`ask_g4` — records the restriction (open) and opens the binding
  question; returns False when the state could NOT be persisted (the caller
  must fail closed);
* :func:`g4_state` — pending question and / or durable restriction;
* :func:`route_g4_reply` — the deterministic outcome of the next reply:
  G6 sign → STOP (G6 keeps precedence), explicit / recent-resolved G4 sign →
  STOP G4, any other red flag → STOP, everything else («не знаю», «нет», a new
  booking intent, an unrelated sentence) → the restriction persists and the
  same single question is put again. A STOP is recorded durably ([§156]);
  a plain «нет» is NOT medical clearance and clears nothing.

Not here, by design: a negative resolution. [§162] allows ``CLEARED_BY_RECHECK``
only through an explicit ``safety_recheck`` with all eight conditions and
provenance; the registered sources give no deterministic contract for such an
answer, and a «нет»-regex would invent one. Owner blocker, named in the PR.

Implementation of registered owner policy does not constitute CLINICAL
APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT. Production wording of the
question is pending physician + Legal ([OD-BOT §164]); the text below is the
registered candidate, verbatim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from apps.orchestrator.open_question import open_question, pending_question, resolve_question
from apps.orchestrator.safety.s1_restriction import (
    S1Restriction,
    mark_stop,
    open_restriction,
    restriction,
)
from apps.skills.health_screening.classifier import (
    PainSignal,
    classify,
    s1_group_of,
)

logger = logging.getLogger(__name__)

#: The one question id every surface binds the next reply to.
G4_QUESTION_ID = "health_screening.g4"

#: [OD-BOT §164] — verbatim. Do not edit; a new owner ruling supersedes it.
G4_ROUTING_QUESTION = (
    "Это началось внезапно, и есть ли сейчас слабость или онемение с одной стороны, "
    "перекос лица, нарушение речи, зрения или равновесия?"
)

OutcomeKind = Literal["stop", "restriction_persists"]


@dataclass(frozen=True)
class G4Outcome:
    """What the reply to the open G4 question means."""

    kind: OutcomeKind
    #: The group the classifier attributes the stop to (``G6`` keeps precedence,
    #: then ``G4``, then G1 / G2 / G3 / G5 / G7 by their named tuples); ``None``
    #: for a red flag of an unnamed older rule — never mislabelled as G4 — and
    #: None while the restriction persists.
    group: str | None = None

    @property
    def stop(self) -> bool:
        return self.kind == "stop"


@dataclass(frozen=True)
class G4State:
    """Pending conversational question + durable restriction, read together."""

    question_pending: bool
    restriction: S1Restriction | None

    @property
    def active(self) -> bool:
        """Something binds the next reply to the screening skill."""

        return self.question_pending or self.restriction is not None

    @property
    def stopped(self) -> bool:
        return self.restriction is not None and self.restriction.status == "stop"


def g4_pending(conversation: Any) -> bool:
    """The conversational G4 question is open (and not expired)."""

    pending = pending_question(conversation)
    return pending is not None and pending.binding and pending.question_id == G4_QUESTION_ID


def g4_state(conversation: Any, bot_user: Any) -> G4State:
    """Conversational question (on the conversation) + durable restriction (on
    the identity), read together."""

    return G4State(question_pending=g4_pending(conversation), restriction=restriction(bot_user))


def ask_g4(conversation: Any, bot_user: Any) -> bool:
    """Record the open restriction on the identity and put the single binding
    question on the conversation.

    Returns True only when BOTH are persisted after the write (re-read); False
    means the state could not be persisted and the caller must not proceed as
    if unrestricted.
    """

    if conversation is None or bot_user is None:
        return False
    restricted = open_restriction(
        bot_user, group="G4", question_id=G4_QUESTION_ID, source="health_screening.g4"
    )
    open_question(conversation, G4_QUESTION_ID, asked_text=G4_ROUTING_QUESTION, binding=True)
    persisted = restricted and g4_pending(conversation)
    if not persisted:
        logger.error(
            "health_screening.g4.ask_not_persisted conversation=%s bot_user=%s "
            "restriction=%s question=%s",
            getattr(conversation, "id", None),
            getattr(bot_user, "pk", None),
            restricted,
            g4_pending(conversation),
        )
    return persisted


def route_g4_reply(conversation: Any, bot_user: Any, text: str) -> G4Outcome:
    """Deterministic outcome of ``text`` as the reply to the open G4 question.

    The crisis route is not decided here: ``evaluate_inbound`` runs before
    any of this on every surface, so a crisis phrase never reaches it.
    """

    if classify(text) is PainSignal.RED_FLAG:
        group = s1_group_of(text)
        reason = {"G6": "g6_sign", "G4": "positive_sign"}.get(group or "", "red_flag")
        outcome = G4Outcome("stop", group)
    else:
        outcome, reason = G4Outcome("restriction_persists"), "unknown"

    if outcome.stop:
        # [§156]: the STOP is durable — the question is answered, the
        # restriction is not lifted. Nothing here is clearance. The group is
        # the classifier's attribution or None — never a default of G4.
        resolve_question(conversation, G4_QUESTION_ID, text)
        mark_stop(
            bot_user,
            group=outcome.group,
            question_id=G4_QUESTION_ID,
            source="health_screening.g4",
            reason=reason,
        )
    else:
        # Same question, same slot — the conversational record is re-stamped
        # (or re-opened after its TTL / in a new conversation); the durable
        # restriction was never gone.
        ask_g4(conversation, bot_user)
    logger.info(
        "health_screening.g4.reply conversation=%s outcome=%s group=%s",
        getattr(conversation, "id", None),
        outcome.kind,
        outcome.group,
    )
    return outcome


__all__ = [
    "G4_QUESTION_ID",
    "G4_ROUTING_QUESTION",
    "G4Outcome",
    "G4State",
    "ask_g4",
    "g4_pending",
    "g4_state",
    "route_g4_reply",
]
