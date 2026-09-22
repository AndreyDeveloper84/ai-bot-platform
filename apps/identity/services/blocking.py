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

### Вход (DRF-2276, CD §72 п.15)

Блокировка — операция оператора платформы (право
``tenancy.platform_operations``), и её эффект начинается на входе, а не
только на выходе. Каналы спрашивают :func:`blocked_since` сразу после гейта
безопасности:

* кризис и неотложка (включая red flag классификатора) заблокированному
  отвечаются всё равно — N-1 (CD §67), гейт раньше блокировки;
* всё остальное — :data:`BLOCK_NOTICE_TEXT` раз за эпизод
  (:func:`claim_block_notice`) и больше ничего: ни навыков, ни модели.

Блокировка платформенная: достаточно одной заблокированной строки человека
в канале (любой салон или витринный бот) — та же выборка, что у забора на
выходе.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.db.models import Max, Q
from django.utils import timezone

from apps.audit.services import write_audit

logger = logging.getLogger(__name__)

#: Короче этого причина ничего не объясняет читающему журнал.
MIN_REASON_LENGTH = 12

#: Что заблокированный слышит в ответ — раз за эпизод блокировки (DRF-2276).
#: Черновик владельцу (VQ, CD §72 п.15): до его слова текст не окончательный.
#: CD §76 №35 — при угрозе жизни фраза прямо называет 112, и раньше, чем
#: предлагает написать: звонок не ждёт переписки. Последняя фраза — не
#: вежливость, а правда: кризис и неотложка заблокированному отвечаются всё
#: равно (N-1, CD §67). Голос бота — на «ты».
BLOCK_NOTICE_TEXT = (
    "Сейчас я не могу продолжить этот разговор. "
    "Если есть угроза жизни — звони 112. "
    "Если что-то срочное со здоровьем — напиши, я подскажу, куда обратиться."
)

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


def blocked_since(*, channel: str, channel_user_id: str) -> datetime | None:
    """Время действующей блокировки человека в канале или ``None``.

    Любая строка ``BotUser`` этого человека в канале (салонная или
    витринная) — платформенная блокировка; при нескольких берётся самая
    поздняя: от неё отсчитывается эпизод фразы.

    Сбой запроса — fail-open, как у забора на выходе
    (``max/outbound._recipient_blocked``): блокировка — рабочее действие
    поддержки, не контур безопасности. Контур безопасности (гейт) к этому
    моменту уже отработал.
    """
    from apps.identity.models import BotUser

    try:
        return BotUser.all_tenants.filter(
            channel=channel,
            channel_user_id=str(channel_user_id),
            blocked_at__isnull=False,
        ).aggregate(latest=Max("blocked_at"))["latest"]
    except Exception:  # noqa: BLE001 — см. docstring: fail-open, но с криком в лог
        logger.exception("identity.blocking.blocked_since_failed channel=%s", channel)
        return None


def claim_block_notice(bot_user: Any, *, since: datetime) -> bool:
    """Забрать право сказать фразу блокировки в этом эпизоде. True — говорить.

    Эпизод — действующая блокировка с ``since``: метка строки раньше неё (или
    пусто) — фраза ещё не звучала. Условный UPDATE, а не чтение-запись: кто
    выиграл его, тот и говорит, остальные находят ноль строк и молчат — тот же
    приём, что у ``handoff.silence.notify_silence``. Метка ставится ДО
    отправки: сказать дважды хуже, чем один раз не сказать.
    """
    from apps.identity.models import BotUser

    claimed = (
        BotUser.all_tenants.filter(pk=bot_user.pk)
        .filter(Q(block_notice_at__isnull=True) | Q(block_notice_at__lt=since))
        .update(block_notice_at=timezone.now())
    )
    return bool(claimed)


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
