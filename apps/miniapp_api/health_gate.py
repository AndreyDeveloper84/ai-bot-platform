"""Safety entry on the free text of the goal anketa (DRF-1763, C02-M7).

Until this module «болит спина после работы» typed into the Mini App goal
screen became ``ClientGoal.goal_text`` verbatim: the proxy
:func:`apps.miniapp_api.views.customer_goal_select` forwarded the body to
Ayla with no safety call on the path (grep ``pre_check|evaluate_inbound|
health_screening`` over ``miniapp_api/`` — 0 before this ticket). In the
chat the same sentence meets two gates — :func:`evaluate_inbound` and the
``health_screening`` skill — before any model or catalog sees it.

Owner ruling 18.09 (§48 п.6, verbatim): «D1-B: CLARIFY означает реальный
вопрос пользователю, а не разрешение продолжить». So a health signal in
the goal text does not let the write through with a softer wording — it
STOPS the flow: the goal is not created, the document is not changed, the
person is shown an acknowledgement and the clarifying questions, and only
their answer (classified, never stored) reopens the write. D7 (same
ruling): the signal set is the one the chat already has — the seven S1
groups of :mod:`apps.orchestrator.safety.pre_check` plus the soft-pain
classifier of :mod:`apps.skills.health_screening`. No new matrix, no new
pattern lives here.

### What this module does and does not own

It CALLS the safety producers and consumes their verdicts unchanged:

* :func:`apps.orchestrator.safety.gate.evaluate_inbound` — ``HANDOFF`` →
  the founder-approved crisis reply, ``BLOCK`` → the block reply. Same
  text as the chat, same source constant.
* :func:`apps.skills.health_screening.classifier.classify` — ``RED_FLAG``
  → the chat's medical emergency text v2 ([OD-BOT §163], one canonical
  constant); ``SOFT`` → the acknowledgement +
  the chat's two diagnostic-first questions.
* ``pre_check`` ``CLARIFY`` (the vague-medical bucket the inbound gate
  deliberately lets through in chat) → the same acknowledgement + questions.

It owns nothing about the verdicts. ``gate.py:145`` (CLARIFY → allowed in
the chat) is the identity window's module and is not touched here.

### The answer is classified, never stored

The person's answer to the clarifying questions arrives as
``safety_answer`` next to the ORIGINAL body. It is run through the hard
stops (crisis / block / red flag) and then discarded: it is not forwarded
to Ayla, not written locally, not logged. Special-category data has no
consent scope on this surface yet (D4, DRF-1750); the only thing this
module may do with the answer is refuse on it.

``safety_answer`` alone is not clearance. Without the «asked» memo the
flag is ignored and the text is screened as if it had arrived fresh — a
client cannot skip the question by claiming it was answered.

### Two memos, one limit

The chat keeps «questions asked» on ``Conversation.skill_state``
(:mod:`apps.skills.health_screening.memo`); this surface has no
conversation row and keeps it in the Django cache keyed by the BotUser,
with the same TTL. They are NOT merged in this ticket: a person who
answered the questions in the chat and then opens the Mini App will be
asked again. Named as a limit, not fixed here.

Unlike the chat memo, «asked» here is not enough to let a repeat through
— only «answered» is. In the chat the answer goes to the model, which
continues the conversation; here nothing continues the flow but the
person's answer, so «asked and abandoned» must ask again (D1-B).

### After a clean answer

The ORIGINAL ``goal_text`` («болит спина после работы») is forwarded to
Ayla and becomes ``ClientGoal.goal_text`` — the current contract, and
what the chat does after its two questions. Owner decision 18.09,
verbatim: «оставь как в чате» — the wording of the goal is not rewritten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from django.core.cache import cache

from apps.orchestrator.safety.gate import evaluate_inbound
from apps.orchestrator.safety.pre_check import SafetyVerdict
from apps.skills.health_screening.classifier import PainSignal, classify
from apps.skills.health_screening.g4_question import (
    G4_ROUTING_QUESTION,
    ask_g4,
    g4_state,
    route_g4_reply,
)
from apps.skills.health_screening.memo import STATE_TTL_SECONDS
from apps.skills.health_screening.skill import RED_FLAG_REPLY

logger = logging.getLogger(__name__)

#: Mockup copy (GAP_MAP_C02 Q12), shared with the Mini App word for word —
#: ``apps/miniapp/src/lib/health-gate-copy.ts`` re-states it and a parity
#: test pins the two. The chat's ``SOFT_PAIN_REPLY`` opens with its own
#: «Понимаю. Уточню…»; giving the chat this line too is the skill owner's
#: move, not this ticket's.
HEALTH_ACKNOWLEDGEMENT_COPY = (
    "Я поняла, что здесь есть вопрос самочувствия. "
    "Сначала уточню несколько вещей, чтобы не предложить неподходящий вариант."
)

#: The chat's two diagnostic-first questions (D7 — «как в чате»), one per
#: line so the screen can render them as a list. A parity test pins each
#: line to :data:`SOFT_PAIN_REPLY` so the surfaces cannot drift apart.
HEALTH_CLARIFY_QUESTIONS: tuple[str, ...] = (
    "Где именно болит — конкретное место?",
    "Это после нагрузки / сидячей работы или с утра после сна?",
)

#: Body key the Mini App uses to carry the person's answer next to the
#: original body. Stripped before anything is forwarded.
SAFETY_ANSWER_FIELD = "safety_answer"

#: Same lifetime as the chat memo — the span of one conversation.
ASKED_TTL_SECONDS = STATE_TTL_SECONDS

KIND_CRISIS = "crisis"
KIND_BLOCK = "block"
KIND_RED_FLAG = "health_red_flag"
KIND_CLARIFY = "health_clarify"
#: The safety state could not be read or written (carrier lookup / creation /
#: persistence failure). Fail-closed: the request is blocked with this frame and
#: nothing is forwarded. Infrastructure copy, not clinical wording.
KIND_STATE_UNAVAILABLE = "safety_state_unavailable"
STATE_UNAVAILABLE_TEXT = (
    "Сейчас не получается проверить безопасность запроса. Попробуй ещё раз чуть позже."
)

_MEMO_ASKED = "asked"
_MEMO_ANSWERED = "answered"


@dataclass(frozen=True)
class SafetyStop:
    """Why the write did not happen, in the shape the Mini App renders.

    ``text`` is the whole reply for the three hard stops; ``acknowledgement``
    + ``questions`` are the clarify frame. Never both.
    """

    kind: str
    text: str = ""
    acknowledgement: str = ""
    questions: tuple[str, ...] = field(default_factory=tuple)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.text:
            payload["text"] = self.text
        if self.acknowledgement:
            payload["acknowledgement"] = self.acknowledgement
        if self.acknowledgement or self.questions:
            # The G4 frame ([OD-BOT §164]) is one question with no
            # acknowledgement line; the screen lists ``questions`` on its own.
            payload["questions"] = list(self.questions)
        return payload


_CLARIFY_STOP = SafetyStop(
    kind=KIND_CLARIFY,
    acknowledgement=HEALTH_ACKNOWLEDGEMENT_COPY,
    questions=HEALTH_CLARIFY_QUESTIONS,
)

#: [OD-BOT §164] — the one G4 routing question, same kind as the pain clarify
#: (the screen shows the list and the answer box for ``health_clarify``), but
#: NO acknowledgement line and NO second question. The answer never forwards
#: the body while the question is open.
_G4_QUESTION_STOP = SafetyStop(kind=KIND_CLARIFY, questions=(G4_ROUTING_QUESTION,))
_STATE_UNAVAILABLE_STOP = SafetyStop(kind=KIND_STATE_UNAVAILABLE, text=STATE_UNAVAILABLE_TEXT)
_S1_STOP = SafetyStop(kind=KIND_RED_FLAG, text=RED_FLAG_REPLY)


class _CarrierUnavailable(Exception):
    """The conversation could not be resolved / created — fail closed."""


def _conversation_for(bot_user: Any, *, create: bool):
    """The bot_user's conversation — the carrier of the persisted G4 state.

    Shared with every chat surface. ``None`` only when nothing exists yet and
    ``create`` is False (a legitimate «no state»). A lookup or creation
    FAILURE raises :class:`_CarrierUnavailable`: the caller blocks the
    request — an unreadable safety state must never read as «unrestricted».
    """

    from apps.conversations.services import resolve_conversation_for_bot_user

    try:
        return resolve_conversation_for_bot_user(bot_user, create_if_missing=create)
    except Exception as exc:  # noqa: BLE001 — logged, then fail-closed upstream
        logger.exception("miniapp_api.health_gate.conversation_lookup_failed")
        raise _CarrierUnavailable from exc


def _g4_stop_from(outcome: Any) -> SafetyStop:
    return _S1_STOP if outcome.stop else _G4_QUESTION_STOP


def free_text_of(body: dict[str, Any]) -> str | None:
    """The free text a goal body carries, or None when it carries none.

    Only ``goal_text`` and ``answer.text`` are human sentences; keys,
    option keys, intents and confirms are vocabulary and are not screened.
    """

    goal_text = body.get("goal_text")
    if isinstance(goal_text, str) and goal_text.strip():
        return goal_text
    answer = body.get("answer")
    if isinstance(answer, dict):
        text = answer.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return None


def _memo_key(bot_user: Any) -> str:
    return f"miniapp_api:health_gate:{bot_user.pk}"


def _memo(bot_user: Any) -> str | None:
    value = cache.get(_memo_key(bot_user))
    return value if isinstance(value, str) else None


def _remember(bot_user: Any, state: str) -> None:
    cache.set(_memo_key(bot_user), state, timeout=ASKED_TTL_SECONDS)


def _crisis_or_block(text: str) -> SafetyStop | None:
    """The inbound gate's two verdicts that end the flow, on their own route."""

    inbound = evaluate_inbound(text)
    if not inbound.allowed:
        if inbound.verdict == SafetyVerdict.HANDOFF.value:
            kind = KIND_CRISIS
        elif inbound.verdict == SafetyVerdict.MEDICAL.value:
            # DRF-2000: the gate's medical verdict is the same medical S1
            # stop the classifier produces below — one kind, one text.
            kind = KIND_RED_FLAG
        else:
            kind = KIND_BLOCK
        return SafetyStop(kind=kind, text=inbound.reply_text)
    return None


