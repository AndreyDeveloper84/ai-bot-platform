"""Тапы по карточке C04 в DM: «Почему» / «Другой вариант» / «Не сейчас» (DRF-1772).

Реакция пишется в запись `Recommendation` (B8: ENGAGED доказывает
взаимодействие; события N7 сядут сюда же).

«Другой вариант» (DRF-1770, К-3 N4)
-----------------------------------
Есть другие подходы — кадр C04.3 макета дословно, кнопка на каждый.
Выбор подхода — НОВАЯ запись `Recommendation` с той же целью и теми же
причинами: запись immutable (B13), и переписать в ней направление
значило бы стереть то, что человеку показали первым. В «других подходах»
новой карточки прежнее направление встаёт на место выбранного — человек
может вернуться, не начиная разговор заново.

Подходов нет (одна строка в таблице владельца) — прежняя честная
заглушка: второго направления нет, и бот этого не скрывает. Кадр C04.2
(«Почему») по-прежнему показывает те же причины под заголовком макета:
дополнительных причин нет до `evidence_refs` — предел D11, тот же, что
назван в N5. Тексты заглушек — не с макета (в макете этих состояний при
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

#: C04.3 при ОДНОМ направлении — честно: другого пока нет, путь — своими словами.
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


def _parse(text: str) -> tuple[str, uuid.UUID, int] | None:
    """``(вид, id карточки, индекс подхода)``; индекс ``-1`` — не выбор."""
    if text.startswith(c.RECO_PICK_PREFIX):
        raw, _, tail = text[len(c.RECO_PICK_PREFIX) :].partition(":")
        try:
            return "pick", uuid.UUID(raw), int(tail)
        except ValueError:
            return None
    for kind, prefix in (
        ("why", c.RECO_WHY_PREFIX),
        ("alt", c.RECO_ALT_PREFIX),
        ("skip", c.RECO_SKIP_PREFIX),
    ):
        if text.startswith(prefix):
            raw = text[len(prefix) :]
            try:
                return kind, uuid.UUID(raw), -1
            except ValueError:
                return None
    return None


def _draft_of(record: Recommendation) -> c.CardDraft:
    """Черновик из записи — читаем показанное, а не пересобираем заново."""
    return c.CardDraft(
        goal_id=record.goal_id,
        what=record.what,
        subline=record.subline,
        why=tuple(record.why or ()),
        facts=dict(record.facts or {}),
        alternatives=list(record.alternatives or []),
    )


def _pick_alternative(record: Recommendation, index: int) -> DiscoveryReply:
    """Выбранный подход — новая карточка C04.1, прежнее направление рядом.

    Индекс вне показанного — `stale`: кнопку рисовал бот, но кадр мог
    устареть, и молча показать «какой-нибудь» подход было бы подменой.
    """
    from apps.recommendation.dispatch import create_record

    shown = list(record.alternatives or [])
    if not 0 <= index < len(shown):
        logger.info("recommendation.tap kind=pick_stale recommendation=%s", record.id)
        return DiscoveryReply(text=STALE_TEXT)

    chosen = shown[index]
    others = [{"what": record.what, "subline": record.subline}]
    others.extend(row for position, row in enumerate(shown) if position != index)
    draft = c.CardDraft(
        goal_id=record.goal_id,
        what=str(chosen.get("what") or ""),
        subline=str(chosen.get("subline") or ""),
        why=tuple(record.why or ()),
        facts=dict(record.facts or {}),
        alternatives=others[: c.MAX_ALTERNATIVES],
    )
    picked = (
        create_record(
            record.bot_user,
            kind=Recommendation.Kind.DIRECTION,
            goal_id=draft.goal_id,
            fingerprint=draft.fingerprint,
            what=draft.what,
            subline=draft.subline,
            why=list(draft.why),
            facts=draft.facts,
            alternatives=list(draft.alternatives),
        )
        or Recommendation.objects.filter(
            bot_user=record.bot_user, fingerprint=draft.fingerprint
        ).first()
    )
    if picked is None:
        return DiscoveryReply(text=STALE_TEXT)
    logger.info("recommendation.tap kind=pick recommendation=%s picked=%s", record.id, picked.id)
    return DiscoveryReply(
        text=c.render_card_text(draft), action_data=c.card_keyboard(str(picked.id))
    )


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
        # Выбор подхода — та же реакция, что и просьба о нём: человек
        # попросил другое и получил другое. Отдельного имени у неё нет
        # (B8 — словарь реакций закрыт владельцем).
        "pick": Recommendation.Reaction.ALTERNATIVE_REQUESTED,
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
        return DiscoveryReply(text=c.render_why_more_text(_draft_of(record)))
    if kind == "pick":
        return _pick_alternative(record, parsed[2])
    if kind == "alt":
        draft = _draft_of(record)
        if not draft.alternatives:
            return DiscoveryReply(text=ALT_STUB_TEXT)
        return DiscoveryReply(
            text=c.render_alternatives_text(draft),
            action_data=c.alternatives_keyboard(str(record.id), draft),
        )
    return DiscoveryReply(text=SKIP_TEXT)


__all__ = [
    "ALT_STUB_TEXT",
    "SKIP_TEXT",
    "STALE_TEXT",
    "is_recommendation_callback",
    "route_recommendation_callback",
]
