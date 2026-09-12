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
from apps.skills.health_screening.classifier import PainSignal, classify
from apps.orchestrator.open_question import open_question
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

# Red-flag wording mirrors the source: warm but firm. The user should
# leave thinking "врач, не массаж" without feeling brushed off.
RED_FLAG_REPLY = (
    "Звучит серьёзно — лучше сначала к врачу, "
    "массаж в острой фазе может ухудшить. "
    "Когда специалист даст добро — приходи, разомнём аккуратно."
)


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

        signal = classify(context.message_text)
        if signal == PainSignal.NONE:
            return False
        if signal == PainSignal.RED_FLAG:
            return True
        return not screening_asked_recently(context.conversation)

    def handle(self, context: SkillContext) -> SkillResult:
        signal = classify(context.message_text)

        if signal == PainSignal.RED_FLAG:
            logger.info(
                "health_screening.red_flag conversation=%s",
                context.conversation.id if context.conversation else None,
            )
            return SkillResult(
                reply_text=RED_FLAG_REPLY,
                meta={"reply_kind": "health_red_flag"},
            )

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
