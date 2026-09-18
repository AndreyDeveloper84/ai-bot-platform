"""«Мой план» в чате — карточка Plan Lite из wellness-context (DRF-2101, §49).

Тот же документ, что читает Mini App: «Твоя цель: … · На этой неделе:
записаться на услугу ✓, дневник 3 из 5, вода 4 из 7». Только adherence
«N из M» (В-5, DRF-1332): ни процента цели, ни «достигнута», ни «ты
пропустил». Без проактивности — карточка показывается, когда человек сам
спросил (``WELLNESS_PROACTIVE_ENABLED`` заперт, здесь не читается).

Матчер детерминированный — тот же двухслойный приём, что у чтения дневника
(DRF-1302): фраза «мой план» забирается здесь и до модели не доходит;
без флага ``PLAN_LITE_ENABLED`` текст — не наш, уходит модели как раньше.
Триггеры — фразы со словом «план», а не одно слово: «план массажа» —
модели.

Логи — без идентификатора канала (DRF-2009): только ``bot_user.pk``,
статус и класс отказа.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from django.conf import settings

from apps.integrations.ayla import external_user_id_for
from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    WellnessContextError,
    WellnessContextHttpClient,
)
from apps.skills.base import SkillResult

logger = logging.getLogger(__name__)

#: Фразы, которые забираются детерминированно. Нижняя граница в тесте:
#: каждая содержит «план» и не равна ему.
MY_PLAN_TRIGGERS: tuple[str, ...] = (
    "мой план",
    "покажи план",
    "какой у меня план",
    "план на неделю",
)


@dataclass(frozen=True)
class _Copy:
    title: str = "Твоя цель: {goal}."
    week: str = "На этой неделе: {items}."
    today_suffix: str = " (сегодня)"
    no_plan: str = (
        "Плана пока нет. Составить его можно в приложении: выбери 1–3 шага под свою цель — "
        "и я буду показывать, сколько из них сделано."
    )
    unavailable: str = "Не могу прочитать план: сервис сейчас не отвечает. Попробуй чуть позже."
    action_book: str = "записаться на услугу"
    action_food: str = "дневник"
    action_water: str = "вода"


PLAN_LITE_COPY = _Copy()

_ACTION_LABELS = {
    "book_service": PLAN_LITE_COPY.action_book,
    "log_food": PLAN_LITE_COPY.action_food,
    "log_water": PLAN_LITE_COPY.action_water,
}


def plan_lite_enabled() -> bool:
    return bool(getattr(settings, "PLAN_LITE_ENABLED", False))


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower().replace("ё", "е"))


def looks_like_my_plan_request(text: str) -> bool:
    """``True`` — фраза про свой план. ``False`` — «не моё, дальше по лестнице»."""
    norm = _normalise(text)
    if not norm or len(norm) > 60:
        return False
    return any(trigger in norm for trigger in MY_PLAN_TRIGGERS)


def _action_line(action: PlanLiteAction) -> str:
    label = _ACTION_LABELS.get(action.action_type, action.action_type)
    if action.action_type == "book_service" and action.target_count == 1:
        return f"{label} {'✓' if action.done_count >= 1 else '—'}"
    suffix = PLAN_LITE_COPY.today_suffix if action.cadence == "per_day" else ""
    return f"{label} {action.done_count} из {action.target_count}{suffix}"


def render_plan_lite_card(plan: PlanLite) -> str:
    """Карточка — только форма обязательств и факты «N из M»."""
    lines = [PLAN_LITE_COPY.title.format(goal=plan.goal_key or "—")]
    if plan.actions:
        lines.append(
            PLAN_LITE_COPY.week.format(items=", ".join(_action_line(a) for a in plan.actions))
        )
    return "\n".join(lines)


def try_handle_my_plan(*, text: str, bot_user, trace_id: str) -> SkillResult | None:
    """«мой план» → карточка из документа; ``None`` — не наше (флаг/текст)."""
    if not plan_lite_enabled() or not looks_like_my_plan_request(text):
        return None
    external_id = external_user_id_for(bot_user)
    try:
        ctx = WellnessContextHttpClient().get_wellness_context(external_user_id=external_id)
    except WellnessContextError as exc:
        logger.warning(
            "orchestrator.plan_lite.unavailable bot_user=%s class=%s trace=%s",
            getattr(bot_user, "pk", None),
            type(exc).__name__,
            trace_id,
        )
        return SkillResult(
            reply_text=PLAN_LITE_COPY.unavailable, meta={"reply_kind": "plan_lite_unavailable"}
        )
    if ctx.plan_lite is None:
        return SkillResult(reply_text=PLAN_LITE_COPY.no_plan, meta={"reply_kind": "plan_lite_none"})
    logger.info(
        "orchestrator.plan_lite.card bot_user=%s actions=%d trace=%s",
        getattr(bot_user, "pk", None),
        len(ctx.plan_lite.actions),
        trace_id,
    )
    return SkillResult(
        reply_text=render_plan_lite_card(ctx.plan_lite),
        action_type="plan_lite_card",
        meta={"reply_kind": "plan_lite_card"},
    )
