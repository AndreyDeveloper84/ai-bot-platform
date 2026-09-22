"""G7 question — [OD-BOT §170], one router for every surface.

The registered owner contract (immutable record
``docs/safety/reviews/OWNER_RULINGS_S1_G7_QUESTION_CONTRACT_2026-09-21.md``,
RECORD SHA-256 ``b4f2f3f11045f5527b2c16f616bf4116e15c682ab309e54ccbea1d1f7c3597d9``):

* G7 is a FALLBACK after G1–G6 (решение 1) — the classifier reads it last;
* an ambiguous G7 message gets exactly ONE question, ``health_screening.g7``,
  with the exact text below (решение 2); an explicit sign is STOP without it;
* exactly THREE structured answers (решение 3); free text — «нет», «прошло»,
  «сейчас нормально», a new topic — is none of them and clears nothing;
* the answer does not always lift a restriction (решение 4): on the first
  ambiguous turn with no restriction on record, answer №2 means «G7 not
  confirmed» and NO restriction is created; with a restriction already active
  answer №2 changes nothing here — outside ``safety_recheck.start`` it is not a
  recheck, and Package B does not exist;
* a real severe episode that has passed stays STOP (решение 5).

Unlike G4 (:mod:`apps.skills.health_screening.g4_question`) the question does
NOT open a durable restriction: the owner ruled the first ambiguous turn must
not create one. What blocks booking / recommendation while it is open is the
binding question itself (:mod:`apps.orchestrator.open_question`, B13 two-hour
TTL). Consequence, named: when the TTL passes with no answer the question reads
as «not asked» and the flow is open again — the framework's TTL is not
extended here (prompt §12, owner policy).

Structured answers are callbacks ``cb:s1g7:<answer>:<token>``. The token is the
slot's one-time token (:attr:`OpenQuestion.token`): a tap from another user's,
another tenant's, an expired or an already resolved slot does not match, and a
string typed by hand without the live token is free text. No NLP guessing of
which button was meant.

Implementation of the registered G7 owner contract does not constitute
CLINICAL APPROVED, PHYSICIAN PASS, or SAFE FOR PILOT. Production wording of the
question and the labels is pending physician + Legal; the text below is the
registered owner text, verbatim.
"""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any, Literal

from apps.orchestrator.open_question import (
    OpenQuestion,
    open_question,
    pending_question,
    resolve_question,
)
from apps.orchestrator.safety.s1_restriction import mark_stop, restriction
from apps.skills.health_screening.classifier import PainSignal, classify, s1_group_of

logger = logging.getLogger(__name__)

#: The one question id every surface binds the next reply to ([OD-BOT §170] решение 2).
G7_QUESTION_ID = "health_screening.g7"

#: [OD-BOT §170] решение 2 — verbatim. Do not edit; a new owner ruling supersedes it.
G7_QUESTION_TEXT = (
    "Сейчас есть хотя бы один из признаков: кажется, что вы вот-вот потеряете сознание; "
    "трудно самостоятельно стоять, говорить или дышать; появилась спутанность; "
    "состояние быстро ухудшается?"
)

#: The three structured answers ([OD-BOT §170] решение 3) — stable action values.
ANSWER_YES = "yes"
ANSWER_NO = "no"
ANSWER_UNSURE = "unsure"
ANSWERS: tuple[str, ...] = (ANSWER_YES, ANSWER_NO, ANSWER_UNSURE)

#: The user-facing labels — verbatim from [OD-BOT §170] решение 3.
ANSWER_LABELS: dict[str, str] = {
    ANSWER_YES: "Да, есть хотя бы один признак",
    ANSWER_NO: "Нет — этих признаков не было и сейчас нет, состояние не ухудшается",
    ANSWER_UNSURE: "Не уверен(а) или не могу ответить",
}

#: Callback family. Own prefix — ``cb:health:`` is the nutrition-consent menu.
CALLBACK_PREFIX = "cb:s1g7:"

#: A live tap is exactly ``cb:s1g7:<answer>:<12 hex>`` — by FORM, not by prefix.
_CALLBACK_RE = re.compile(r"^cb:s1g7:(yes|no|unsure):([0-9a-f]{12})$")

#: Owner-approved (decisions on PR #1982, 22.09), verbatim. Shown ONLY when all
#: hold: the first ambiguous G7 turn, a REAL structured action №2 with the live
#: slot token, no active S1 restriction, no other S1 group. Never on an active
#: restriction, another S1 group or an unstructured answer. It is not medical
#: clearance and not ``CLEARED_BY_RECHECK``.
OUTSIDE_S1_G7_ACK = "Спасибо, что уточнили. Чем могу помочь дальше?"

OutcomeKind = Literal["stop", "outside_s1_g7", "unknown", "restriction_persists"]


