"""Тапы по карточке C04 в DM: «Почему» / «Другой вариант» / «Не сейчас» (DRF-1772).

Реакция пишется в запись `Recommendation` (B8: ENGAGED доказывает
взаимодействие; события N7 сядут сюда же). Ответы — честные заглушки до
N4 (альтернативы) и N5 (evidence_refs): второго направления нет, и бот
этого не скрывает; кадр C04.2 показывает те же причины под заголовком
макета. Тексты заглушек — не с макета (в макете этих состояний при
одном направлении нет) — отступление, вынесено владельцу.

Чужой или протухший id — ответ без записи: payload нарисовал бот, и
подделанный buys nothing — запись ищется по своему `bot_user`.
"""

from __future__ import annotations

import logging
import uuid

from django.utils import timezone

from apps.orchestrator.discovery import DiscoveryReply
from apps.recommendation import card as c
from apps.recommendation.models import Recommendation
from apps.recommendation.provenance import audit_reaction

logger = logging.getLogger(__name__)

#: C04.3 при одном направлении — честно: другого пока нет, путь — своими словами.
ALT_STUB_TEXT = (
    "Пока у меня одно направление под твою цель. "
    "Расскажи своими словами, что для тебя важнее, — подберу другое."
)
#: «Не сейчас» — закрыть без записи отказа-навсегда (R04).
SKIP_TEXT = "Хорошо, не сейчас. Когда захочешь вернуться — напиши мне."
#: Кнопка есть, карточки за ней уже нет.
STALE_TEXT = "Эта карточка уже неактуальна — напиши, чего хочется, и я подберу заново."


def is_recommendation_callback(text: str) -> bool:
    return text.startswith(c.RECO_CALLBACK_PREFIX)


def _parse(text: str) -> tuple[str, uuid.UUID] | None:
    for kind, prefix in (
        ("why", c.RECO_WHY_PREFIX),
        ("alt", c.RECO_ALT_PREFIX),
        ("skip", c.RECO_SKIP_PREFIX),
    ):
        if text.startswith(prefix):
            raw = text[len(prefix) :]
            try:
                return kind, uuid.UUID(raw)
            except ValueError:
                return None
    return None


def route_recommendation_callback(*, global_bot_user, callback_text: str) -> DiscoveryReply:
    parsed = _parse(callback_text)
    record = (
        Recommendation.objects.filter(
            id=parsed[1], bot_user=global_bot_user, kind=Recommendation.Kind.DIRECTION
        ).first()
        if parsed
        else None
    )
    if parsed is None or record is None:
        logger.info("recommendation.tap kind=stale")
        return DiscoveryReply(text=STALE_TEXT)

    kind = parsed[0]
    reaction = {
        "why": Recommendation.Reaction.WHY_REQUESTED,
        "alt": Recommendation.Reaction.ALTERNATIVE_REQUESTED,
        "skip": Recommendation.Reaction.REJECTED,
    }[kind]
    record.reaction = reaction
    record.reacted_at = timezone.now()
    record.save(update_fields=["reaction", "reacted_at"])
    # DRF-1773 — реакция в существующий аудит; новой шины нет,
    # `recommendation.accepted` не вводится (снят владельцем, B8).
    audit_reaction(record, reaction)
    logger.info("recommendation.tap kind=%s recommendation=%s", kind, record.id)

    if kind == "why":
        draft = c.CardDraft(
            goal_id=record.goal_id,
            what=record.what,
            subline=record.subline,
            why=tuple(record.why or ()),
            facts=dict(record.facts or {}),
        )
        return DiscoveryReply(text=c.render_why_more_text(draft))
    if kind == "alt":
        return DiscoveryReply(text=ALT_STUB_TEXT)
    return DiscoveryReply(text=SKIP_TEXT)


__all__ = [
    "ALT_STUB_TEXT",
    "SKIP_TEXT",
    "STALE_TEXT",
    "is_recommendation_callback",
    "route_recommendation_callback",
]
