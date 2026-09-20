"""G4 routing question — [OD-BOT §164], one persisted state for every surface.

The registered contract (owner ruling 18.09, immutable record
``docs/safety/reviews/OWNER_RULINGS_S1_AI_CLINICAL_PRE_REVIEW_2026-09-18.md``):
an ambiguous G4 message («немеет рука иногда» — numbness / weakness with no
sudden onset and no side) gets exactly ONE routing question; any positive
sign or its recent-resolved equivalent → STOP; UNKNOWN → the restriction
stays. No diagnostic questionnaire, no second question, no clearance by
words.

What this module owns, and every surface calls (chat skill on MAX /
Telegram, the global concierge, the Mini App goal gate):

* :func:`ask_g4` — opens the BINDING question ``health_screening.g4`` on the
  conversation (:mod:`apps.orchestrator.open_question`); re-asking is
  idempotent (one slot).
* :func:`g4_pending` — is that question open and fresh.
* :func:`route_g4_reply` — the deterministic outcome of the next reply:
  G6 sign → STOP (G6 keeps precedence), explicit / recent-resolved G4 sign →
  STOP G4, any other red flag → STOP, everything else («не знаю», «нет»,
  a new booking intent, an unrelated sentence) → the restriction persists
  and the same single question is put again. A plain «нет» is NOT medical
  clearance: the registered contract has no negative-answer outcome for G4,
  so nothing here turns it into one.

The state is the conversation's — the Mini App reaches it through
:func:`apps.conversations.services.resolve_conversation_for_bot_user`. The
restriction lives as long as the open question (B13 two-hour context TTL);
no longer span exists in the registered decisions.

Implementation of registered owner policy does not constitute CLINICAL
APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT. Production wording of the
question is pending physician + Legal ([OD-BOT §164]); the text below is
the registered candidate, verbatim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from apps.orchestrator.open_question import open_question, pending_question, resolve_question
from apps.skills.health_screening.classifier import (
    PainSignal,
    classify,
    detect_g4,
    detect_g6,
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
    #: ``"G4"`` / ``"G6"`` for an attributed stop, None for an unattributed
    #: red flag of the older rules, None while the restriction persists.
    group: str | None = None

    @property
    def stop(self) -> bool:
        return self.kind == "stop"


def g4_pending(conversation: Any) -> bool:
    """The G4 question is open on this conversation (and not expired)."""

    pending = pending_question(conversation)
    return pending is not None and pending.binding and pending.question_id == G4_QUESTION_ID


def ask_g4(conversation: Any) -> None:
    """Open (or re-stamp) the single binding G4 question. Never raises."""

    open_question(conversation, G4_QUESTION_ID, asked_text=G4_ROUTING_QUESTION, binding=True)


def route_g4_reply(conversation: Any, text: str) -> G4Outcome:
    """Deterministic outcome of ``text`` as the reply to the open G4 question.

    The crisis route is not decided here: ``evaluate_inbound`` runs before
    any of this on every surface, so a crisis phrase never reaches it.
    """

    if detect_g6(text):
        outcome = G4Outcome("stop", "G6")
    elif detect_g4(text):
        outcome = G4Outcome("stop", "G4")
    elif classify(text) is PainSignal.RED_FLAG:
        outcome = G4Outcome("stop", None)
    else:
        outcome = G4Outcome("restriction_persists")

    if outcome.stop:
        resolve_question(conversation, G4_QUESTION_ID, text)
    else:
        # Same question, same slot — the time stamp moves, nothing else.
        ask_g4(conversation)
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
    "ask_g4",
    "g4_pending",
    "route_g4_reply",
]