@dataclass(frozen=True)
class G7Outcome:
    """What a turn means while the G7 question is open."""

    kind: OutcomeKind
    #: The group a STOP is attributed to (``G7`` for answer №1; the classifier's
    #: group for an explicit sign of another group); ``None`` otherwise.
    group: str | None = None
    #: Why — a routing reason, never the person's words.
    reason: str = ""

    @property
    def stop(self) -> bool:
        return self.kind == "stop"


def g7_pending(conversation: Any) -> OpenQuestion | None:
    """The open binding G7 question (not expired), or None."""

    pending = pending_question(conversation)
    if pending is None or not pending.binding or pending.question_id != G7_QUESTION_ID:
        return None
    return pending


def ask_g7(conversation: Any) -> str | None:
    """Open the ONE binding G7 question; returns its slot token, or None.

    No durable restriction is written ([OD-BOT §170] решение 4). A repeated
    ambiguous message re-stamps the same slot and keeps its token — never a
    second slot. None means the question could not be persisted: the caller
    must not proceed as if nothing were pending (fail closed).
    """

    if conversation is None:
        return None
    current = g7_pending(conversation)
    token = current.token if current is not None and current.token else secrets.token_hex(6)
    open_question(
        conversation, G7_QUESTION_ID, asked_text=G7_QUESTION_TEXT, binding=True, token=token
    )
    after = g7_pending(conversation)
    if after is None or not after.token:
        logger.error(
            "health_screening.g7.ask_not_persisted conversation=%s",
            getattr(conversation, "id", None),
        )
        return None
    return after.token


def g7_callback(answer: str, token: str) -> str:
    """The callback payload of one structured answer."""

    if answer not in ANSWERS:
        raise ValueError(answer)
    return f"{CALLBACK_PREFIX}{answer}:{token}"


def g7_buttons(token: str) -> list[dict[str, str]]:
    """The three answers as channel-agnostic ``[{label, callback}]`` buttons."""

    return [{"label": ANSWER_LABELS[a], "callback": g7_callback(a, token)} for a in ANSWERS]


def g7_action_data(token: str) -> dict[str, Any]:
    """``action_data`` for a reply that carries the question.

    The platform-canonical envelope — the one shape BOTH adapters read
    (``telegram.handler._extract_keyboard`` reads only this one; MAX
    ``_build_attachments`` reads it first). One button per row: the labels are
    long sentences.
    """

    return {
        "attachments": [{"type": "inline_keyboard", "payload": {"buttons": g7_buttons(token)}}],
        "question_id": G7_QUESTION_ID,
    }


def buttons_of(action_data: dict[str, Any] | None) -> list[dict[str, str]]:
    """The G7 buttons inside an ``action_data`` built by :func:`g7_action_data`."""

    for attachment in (action_data or {}).get("attachments") or []:
        if isinstance(attachment, dict) and attachment.get("type") == "inline_keyboard":
            return list((attachment.get("payload") or {}).get("buttons") or [])
    return []


def is_g7_callback(text: str) -> bool:
    """True for a string of the G7 callback FORM (tap or forgery alike)."""

    return bool(_CALLBACK_RE.match((text or "").strip()))


def parse_g7_callback(text: str) -> tuple[str, str] | None:
    """``(answer, token)`` of a G7 callback, or None when ``text`` is not one."""

    match = _CALLBACK_RE.match((text or "").strip())
    if match is None:
        return None
    return match.group(1), match.group(2)


def _record_stop(bot_user: Any, group: str | None, reason: str) -> None:
    persisted = mark_stop(
        bot_user, group=group, question_id=G7_QUESTION_ID, source=G7_QUESTION_ID, reason=reason
    )
    if not persisted:
        logger.error(
            "health_screening.g7.stop_not_persisted bot_user=%s", getattr(bot_user, "pk", None)
        )


def live_g7_answer(conversation: Any, text: str) -> str | None:
    """The answer of a tap bound to THIS conversation's open slot, or None."""

    parsed = parse_g7_callback(text)
    pending = g7_pending(conversation)
    if parsed is None or pending is None or parsed[1] != pending.token:
        return None
    return parsed[0]


def is_stale_g7_tap(conversation: Any, bot_user: Any, text: str) -> bool:
    """A G7 tap with nothing to answer: no open G7 question and no S1
    restriction on record (a duplicate delivery, an old keyboard, a forgery).

    Owner decisions on PR #1982: an action is accepted ONLY with the live slot
    token, and the «Нет» acknowledgement is shown only for a real answer. Such a
    tap is therefore a no-op — no state change, no text: idempotent. With a
    restriction on record the tap is NOT stale: the durable reply stands.
    """

    return (
        is_g7_callback(text) and g7_pending(conversation) is None and restriction(bot_user) is None
    )


