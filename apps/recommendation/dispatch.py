"""Триггер и отправка карточки C04 в DM после C03 (DRF-1772, К-3).

Триггер — серверный факт, не эвристика (ruling §61)
-----------------------------------------------------
Бот узнаёт, что контекст под цель собран, из документа каталога, который
сам же и проксирует Mini App (`miniapp_api.customer_goal_select`): ответ с
`next.id == "return_to_chat"` — «спрашивать больше нечего» (К-2, каталог
d63ef965). Ни движок readiness (он в тени и на проде BLOCKED — лист
мозга), ни модель, ни клиент здесь ничего не решают: клиент мог бы
прислать что угодно, поэтому читается ответ каталога, а не тело запроса.

Идемпотентность
---------------
Мини-апп может показать тот же документ дважды (повторный GET, перезапуск),
человек — ответить и вернуться. Один и тот же собранный контекст под ту же
цель → один `fingerprint` → одна запись `Recommendation` → одна карточка.
Второй раз — молчание, не вторая карточка.

Отправка — best-effort
----------------------
Вызывается из ответа на запрос Mini App: отказ MAX или базы не должен
превратить успешный ответ каталога в 5xx для экрана. Ошибка пишется в
журнал своим именем, экран получает свой документ как ни в чём не бывало.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import IntegrityError, transaction

from apps.recommendation import card as c
from apps.recommendation.models import Recommendation

logger = logging.getLogger(__name__)

NEXT_RETURN_TO_CHAT = "return_to_chat"
ACTION_TYPE_CARD = "recommendation_card"
ACTION_TYPE_ABSENCE = "recommendation_absence"


def context_collected(doc: Any) -> bool:
    """``next.id == return_to_chat`` и вопросов нет — серверный факт каталога."""
    if not isinstance(doc, dict):
        return False
    nxt = doc.get("next")
    if not isinstance(nxt, dict) or nxt.get("id") != NEXT_RETURN_TO_CHAT:
        return False
    return not doc.get("missing")


def _is_global(bot_user: Any) -> bool:
    from apps.identity.services.global_tenant import get_global_bot_tenant

    try:
        return bot_user.tenant_id == get_global_bot_tenant().id
    except Exception:  # noqa: BLE001 — нет сентинела → не глобальный
        return False


def _absence_fingerprint(goal_id: str) -> str:
    return f"absence:{goal_id}"


def maybe_send_card(bot_user: Any, doc: Any) -> Recommendation | None:
    """На собранном контексте — карточка C04.1 или C04.4 в DM, один раз.

    Возвращает созданную запись или ``None`` (не собран / уже показано /
    отправить не удалось). Никогда не поднимает исключение наружу.
    """
    if not context_collected(doc):
        return None
    if not _is_global(bot_user):
        # Mini App открывают и из салонного бота. Карточка C04 — путь
        # клиентского (глобального) бота; слать её от него в салонный диалог
        # — класс дефекта DRF-2128 (не тот бот). Салонный путь — отдельно.
        logger.info("recommendation.dispatch.skipped reason=not_global bot_user=%s", bot_user.id)
        return None
    try:
        return _send_once(bot_user, doc)
    except Exception:  # noqa: BLE001 — экран не должен пострадать от DM
        logger.exception(
            "recommendation.dispatch.failed bot_user=%s", getattr(bot_user, "id", None)
        )
        return None


def _send_once(bot_user: Any, doc: dict[str, Any]) -> Recommendation | None:
    draft = c.build_card(doc)
    goal = (
        ((doc.get("known") or {}).get("goal") or {}) if isinstance(doc.get("known"), dict) else {}
    )
    goal_id = str(goal.get("id") or "")

    if draft is None:
        record = _create(
            bot_user,
            kind=Recommendation.Kind.ABSENCE,
            goal_id=goal_id,
            fingerprint=_absence_fingerprint(goal_id),
        )
        if record is None:
            return None
        text = c.NO_VERIFIED_EVIDENCE_TEXT
        envelope = c.absence_keyboard()
        action_type = ACTION_TYPE_ABSENCE
    else:
        record = _create(
            bot_user,
            kind=Recommendation.Kind.DIRECTION,
            goal_id=draft.goal_id,
            fingerprint=draft.fingerprint,
            what=draft.what,
            subline=draft.subline,
            why=list(draft.why),
            facts=draft.facts,
        )
        if record is None:
            return None
        text = c.render_card_text(draft)
        envelope = c.card_keyboard(str(record.id))
        action_type = ACTION_TYPE_CARD

    try:
        _deliver(bot_user, record, text=text, envelope=envelope, action_type=action_type)
    except Exception:
        # Запись — ключ идемпотентности; недоставленная карточка не должна
        # запирать его: снимаем запись, следующий триггер попробует снова.
        record.delete()
        raise
    return record


def _create(bot_user: Any, **fields: Any) -> Recommendation | None:
    """Запись или ``None``, если такая уже была (идемпотентность по fingerprint)."""
    try:
        with transaction.atomic():
            return Recommendation.objects.create(bot_user=bot_user, **fields)
    except IntegrityError:
        logger.info(
            "recommendation.dispatch.duplicate bot_user=%s fingerprint=%s",
            bot_user.id,
            fields.get("fingerprint"),
        )
        return None


def _deliver(
    bot_user: Any,
    record: Recommendation,
    *,
    text: str,
    envelope: dict[str, Any] | None,
    action_type: str,
) -> None:
    """DM по `user_id` человека (DRF-1558) + строка ассистента в глобальной переписке."""
    from apps.channels.max.handler import _build_attachments
    from apps.channels.max.outbound import send_message
    from apps.conversations.services import (
        record_global_message,
        resolve_active_global_conversation,
    )

    attachments = _build_attachments(envelope) if envelope else None
    send_message(user_id=str(bot_user.channel_user_id), text=text, attachments=attachments)

    action_data: dict[str, Any] = dict(envelope or {})
    action_data["recommendation"] = {"id": str(record.id), "kind": record.kind}
    conversation = resolve_active_global_conversation(bot_user)
    if conversation is not None:
        record_global_message(
            conversation,
            role="assistant",
            content=text,
            action_type=action_type,
            action_data=action_data,
        )
    logger.info(
        "recommendation.dispatch.sent bot_user=%s recommendation=%s kind=%s why=%d",
        bot_user.id,
        record.id,
        record.kind,
        len(record.why or []),
    )


__all__ = ["ACTION_TYPE_ABSENCE", "ACTION_TYPE_CARD", "context_collected", "maybe_send_card"]
