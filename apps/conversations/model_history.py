"""Что из переписки можно отдавать модели — один гейт для всех читателей (DRF-2700).

Видимая история и данные, которые получает модель, — разные вещи (решение
владельца 07.10.2026, п.23). Этот модуль — про вторые. Экран переписки человека
его не зовёт.

Два хранилища истории, два приёма:

* **строки ``Message``** — читатель пропускает запрос через
  :func:`after_model_cutoff`: реплики не позже отсечки модели не отдаются;
* **окно Redis и состояние подбора** — у их записей нет времени, по которому
  можно резать, поэтому в момент стирания они очищаются целиком
  (:func:`clear_model_context`).

Отсечку считает :func:`apps.consent.services.model_history_cutoff`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, TypeVar

from django.db.models import QuerySet

logger = logging.getLogger(__name__)

_Q = TypeVar("_Q", bound="QuerySet[Any]")


def after_model_cutoff(rows: _Q, bot_user: Any) -> _Q:
    """Оставить в запросе только реплики, которые модели можно отдать.

    Сбой чтения отсечки пробрасывается: каждый читатель обязан закрыть историю,
    а не открыть её (у всех четырёх свой ``except`` с безопасной стороной).
    """
    from apps.consent.services import model_history_cutoff

    cutoff = model_history_cutoff(bot_user)
    if cutoff is None:
        return rows
    # В сам момент отсечки реплика уже не отдаётся — граница строгая.
    return rows.filter(created_at__gt=cutoff)


def clear_model_context(conversation_id: uuid.UUID | str) -> None:
    """Очистить окно Redis и состояние подбора одного разговора.

    Карта токенов ПДн не трогается: в ней телефоны и почта, а не факты памяти,
    и без неё ответ модели в этом же разговоре нельзя было бы раскрыть обратно.
    """
    from apps.orchestrator.decision_readiness import state as dre_state
    from apps.orchestrator.memory import short_term

    short_term.clear(conversation_id)
    dre_state.clear(str(conversation_id))


def clear_model_context_for_person(bot_user: Any) -> int:
    """Очистить окно Redis и состояние подбора во всех разговорах человека.

    Для стирания не из чата (экран памяти, отзыв согласия на предположения):
    хода, который очистил бы свой разговор сам, там нет. Никогда не бросает —
    отсечка для строк базы уже стоит, а сбой Redis не должен стоить человеку
    удаления; он пишется в лог.

    Returns:
      Сколько разговоров очищено.
    """
    try:
        from apps.consent.services import person_channel_shells
        from apps.conversations.models import Conversation

        ids = list(
            Conversation.all_tenants.filter(
                bot_user__in=person_channel_shells(bot_user)
            ).values_list("id", flat=True)
        )
    except Exception:  # noqa: BLE001 — см. докстринг
        logger.exception("conversations.model_history.person_conversations_failed")
        return 0

    cleared = 0
    for conversation_id in ids:
        try:
            clear_model_context(conversation_id)
            cleared += 1
        except Exception:  # noqa: BLE001 — один разговор не должен стоить остальных
            logger.exception(
                "conversations.model_history.clear_failed conversation=%s", conversation_id
            )
    return cleared


def close_forget_turn(bot_user: Any, conversation: Any) -> None:
    """Конец хода «забудь X»: отсечка после ответа и чистое окно разговора.

    Зовётся ПОСЛЕ записи ответа Ayla. Ответ «Готово — забыла, что ты …»
    повторяет стёртое, поэтому отсечка ставится ещё раз — уже позже него, — а
    окно Redis, куда ответ только что дописан, очищается. Никогда не бросает.
    """
    try:
        from apps.identity.services import model_history_cutoff as cutoff

        cutoff.stamp(getattr(bot_user, "ayla_user_id", None))
    except Exception:  # noqa: BLE001 — память не ломает ход
        logger.exception("conversations.model_history.stamp_failed")
    try:
        clear_model_context(conversation.id)
    except Exception:  # noqa: BLE001 — память не ломает ход
        logger.exception(
            "conversations.model_history.clear_failed conversation=%s",
            getattr(conversation, "id", None),
        )


__all__ = [
    "after_model_cutoff",
    "clear_model_context",
    "clear_model_context_for_person",
    "close_forget_turn",
]
