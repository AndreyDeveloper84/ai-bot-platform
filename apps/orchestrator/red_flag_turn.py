"""Медицинский red flag G1–G7 → детерминированный ответ, одна точка (DRF-2213 Q2).

Решение владельца 20.09 (DRF-2000): red flag ``health_screening`` на global-пути
отвечает детерминированно, не LLM. В DRF-2000 замыкание стояло внутри консьержа
— и всё, что обработчик MAX решал раньше консьержа (онбординг нового человека,
продолжение записи, ответ на вопрос памяти), отвечало на red flag своим текстом:
«онемела половина лица» первым сообщением получало приветствие. Теперь проверка
стоит в обработчике сразу после гейта ``evaluate_inbound`` — выше любой ветки,
кроме самого гейта (группа «неотложка» и кризис уходят там же, раньше).

Ответ — тот же навык тем же исполнителем, что и по вызову модели
(:func:`apps.orchestrator.nutrition_global.execute_nutrition_tool`): текст
[OD-BOT §163] один на всех путях, второго классификатора нет.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from apps.orchestrator.discovery import DiscoveryReply

logger = logging.getLogger(__name__)

#: ``Message.action_type`` хода — тот же, что у ответа навыка по вызову модели.
RED_FLAG_ACTION_TYPE = "health_screening"


def red_flag_reply(
    message_text: str, *, bot_user: Any, conversation: Any, trace_id: str | UUID | None
) -> DiscoveryReply | None:
    """Ответ на red flag классификатора или ``None`` (сигнала нет / навык отказал).

    ``None`` возвращает ход обычной лестнице обработчика, как до DRF-2213.
    Никогда не бросает: сбой этой ветки не должен стоить человеку хода.

    Открытый вопрос бота (DRF-1779) закрывается этой репликой: red flag в
    ответе на «где болит?» — ответ, и висеть до следующего хода вопрос не
    должен. ``persisted=False`` — навык ход не пишет, пишет обработчик.
    """

    from apps.orchestrator.nutrition_global import execute_nutrition_tool
    from apps.orchestrator.open_question import close_question
    from apps.skills.health_screening.classifier import PainSignal, classify

    try:
        if classify(message_text) != PainSignal.RED_FLAG:
            return None
        close_question(conversation, message_text)
        result = execute_nutrition_tool(
            "health_screening",
            {"symptom_text": message_text},
            bot_user=bot_user,
            conversation=conversation,
            trace_id=str(trace_id) if trace_id else "",
            message_text=message_text,
        )
    except Exception:  # noqa: BLE001 — сбой детерминированной ветки не стоит хода
        logger.exception("orchestrator.red_flag_turn.failed trace=%s", trace_id)
        return None
    if result is None or not result.reply_text:
        return None
    logger.info("orchestrator.red_flag_turn.answered trace=%s", trace_id)
    return DiscoveryReply(text=result.reply_text, action_data=result.action_data, persisted=False)


def g7_question_reply(
    message_text: str, *, bot_user: Any, conversation: Any, trace_id: str | UUID | None
) -> DiscoveryReply | None:
    """[OD-BOT §170] — the G7 question turn, at the same point as the red flag.

    An ambiguous G7 message asks the one ``health_screening.g7`` question; an
    open G7 question binds the next turn; a G7 tap is routed. All by the same
    skill every other surface runs (:class:`HealthScreeningSkill`), BEFORE any
    other branch of the handler ladder (onboarding, booking continuation, a
    memory answer) and before the model — otherwise one of them would answer
    in the question's place. ``None`` — not a G7 turn (or the skill failed:
    the concierge still runs the same skill before the model).
    """

    from apps.skills.base import SkillContext
    from apps.skills.health_screening.classifier import PainSignal, clarify_group, classify
    from apps.skills.health_screening.g7_question import g7_pending, is_g7_callback
    from apps.skills.health_screening.skill import HealthScreeningSkill

    try:
        if not (
            g7_pending(conversation) is not None
            or is_g7_callback(message_text)
            or (
                classify(message_text) is PainSignal.CLARIFY and clarify_group(message_text) == "G7"
            )
        ):
            return None
        result = HealthScreeningSkill().handle(
            SkillContext(conversation=conversation, bot_user=bot_user, message_text=message_text)
        )
    except Exception:  # noqa: BLE001 — сбой детерминированной ветки не стоит хода
        logger.exception("orchestrator.red_flag_turn.g7_failed trace=%s", trace_id)
        return None
    if not result.reply_text:
        return None
    logger.info(
        "orchestrator.red_flag_turn.g7 kind=%s trace=%s",
        (result.meta or {}).get("reply_kind"),
        trace_id,
    )
    return DiscoveryReply(text=result.reply_text, action_data=result.action_data, persisted=False)


__all__ = ["RED_FLAG_ACTION_TYPE", "g7_question_reply", "red_flag_reply"]