def history_text(text: str) -> str | None:
    """What a G7 tap was as a reply — its verbatim label — for the dialog
    history, never the raw ``cb:`` payload. None when ``text`` is not a tap."""

    parsed = parse_g7_callback(text)
    return ANSWER_LABELS[parsed[0]] if parsed is not None else None


def g7_under_mute(conversation: Any, bot_user: Any, text: str, outcome: Any) -> Any:
    """The inbound verdict while the bot is muted (operator handoff or a
    platform block, DRF-2213 / DRF-2276), with the G7 question honoured.

    A live «Да, есть хотя бы один признак» tap is a medical emergency: it
    reaches the person through the mute exactly like the gate's ``MEDICAL``
    verdict (owner decision N-1, CD §67 — «кризис и неотложка получают
    детерминированный ответ всегда»). The durable G7 STOP is recorded and the
    question resolved here, because no skill runs under the mute. Any other
    turn — the two other answers included — keeps the verdict it had.
    """

    if not getattr(outcome, "allowed", False):
        return outcome
    if live_g7_answer(conversation, text) != ANSWER_YES:
        return outcome
    from apps.orchestrator.safety.gate import SafetyGateOutcome
    from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
    from apps.orchestrator.safety.pre_check import SafetyResult, SafetyVerdict

    _record_stop(bot_user, "G7", "g7_answer_yes_under_mute")
    resolve_question(conversation, G7_QUESTION_ID, ANSWER_YES)
    reason = "health_screening_g7_answer_yes_under_mute"
    return SafetyGateOutcome(
        allowed=False,
        verdict=SafetyVerdict.MEDICAL.value,
        reply_text=MEDICAL_EMERGENCY_TEXT_V2,
        reason=reason,
        result=SafetyResult(verdict=SafetyVerdict.MEDICAL, reason=reason),
    )


def route_g7_turn(conversation: Any, bot_user: Any, text: str) -> G7Outcome:
    """Deterministic outcome of ``text`` while the G7 question is open.

    The crisis route is not decided here: ``evaluate_inbound`` runs before any
    of this on every surface, so a crisis phrase never reaches it.
    """

    pending = g7_pending(conversation)
    # 1. An explicit S1 sign of any group in the turn is STOP with its own label
    #    — a free-text reply cannot hide it, and G1–G6 keep precedence.
    if not is_g7_callback(text) and classify(text) is PainSignal.RED_FLAG:
        group = s1_group_of(text)
        _record_stop(bot_user, group, "red_flag")
        resolve_question(conversation, G7_QUESTION_ID, "red_flag")
        return G7Outcome("stop", group, "red_flag")

    parsed = parse_g7_callback(text)
    live = parsed is not None and pending is not None and parsed[1] == pending.token
    if parsed is not None and not live:
        # a stale, resolved, another slot's or a forged token: never an answer
        logger.info(
            "health_screening.g7.token_mismatch conversation=%s pending=%s",
            getattr(conversation, "id", None),
            pending is not None,
        )
    if not live:
        return G7Outcome("unknown", None, "free_text" if parsed is None else "stale_token")

    assert parsed is not None
    answer = parsed[0]
    if answer == ANSWER_YES:
        _record_stop(bot_user, "G7", "g7_answer_yes")
        resolve_question(conversation, G7_QUESTION_ID, ANSWER_YES)
        return G7Outcome("stop", "G7", "g7_answer_yes")
    if answer == ANSWER_UNSURE:
        # UNKNOWN: the question stays open in the same slot ([OD-BOT §170] решение 3)
        return G7Outcome("unknown", None, "g7_answer_unsure")
    # ANSWER_NO
    if restriction(bot_user) is not None:
        # [OD-BOT §170] решение 4: not a recheck outside safety_recheck.start —
        # nothing is lifted, nothing is lowered, clear_restriction() is not called.
        resolve_question(conversation, G7_QUESTION_ID, ANSWER_NO)
        return G7Outcome("restriction_persists", None, "g7_answer_no_restriction_active")
    resolve_question(conversation, G7_QUESTION_ID, ANSWER_NO)
    return G7Outcome("outside_s1_g7", None, "g7_answer_no")


__all__ = [
    "ANSWERS",
    "ANSWER_LABELS",
    "ANSWER_NO",
    "ANSWER_UNSURE",
    "ANSWER_YES",
    "CALLBACK_PREFIX",
    "G7Outcome",
    "G7_QUESTION_ID",
    "G7_QUESTION_TEXT",
    "OUTSIDE_S1_G7_ACK",
    "ask_g7",
    "buttons_of",
    "g7_action_data",
    "g7_buttons",
    "g7_callback",
    "g7_pending",
    "g7_under_mute",
    "history_text",
    "is_g7_callback",
    "is_stale_g7_tap",
    "live_g7_answer",
    "parse_g7_callback",
    "route_g7_turn",
]