def _needs_clarification(text: str) -> bool:
    if classify(text) == PainSignal.SOFT:
        return True
    return evaluate_inbound(text).verdict == SafetyVerdict.CLARIFY.value


def screen_goal_body(
    bot_user: Any, body: dict[str, Any]
) -> tuple[SafetyStop | None, dict[str, Any]]:
    """Decide whether ``body`` may go to Ayla, and what goes.

    Returns ``(stop, forward)``: ``stop`` is None when the write may
    proceed with ``forward`` (the body minus ``safety_answer``); otherwise
    ``stop`` is what the person is shown instead and nothing is forwarded.

    Fail-closed: when the persisted safety state cannot be read, created or
    written, the answer is :data:`_STATE_UNAVAILABLE_STOP` — never a forward.
    """

    forward = {k: v for k, v in body.items() if k != SAFETY_ANSWER_FIELD}
    try:
        return _screen(bot_user, body, forward)
    except _CarrierUnavailable:
        logger.error(
            "miniapp_api.health_gate.fail_closed bot_user=%s", getattr(bot_user, "pk", None)
        )
        return _STATE_UNAVAILABLE_STOP, forward


def _screen(
    bot_user: Any, body: dict[str, Any], forward: dict[str, Any]
) -> tuple[SafetyStop | None, dict[str, Any]]:
    text = free_text_of(body)
    raw_answer = body.get(SAFETY_ANSWER_FIELD)
    answer: str = raw_answer.strip() if isinstance(raw_answer, str) else ""
    has_answer = bool(answer)
    # What the person sent this time: the answer field when there is one,
    # otherwise the free text. Everything below reads this first.
    reply_text = answer if has_answer else (text or "")

    # 1. The strongest outcomes need no state: crisis / block, then an explicit
    #    S1 red flag in the reply or the text. They come BEFORE the carrier so
    #    an unreadable state can never weaken them.
    if reply_text:
        inbound_stop = _crisis_or_block(reply_text)
        if inbound_stop is not None:
            logger.info(
                "miniapp_api.health_gate.stop kind=%s on=reply bot_user=%s",
                inbound_stop.kind,
                bot_user.pk,
            )
            return inbound_stop, forward
    if text is not None and has_answer:
        inbound_stop = _crisis_or_block(text)
        if inbound_stop is not None:
            return inbound_stop, forward

    # 2. The persisted state: the durable restriction on the identity
    #    (``BotUser.context``, survives a new conversation) and the
    #    conversational question on the conversation. A lookup FAILURE is not
    #    «no state»: it is recorded and the request is blocked (fail-closed)
    #    — unless the text itself is an explicit red flag, which is the
    #    stronger outcome and needs no state.
    carrier_failed = False
    conversation = None
    try:
        conversation = _conversation_for(bot_user, create=False)
    except _CarrierUnavailable:
        carrier_failed = True
    try:
        state: Any = g4_state(conversation, bot_user)
    except Exception:  # noqa: BLE001 — an unreadable identity row is a failure, not «no state»
        logger.exception("miniapp_api.health_gate.state_read_failed bot_user=%s", bot_user.pk)
        state = None
        carrier_failed = True

    if state is not None and state.stopped:
        # [OD-BOT §156]: a durable S1 STOP — every later request gets the STOP
        # frame, nothing is forwarded, nothing here is clearance.
        return _S1_STOP, forward

    if state is not None and state.active:
        # [OD-BOT §164] — the open restriction binds whatever the person sends
        # next: the answer field, or the free text itself. The body is never
        # forwarded while it is open — a «нет» is not clearance. A restriction
        # without a conversation (new session, or the row could not be read)
        # is still a restriction: the question is put again on a carrier
        # created on demand; if none can be had, the request is blocked.
        if not reply_text:
            return _G4_QUESTION_STOP, forward
        if conversation is None:
            conversation = _conversation_for(bot_user, create=True)
            if conversation is None:
                raise _CarrierUnavailable
        outcome = route_g4_reply(conversation, bot_user, reply_text)
        logger.info(
            "miniapp_api.health_gate.g4 outcome=%s group=%s bot_user=%s",
            outcome.kind,
            outcome.group,
            bot_user.pk,
        )
        if outcome.stop and not g4_state(conversation, bot_user).stopped:
            # The STOP reply is still the STOP reply; the durable record did not
            # land — logged, and the request stays blocked either way.
            logger.error("miniapp_api.health_gate.stop_not_persisted bot_user=%s", bot_user.pk)
        return _g4_stop_from(outcome), forward

    explicit = _explicit_s1_stop(reply_text) or (_explicit_s1_stop(text) if text else None)
    if explicit is not None:
        logger.info(
            "miniapp_api.health_gate.stop kind=%s on=text bot_user=%s", explicit.kind, bot_user.pk
        )
        return explicit, forward

    if carrier_failed:
        # Nothing explicit in the text, and we could not learn whether a
        # restriction is on record: block, never forward.
        raise _CarrierUnavailable

    if text is None:
        return None, forward

    if has_answer:
        # No G4 question on record: the pain-clarify answer path (DRF-1763).
        # The answer was already screened for crisis / block / red flag above.
        if _memo(bot_user) == _MEMO_ASKED:
            _remember(bot_user, _MEMO_ANSWERED)
        # No question on record → the flag is not clearance; fall through
        # and screen the text itself.

    if classify(text) == PainSignal.CLARIFY:
        # Ambiguous G4 — the one registered question, persisted on the same
        # conversation the chat surfaces read. The carrier is created on
        # demand; if it cannot be created or the state cannot be written, the
        # request is blocked (fail-closed) instead of asked-and-forgotten.
        conversation = _conversation_for(bot_user, create=True)
        if conversation is None or not ask_g4(conversation, bot_user):
            raise _CarrierUnavailable
        logger.info("miniapp_api.health_gate.g4_asked bot_user=%s", bot_user.pk)
        return _G4_QUESTION_STOP, forward

    if _needs_clarification(text) and _memo(bot_user) != _MEMO_ANSWERED:
        _remember(bot_user, _MEMO_ASKED)
        logger.info(
            "miniapp_api.health_gate.stop kind=%s on=text bot_user=%s", KIND_CLARIFY, bot_user.pk
        )
        return _CLARIFY_STOP, forward

    return None, forward


def _explicit_s1_stop(text: str | None) -> SafetyStop | None:
    """An explicit S1 red flag in ``text`` — the S1 STOP frame, no state needed."""

    if text and classify(text) == PainSignal.RED_FLAG:
        return _S1_STOP
    return None


__all__ = [
    "ASKED_TTL_SECONDS",
    "HEALTH_ACKNOWLEDGEMENT_COPY",
    "HEALTH_CLARIFY_QUESTIONS",
    "KIND_BLOCK",
    "KIND_CLARIFY",
    "KIND_CRISIS",
    "KIND_RED_FLAG",
    "KIND_STATE_UNAVAILABLE",
    "SAFETY_ANSWER_FIELD",
    "STATE_UNAVAILABLE_TEXT",
    "SafetyStop",
    "free_text_of",
    "screen_goal_body",
]
