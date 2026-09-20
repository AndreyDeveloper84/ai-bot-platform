"""Провенанс «рекомендация → запись»: чтение ссылки и отметка брони (DRF-1773).

Где едет ссылка
---------------
Карточка C04 открывает Mini App кнопкой «Подобрать вариант», и в её
``open_app`` payload лежит ``reco_<uuid>`` — та же грамматика, что у
``master_invite_<token>`` и ``reschedule_<id>`` (двоеточие в payload
запрещено, отсюда подчёркивание). Mini App кладёт payload в провенанс
интента как ``deep_link:reco_<uuid>`` — **существующее поле**
``PendingBookingIntent.entry_point`` (DRF-1484, §24.5); ни один контракт
не расширяется.

Что этот модуль делает
----------------------
Читает ссылку обратно (``recommendation_id_from_entry_point``), проверяет,
что карточка принадлежит ЭТОМУ человеку (``attribution_for``), и отмечает
бронь на карточке (``mark_booked``). Реакции пишет в существующий аудит
(``audit_reaction``) — новой шины нет, ``recommendation.accepted`` не
вводится (снят владельцем, B8).

Предел
------
Цепочка замкнута в НАШИХ данных: интент → проекция брони
(``attribution_metadata.recommendation_id``) → запись (``booking_id``).
У каталога бронь остаётся без ссылки: ``POST appointments/`` принимает
закрытый список полей, и новое поле там — транзакционный домен, лист
каталога и слово владельца.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from django.utils import timezone

from apps.recommendation.models import Recommendation

logger = logging.getLogger(__name__)

#: Префикс ссылки на карточку в ``open_app`` payload и в провенансе интента.
RECO_PAYLOAD_PREFIX = "reco_"

_ENTRY_RE = re.compile(
    rf"^deep_link:{RECO_PAYLOAD_PREFIX}"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)

#: Ключ в ``BookingRequest.attribution_metadata`` — JSONField принимает
#: произвольные корректные ключи, и это ровно то место для аудита (§4).
ATTRIBUTION_KEY = "recommendation_id"

_REACTION_ACTIONS: dict[str, str] = {
    Recommendation.Reaction.WHY_REQUESTED.value: "recommendation.why_requested",
    Recommendation.Reaction.ALTERNATIVE_REQUESTED.value: "recommendation.alternative_requested",
    Recommendation.Reaction.REJECTED.value: "recommendation.rejected",
}


def recommendation_id_from_entry_point(entry_point: Any) -> str | None:
    """``deep_link:reco_<uuid>`` → id; всё остальное — ``None``.

    Прежние формы провенанса (``catalog`` / ``master`` / ``direct`` /
    чужой ``deep_link:``) читаются как раньше: строгая форма, а не
    ``startswith``, иначе «объявил себя ссылкой и не является» прошло бы
    дальше.
    """
    if not isinstance(entry_point, str):
        return None
    match = _ENTRY_RE.match(entry_point.strip())
    if match is None:
        return None
    # Канонический вид: один и тот же id, записанный в разном регистре,
    # обязан давать одну строку — по ней сверяют бронь с карточкой в
    # аудите, а сравнение там строковое.
    return str(uuid.UUID(match.group(1)))


def _own_card(bot_user: Any, recommendation_id: str) -> Recommendation | None:
    return Recommendation.objects.filter(
        id=recommendation_id, bot_user=bot_user, kind=Recommendation.Kind.DIRECTION
    ).first()


def attribution_for(bot_user: Any, entry_point: Any) -> dict[str, str]:
    """Добавка к ``attribution_metadata`` брони — или пусто.

    Пусто — это «запись началась не с карточки», обычный случай; ни одна
    ветка отсюда бронь не ломает. Чужая или несуществующая карточка —
    класс отказа в журнал **без id**: идентификатор чужой записи в наш
    лог не идёт.
    """
    recommendation_id = recommendation_id_from_entry_point(entry_point)
    if recommendation_id is None:
        return {}
    if _own_card(bot_user, recommendation_id) is None:
        logger.info(
            "recommendation.provenance.not_found bot_user=%s",
            getattr(bot_user, "id", None),
        )
        return {}
    return {ATTRIBUTION_KEY: recommendation_id}


def mark_booked(bot_user: Any, recommendation_id: str, booking_id: Any) -> bool:
    """Отметить на карточке бронь, к которой она привела. Один раз.

    ``False`` — карточки нет, чужая, или бронь уже отмечена: «booked»
    про ту запись, которая из этой карточки выросла, а не про последнюю
    (R17 «shown ≠ engaged ≠ booked»).
    """
    card = _own_card(bot_user, recommendation_id)
    if card is None or card.booking_id:
        return False
    card.booking_id = str(booking_id)
    card.booked_at = timezone.now()
    card.save(update_fields=["booking_id", "booked_at"])
    logger.info("recommendation.provenance.booked recommendation=%s", card.id)
    return True


def audit_reaction(card: Recommendation, reaction: str) -> None:
    """Реакция — в существующий аудит; слов человека там нет, только вид и id."""
    action = _REACTION_ACTIONS.get(str(reaction))
    if action is None:
        return
    from apps.audit.services import write_audit

    write_audit(
        action,
        target="Recommendation",
        target_id=card.id,
        payload={"bot_user_id": str(card.bot_user_id), "goal_id": card.goal_id},
    )


__all__ = [
    "ATTRIBUTION_KEY",
    "RECO_PAYLOAD_PREFIX",
    "attribution_for",
    "audit_reaction",
    "mark_booked",
    "recommendation_id_from_entry_point",
]
