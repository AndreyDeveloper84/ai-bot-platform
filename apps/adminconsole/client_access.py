"""Пропуск к данным клиента и журнал доступа — правила (DRF-1514).

Модуль отвечает на четыре вопроса и больше ни на что:

* кому ограничения не писаны (:func:`is_unrestricted`);
* какие пропуска у человека действуют прямо сейчас
  (:func:`active_grants`, :func:`granted_client_ids`);
* как пропуск выдаётся (:func:`open_access`);
* как просмотр и отказ попадают в журнал (:func:`record_view`,
  :func:`record_denial`).

### Что считается «обращением»

``handoff.AdminTask`` — строка, которая появляется ровно тогда, когда
бот вышел из автономного разговора и позвал человека: клиент нажал
«позвать человека», сработал классификатор жалобы, всплыл медицинский
красный флаг, либо оператор завёл задачу руками. Это единственная в
платформе запись «клиент обратился, нужен человек», и она уже несёт
и клиента, и салон, и разговор.

Поэтому пропуск выдаётся **по обращению**, а клиент и салон берутся из
него, а не выбираются вручную. Выбрать клиента отдельно от обращения
нельзя — иначе «поиск конкретного клиента» снова превращается в
свободный поиск по всем клиентам всех салонов.

### Почему у пропуска есть срок

Пропуск без срока — это то же право «смотреть всё», просто выданное
один раз и навсегда. :data:`DEFAULT_TTL_MINUTES` задаёт, сколько
пропуск живёт; после этого разбор продолжается новым пропуском с новой
причиной. Срок настраивается
``settings.ADMINCONSOLE_CLIENT_ACCESS_TTL_MINUTES``.

### Почему журнал пишется до показа, а не после

:func:`record_view` вызывается **перед** тем, как экран отдаст данные.
Если запись в журнал не прошла, страница падает и данных не показывает.
Обратный порядок дал бы просмотр без следа ровно в тот момент, когда
журнал сломан, — то есть тогда, когда след нужнее всего.

### Телефона тут нет

:func:`client_label` собирается из ``display_name`` / ``client_name`` /
идентификатора канала. Телефон в подпись не попадает (DRF-1039), и
подпись — единственное, что уходит в пропуск и журнал.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.adminconsole.models import ClientDataAccessGrant, ClientDataAccessLog

logger = logging.getLogger(__name__)

#: Сколько живёт пропуск, если настройка не задана.
DEFAULT_TTL_MINUTES = 60

#: Короче этого причина ничего не объясняет тому, кто будет читать журнал.
MIN_REASON_LENGTH = 12

#: Настройка со сроком жизни пропуска.
TTL_SETTING = "ADMINCONSOLE_CLIENT_ACCESS_TTL_MINUTES"


class ClientAccessError(ValueError):
    """Пропуск выдать нельзя — и вот почему."""


def ttl() -> timedelta:
    """Срок жизни пропуска. Кривая настройка — не повод открыть навсегда."""
    raw = getattr(settings, TTL_SETTING, DEFAULT_TTL_MINUTES)
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        logger.warning("adminconsole.client_access.bad_ttl value=%r", raw)
        minutes = DEFAULT_TTL_MINUTES
    if minutes <= 0:
        logger.warning("adminconsole.client_access.non_positive_ttl value=%r", raw)
        minutes = DEFAULT_TTL_MINUTES
    return timedelta(minutes=minutes)


def is_unrestricted(user: Any) -> bool:
    """Суперпользователь (владелец) ходит как ходил.

    Отвечает ``False`` на всё, что не похоже на вошедшего человека, —
    при сомнении закрываем, а не открываем.
    """
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    )


def client_label(bot_user: Any) -> str:
    """Подпись клиента для пропуска и журнала. Без телефона (DRF-1039)."""
    if bot_user is None:
        return ""
    for attr in ("display_name", "client_name", "channel_user_id"):
        value = (getattr(bot_user, attr, "") or "").strip()
        if value:
            return value[:200]
    return ""


def active_grants(user: Any) -> QuerySet[ClientDataAccessGrant]:
    """Непросроченные пропуска этого человека."""
    user_pk = getattr(user, "pk", None)
    if user_pk is None:
        return ClientDataAccessGrant.objects.none()
    return ClientDataAccessGrant.objects.filter(
        actor_id=user_pk, expires_at__gt=timezone.now()
    ).order_by("-created_at")


def granted_client_ids(user: Any) -> list[UUID]:
    """Клиенты, по которым у человека прямо сейчас открыт доступ."""
    return list(active_grants(user).values_list("client_id", flat=True))


def grant_for_client(user: Any, client_id: Any) -> ClientDataAccessGrant | None:
    """Действующий пропуск на конкретного клиента, если он есть."""
    if client_id is None:
        return None
    return active_grants(user).filter(client_id=client_id).first()


def check_reason(reason: str) -> str:
    """Причина есть и она что-то объясняет. Иначе отказ, а не пустая строка."""
    text = (reason or "").strip()
    if not text:
        raise ClientAccessError(
            "Причина просмотра не указана. Переписка и профиль клиента "
            "открываются только с причиной — её прочитает человек, "
            "разбирающий журнал доступа."
        )
    if len(text) < MIN_REASON_LENGTH:
        raise ClientAccessError(
            f"Причина короче {MIN_REASON_LENGTH} символов — по такой записи "
            "в журнале нельзя понять, зачем открывали переписку. Напишите, "
            "с чем вы работаете."
        )
    return text


def open_access(
    *,
    actor: Any,
    admin_task: Any,
    reason: str,
    grant: ClientDataAccessGrant | None = None,
) -> ClientDataAccessGrant:
    """Выдать пропуск к данным клиента этого обращения.

    Клиент и салон берутся из обращения — параметрами их передать
    нельзя намеренно: пропуск «на клиента вообще» вернул бы свободный
    поиск по всем клиентам всех салонов.

    ``grant`` — несохранённый экземпляр из формы админки. Экран отдаёт
    свой объект, чтобы Django записал его в ``LogEntry`` и вернул на
    страницу обращения; проставляет поля всё равно этот код, а не форма.
    """
    text = check_reason(reason)
    if admin_task is None:
        raise ClientAccessError(
            "Обращение не выбрано. Переписка и профиль открываются только "
            "по обращению, с которым вы работаете."
        )
    client_pk = getattr(admin_task, "bot_user_id", None)
    if client_pk is None:
        raise ClientAccessError(
            "У обращения нет клиента — открывать нечего. Это похоже на "
            "испорченную строку очереди, покажите её владельцу."
        )

    label = client_label(getattr(admin_task, "bot_user", None))
    username = str(getattr(actor, "get_username", lambda: "")() or "")

    grant = grant if grant is not None else ClientDataAccessGrant()
    grant.actor = actor
    grant.actor_username = username
    grant.admin_task = admin_task
    grant.client_id = client_pk
    grant.client_label = label
    grant.tenant_slug = _tenant_slug(admin_task)
    grant.reason = text
    grant.expires_at = timezone.now() + ttl()
    grant.save()
    _write_log(
        outcome=ClientDataAccessLog.Outcome.GRANTED,
        actor=actor,
        grant=grant,
        screen="",
        object_id="",
        detail="",
    )
    logger.info(
        "adminconsole.client_access.granted actor=%s task=%s client=%s",
        username,
        getattr(admin_task, "pk", None),
        client_pk,
    )
    return grant


def record_view(
    *,
    actor: Any,
    grant: ClientDataAccessGrant | None,
    screen: str,
    object_id: str = "",
) -> None:
    """Записать открытый экран. Вызывается ДО показа данных."""
    _write_log(
        outcome=ClientDataAccessLog.Outcome.OPENED,
        actor=actor,
        grant=grant,
        screen=screen,
        object_id=object_id,
        detail="",
    )


def record_denial(*, actor: Any, screen: str, object_id: str = "", detail: str = "") -> None:
    """Записать отказ. Попытка полистать чужое — тоже событие доступа."""
    _write_log(
        outcome=ClientDataAccessLog.Outcome.DENIED,
        actor=actor,
        grant=None,
        screen=screen,
        object_id=object_id,
        detail=detail[:300],
    )


def _tenant_slug(admin_task: Any) -> str:
    try:
        tenant = admin_task.tenant
    except Exception:  # noqa: BLE001 — салон мог уехать; подпись не критична
        return ""
    return str(getattr(tenant, "slug", "") or "")[:100]


def _write_log(
    *,
    outcome: str,
    actor: Any,
    grant: ClientDataAccessGrant | None,
    screen: str,
    object_id: str,
    detail: str,
) -> None:
    username = str(getattr(actor, "get_username", lambda: "")() or "")
    ClientDataAccessLog.objects.create(
        outcome=outcome,
        actor_username=username,
        actor_pk=str(getattr(actor, "pk", "") or ""),
        screen=screen[:120],
        object_id=str(object_id or "")[:200],
        client_id=getattr(grant, "client_id", None),
        client_label=getattr(grant, "client_label", "") or "",
        tenant_slug=getattr(grant, "tenant_slug", "") or "",
        admin_task_id=getattr(grant, "admin_task_id", None),
        grant_id=getattr(grant, "id", None),
        reason=getattr(grant, "reason", "") or "",
        detail=detail,
    )
