"""Health-screening skill — DRF-358 T04 pain consultation gate.

Sprint 9 / P7 (DRF-824). Pre-LLM gate for messages mentioning physical
pain or red-flag symptoms. Three paths:

1. **Red-flag** (``"потерял чувствительность"`` / ``"температура 38.5"``)
   → redirect to a doctor + decline to suggest a service. Pre-empts
   the LLM from confidently recommending massage for what may be a
   neurological emergency.
2. **Soft pain** (``"болит спина"`` / ``"шея хрустит"``) → ask 1-2
   diagnostic-first clarifying questions BEFORE any service. Mirrors
   the mysite DRF-358 T04 system-prompt rule. Wording is sampled from
   the curated ``DIAGNOSTIC_FIRST_PAIN_EXAMPLES`` pool (D1).
3. **No signal** → skill doesn't match; dispatch continues.

## Why this exists as a skill

The mysite DRF-358 T04 fix put the diagnostic-first rule into the
system prompt, where it always applies. Sprint 9 has no booking skill
yet (Phase 1 backlog DRF-839), so the LLM-driven path is the FAQ skill
— and FAQ doesn't suggest services. The pain-redirect SKILL ensures the
behaviour exists even without booking, and stays in place once booking
ships (booking can still own the diagnostic-first system prompt; this
skill catches the cases where booking doesn't even get invoked).

## Limited scope (Tier 1)

The original ``legacy_maxbot/handlers/health_screening.py`` (533 LOC)
is the Tier-B nutrition pre-anketa flow (consent → pregnancy →
breastfeeding → diabetes → eating disorder → ...) gating
``nutrition_anketa`` for sensitive cohorts. That's a multi-screen FSM
that integrates with Ayla profile API; it belongs with P3
``nutrition_anketa`` and may carry to Phase 1 backlog if Sprint 9 runs
hot. P7 here is the **light** version — pain consultation only.

The full Tier-B port is tracked separately (TBD ticket); the FSM
groundwork from D3 + the I1 Ayla client + the D1 voice examples are
the prerequisites and all landed.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from apps.skills.base import SkillContext, SkillResult
from apps.orchestrator.safety.medical_emergency import MEDICAL_EMERGENCY_TEXT_V2
from apps.skills.health_screening.classifier import (
    PainSignal,
    clarify_group,
    classify,
    s1_group_of,
)
from apps.orchestrator.open_question import open_question
from apps.skills.health_screening.g4_question import (
    G4_ROUTING_QUESTION,
    ask_g4,
    g4_state,
    route_g4_reply,
)
from apps.skills.health_screening.g7_question import (
    G7_QUESTION_ID,
    G7_QUESTION_TEXT,
    OUTSIDE_S1_G7_ACK,
    ask_g7,
    g7_action_data,
    g7_pending,
    is_g7_callback,
    route_g7_turn,
)
from apps.skills.health_screening.memo import (
    remember_screening_asked,
    screening_asked_recently,
)
from apps.skills.registry import register

logger = logging.getLogger(__name__)


# Wording follows the D1 voice examples — empathic, asks one specific
# probe rather than a triage form. Picked from
# DIAGNOSTIC_FIRST_PAIN_EXAMPLES with deliberate generic wording so it
# applies to any body part the user mentioned.
SOFT_PAIN_REPLY = (
    "Понимаю. Уточню, чтобы посоветовать точно:\n"
    "1. Где именно болит — конкретное место?\n"
    "2. Это после нагрузки / сидячей работы или с утра после сна?"
)

# Medical S1 reply — the owner-approved emergency text v2 ([OD-BOT §163]), read
# from the single canonical module. The former «сначала к врачу … когда
# специалист даст добро» wording is gone from every live medical-S1 path:
# it read as a clearance gate and carried causality («массаж в острой фазе
# может ухудшить»). The name ``RED_FLAG_REPLY`` is kept for the importers
# (Mini App gate, tests); its value is the canonical text.
RED_FLAG_REPLY = MEDICAL_EMERGENCY_TEXT_V2


@register
class HealthScreeningSkill:
    """Diagnostic-first pain gate. Cheap regex; no network."""

    name: ClassVar[str] = "health_screening"

    def matches(self, context: SkillContext) -> bool:
        """DRF-1542 — порядок здесь несущий, а не стилистический.

        Сначала классификация, и только потом памятка. ``RED_FLAG``
        возвращает ``True`` ДО того, как памятка вообще прочитана:
        решение владельца ``docs/OPEN_DECISIONS.md`` §35 п.5 — тревожный
        признак сразу включает безопасную ветку, и разрыв петли не
        является основанием промолчать про «сначала к врачу». Петля
        раздражает; молчание на тревожном признаке может стоить человеку
        здоровья.

        Памятка гасит ровно один случай — повтор ``SOFT``: те же два
        вопроса, на которые человек уже ответил. Отказ здесь не «бот
        замолчал»: на глобальном пути ``execute_nutrition_tool``
        возвращает ``None``, и консьерж отдаёт собственный текст модели
        (``dto.content``) — ход возвращается модели, как и просил тикет.
        """

        # [OD-BOT §164] — an open G4 question binds the next reply to this
        # skill BEFORE any intent / booking / recommendation skill (registry
        # order: health_screening precedes booking). Read first: the reply may
        # be «нет» or «запишите меня» — no signal of its own.
        if g4_state(context.conversation, context.bot_user).active:
            return True
        # [OD-BOT §170] — an open G7 question binds the next reply the same way,
        # and a G7 structured answer (live or stale) is this skill's to route.
        if g7_pending(context.conversation) is not None or is_g7_callback(context.message_text):
            return True
        signal = classify(context.message_text)
        if signal == PainSignal.NONE:
            return False
        if signal in (PainSignal.RED_FLAG, PainSignal.CLARIFY):
            return True
        return not screening_asked_recently(context.conversation)

    def handle(self, context: SkillContext) -> SkillResult:
        # [OD-BOT §164] — the reply to the open G4 question, routed
        # deterministically by :func:`route_g4_reply`; the same function every
        # surface calls, so MAX / Telegram / the global concierge / the Mini
        # App cannot disagree about what a reply means.
        state = g4_state(context.conversation, context.bot_user)
        if state.stopped:
            # [OD-BOT §156]: S1 STOP is durable — a later turn (a booking intent,
            # «мне лучше», a new session, a new conversation) gets the STOP reply
            # again; nothing is clearance here. The label is the recorded
            # attribution, or absent when the STOP came from an unnamed rule.
            assert state.restriction is not None
            meta_durable: dict[str, object] = {
                "reply_kind": "health_red_flag",
                "s1_restriction": "stop",
            }
            if state.restriction.group is not None:
                meta_durable["s1_group"] = state.restriction.group
            return SkillResult(reply_text=RED_FLAG_REPLY, meta=meta_durable)
        if state.active:
            outcome = route_g4_reply(context.conversation, context.bot_user, context.message_text)
            if outcome.stop:
                meta_stop: dict[str, object] = {
                    "reply_kind": "health_red_flag",
                    "s1_restriction": "stop",
                }
                if outcome.group is not None:
                    meta_stop["s1_group"] = outcome.group
                return SkillResult(reply_text=RED_FLAG_REPLY, meta=meta_stop)
            return SkillResult(
                reply_text=G4_ROUTING_QUESTION,
                meta={
                    "reply_kind": "health_restriction_persists",
                    "s1_group": "G4",
                    "s1_restriction": "open",
                },
            )

        # [OD-BOT §170] — the reply to the open G7 question (or a G7 tap), routed
        # by :func:`route_g7_turn`, the same function every surface calls.
        if g7_pending(context.conversation) is not None or is_g7_callback(context.message_text):
            return self._g7_turn(context)

        signal = classify(context.message_text)

        if signal == PainSignal.CLARIFY and clarify_group(context.message_text) == "G7":
            return self._ask_g7(context)

        if signal == PainSignal.CLARIFY:
            # Ambiguous G4 — exactly one registered question. The durable
            # restriction and the binding question are written together; if
            # they could not be persisted the question is still put (the reply
            # is the same) and the failure is logged — the chat turn ends here
            # either way, nothing downstream runs.
            persisted = ask_g4(context.conversation, context.bot_user)
            logger.info(
                "health_screening.g4.asked conversation=%s persisted=%s",
                context.conversation.id if context.conversation else None,
                persisted,
            )
            return SkillResult(
                reply_text=G4_ROUTING_QUESTION,
                meta={
                    "reply_kind": "health_clarify_g4",
                    "s1_group": "G4",
                    "s1_restriction": "open" if persisted else "not_persisted",
                },
            )

        if signal == PainSignal.RED_FLAG:
            # Attribution only — the reply is the same canonical text for every
            # medical S1 group. The label is the group that actually won
            # (:func:`s1_group_of`: G6, G4, G1 / G2 / G3 / G5, G7 last —
            # [OD-BOT §170] решение 1); the older flat rules carry no group. One
            # label per turn: a message with both G6 and G4 signs is logged as G6.
            text = context.message_text
            group = s1_group_of(text)
            logger.info(
                "health_screening.red_flag conversation=%s group=%s",
                context.conversation.id if context.conversation else None,
                group,
            )
            meta: dict[str, object] = {"reply_kind": "health_red_flag"}
            if group is not None:
                meta["s1_group"] = group
            return SkillResult(reply_text=RED_FLAG_REPLY, meta=meta)

        if signal == PainSignal.SOFT:
            # Записывается ровно то, что было сказано: вопросы заданы.
            # Красный флаг сюда не пишется — его памятка не гасит, и
            # запоминать нечего.
            remember_screening_asked(context.conversation)
            # DRF-1779 — и записывается, что бот ЖДЁТ ответа: памятка выше
            # гасит повтор вопросов, а это — даёт следующей реплике человека
            # адрес. Без второго факта ответ «1. Спина, 2. После работы»
            # читался как новая тема (диалог владельца 12.09).
            open_question(
                context.conversation,
                "health_screening.soft",
                asked_text=SOFT_PAIN_REPLY,
            )
            return SkillResult(
                reply_text=SOFT_PAIN_REPLY,
                meta={"reply_kind": "health_soft_pain"},
            )

        # NONE shouldn't reach handle() (matches=False) — defensive empty.
        return SkillResult(
            reply_text="",
            should_send=False,
            meta={"reply_kind": "health_no_signal"},
        )

    def _ask_g7(self, context: SkillContext) -> SkillResult:
        """Ambiguous G7 — ONE question ``health_screening.g7`` with its three
        structured answers. No durable restriction ([OD-BOT §170] решение 4)."""

        token = ask_g7(context.conversation)
        logger.info(
            "health_screening.g7.asked conversation=%s persisted=%s",
            context.conversation.id if context.conversation else None,
            token is not None,
        )
        return SkillResult(
            reply_text=G7_QUESTION_TEXT,
            action_data=g7_action_data(token) if token else None,
            meta={
                "reply_kind": "health_clarify_g7",
                "s1_group": "G7",
                "question_id": G7_QUESTION_ID,
                "g7_question": "pending" if token else "not_persisted",
            },
        )

    def _g7_turn(self, context: SkillContext) -> SkillResult:
        """A turn while the G7 question is open, or a G7 structured answer."""

        pending_before = g7_pending(context.conversation)
        outcome = route_g7_turn(context.conversation, context.bot_user, context.message_text)
        logger.info(
            "health_screening.g7.turn outcome=%s reason=%s conversation=%s",
            outcome.kind,
            outcome.reason,
            context.conversation.id if context.conversation else None,
        )
        if outcome.stop:
            meta_stop: dict[str, object] = {
                "reply_kind": "health_red_flag",
                "s1_restriction": "stop",
                "g7_outcome": outcome.reason,
            }
            if outcome.group is not None:
                meta_stop["s1_group"] = outcome.group
            return SkillResult(reply_text=RED_FLAG_REPLY, meta=meta_stop)
        if outcome.kind == "restriction_persists":
            # [OD-BOT §170] решение 4 — answer №2 with a restriction on record is
            # not a recheck: the durable reply stands, nothing is lifted.
            return SkillResult(
                reply_text=RED_FLAG_REPLY,
                meta={
                    "reply_kind": "health_restriction_persists",
                    "g7_outcome": outcome.reason,
                },
            )
        if outcome.kind == "outside_s1_g7":
            # G7 not confirmed on the first ambiguous turn: no restriction, the
            # flow continues on the next turn. Neutral, no CTA ([OD-BOT §170]).
            return SkillResult(
                reply_text=OUTSIDE_S1_G7_ACK,
                meta={"reply_kind": "health_outside_s1_g7", "g7_outcome": outcome.reason},
            )
        if pending_before is None:
            # A G7 tap with no open question (stale / resolved / forged): no state
            # change and no question re-opened by a tap.
            return SkillResult(
                reply_text=OUTSIDE_S1_G7_ACK,
                meta={"reply_kind": "health_g7_stale_tap", "g7_outcome": outcome.reason},
            )
        # UNKNOWN — the same single question again, same slot and token.
        return SkillResult(
            reply_text=G7_QUESTION_TEXT,
            action_data=g7_action_data(pending_before.token),
            meta={
                "reply_kind": "health_clarify_g7",
                "s1_group": "G7",
                "question_id": G7_QUESTION_ID,
                "g7_question": "pending",
                "g7_outcome": outcome.reason,
            },
        )
