"""Блокировка клиента из админки (DRF-1497).

### Что такое «блокировка»

До этой задачи механизма не было: были ``proactive_messages_opt_out``
(отписка самого человека от проактивных сообщений) и теневое молчание
handoff (DRF-1015), но «сотрудник выключил этого клиента» не существовало.

Здесь оно одно и честное: ``BotUser.blocked_at`` не NULL — и
``apps.channels.max.outbound`` не отправляет этому человеку ничего. Ни
ответы бота, ни напоминания, ни проактив. Входящие продолжают писаться в
диалог — карточка честно покажет «человек писал, ответа не было, потому
что заблокирован». Снятие блокировки возвращает всё как было.

Телеграм-исходящий (``apps.channels.telegram.outbound``) забором не
закрыт: пилотного трафика там нет, и это осознанно зафиксировано в
отчёте задачи, а не «забыто».

### Почему только через этот модуль

Поля ``blocked_at`` / ``blocked_reason`` / ``blocked_by_username``
правятся только здесь. Причина обязательна — блокировка без причины это
действие, которое невозможно объяснить ни человеку, разбирающему журнал,
ни самому клиенту, когда его разблокируют. Каждая постановка и снятие
пишут строку в ``AuditLog`` с автором и причиной — тот же журнал, куда
DRF-1495 пишет действия админки.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.audit.services import write_audit

logger = logging.getLogger(__name__)

#: Короче этого причина ничего не объясняет читающему журнал.
MIN_REASON_LENGTH = 12

ACTION_BLOCKED = "admin.client.blocked"
ACTION_UNBLOCKED = "admin.client.unblocked"


def check_block_reason(reason: str) -> str:
    """Причина есть и она что-то объясняет. Иначе отказ, а не пустая строка."""
    text = (reason or "").strip()
    if not text:
        raise ValidationError(
            "Причина не указана. Блокировка и снятие блокировки делаются "
            "только с причиной — её прочитает человек, разбирающий журнал."
        )
    if len(text) < MIN_REASON_LENGTH:
        raise ValidationError(
            f"Причина короче {MIN_REASON_LENGTH} символов — по такой записи "
            "в журнале нельзя понять, что случилось. Напишите, за что "
            "блокируете (или почему снимаете блокировку)."
        )
    return text


def is_blocked(bot_user: Any) -> bool:
    """Заблокирован ли человек прямо сейчас."""
    return getattr(bot_user, "blocked_at", None) is not None


def block_user(*, actor: Any, bot_user: Any, reason: str) -> Any:
    """Заблокировать. Идемпотентно: повторная блокировка обновляет причину.

    Автор и причина уходят и в поля строки (чтобы карточка читалась без
    второго запроса), и в журнал — журнал не переписать задним числом, а
    поле можно, поэтому истина в журнале.
    """
    text = check_block_reason(reason)
    username = _username(actor)
    bot_user.blocked_at = timezone.now()
    bot_user.blocked_reason = text
    bot_user.blocked_by_username = username
    bot_user.save(update_fields=["blocked_at", "blocked_reason", "blocked_by_username"])
    _journal(ACTION_BLOCKED, actor=actor, bot_user=bot_user, reason=text)
    logger.info(
        "identity.blocking.blocked bot_user=%s actor=%s",
        getattr(bot_user, "pk", None),
        username,
    )
    return bot_user


def unblock_user(*, actor: Any, bot_user: Any, reason: str) -> Any:
    """Снять блокировку. Причина обязательна и здесь: «почему вернули» —
    такой же вопрос журнала, как «за что выключили».
    """
    text = check_block_reason(reason)
    username = _username(actor)
    bot_user.blocked_at = None
    bot_user.blocked_reason = ""
    bot_user.blocked_by_username = ""
    bot_user.save(update_fields=["blocked_at", "blocked_reason", "blocked_by_username"])
    _journal(ACTION_UNBLOCKED, actor=actor, bot_user=bot_user, reason=text)
    logger.info(
        "identity.blocking.unblocked bot_user=%s actor=%s",
        getattr(bot_user, "pk", None),
        username,
    )
    return bot_user


def _username(actor: Any) -> str:
    return str(getattr(actor, "get_username", lambda: "")() or "")[:150]


def _journal(action: str, *, actor: Any, bot_user: Any, reason: str) -> None:
    """Строка журнала: кто, кого, за что. Телефона здесь нет (DRF-1039)."""
    write_audit(
        action,
        target="identity.botuser",
        target_id=getattr(bot_user, "pk", None),
        payload={
            "actor_pk": str(getattr(actor, "pk", "") or ""),
            "actor_username": _username(actor),
            "reason": reason,
            "channel": getattr(bot_user, "channel", ""),
            "channel_user_id": getattr(bot_user, "channel_user_id", ""),
            "source": "admin_client_card",
        },
    )
