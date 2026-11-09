"""Дозвон связи соло-мастера с каталогом — без оператора и без повторной регистрации.

Решение владельца §77 п.38 (24.09.2026): «мне не надо участия человека в
регистрации мастеров». DRF-2450 исполнил его для первого хода: провижининг →
связь личности → запись ключа, всё в ответе на «Создать мой профиль». Но у
первого хода бывает неудача (каталог не ответил, сеть оборвалась), и до этого
модуля у неудачи не было продолжения, доступного самому человеку.

### Что было не так — замерено, а не предположено

Повтор жил в одном месте: ветка ``created=False`` в
``salon_handler._register_solo_provider``. Дойти до неё можно только нажатием
``cb:solo:confirm`` на пути незнакомца — а человек с кабинетом незнакомцем уже
не бывает: у него рабочая строка владельца, пре-чек (``salon_entry``) отвечает
ему ``NOT_LINKED``, и «Повторить проверку» перечитывает состояние, ничего не
дозванивая. Ветка повтора была достижима из тестов и недостижима из бота.
Подметальщик ``catalog.tasks`` берёт только приглашённых (``invited_at``), а у
соло-строки приглашения нет.

Замер пилота 09.11.2026: три соло-кабинета; зарегистрированный 06.10 (после
DRF-2450) связан автоматически, два более ранних (16.09 и 23.09) стоят в
``PENDING`` с ``proxy_identity`` — семь недель, и довести их мог только
оператор действием в двух админках.

### Что делает модуль

Одна дорожка повтора на всех вызывающих: провижининг (уже подтверждённый не
зовётся), затем — только для ``PENDING`` — связь личности, запись ключа и исход
на связь. Отказ оператора (``REJECTED``) повтор не отменяет никогда: это
решение человека о человеке, а не сбой сети.

Модуль ничего не решает о готовности и ничего не говорит человеку — он пробует
и возвращает машинные причины. Слова выбирает вызывающий по ``status``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SoloWorkspaceLink:
    """Карточка мастера соло-кабинета и её связь личности."""

    master: Any
    link: Any

    @property
    def status(self) -> str:
        return str(self.link.status)

    @property
    def is_pending(self) -> bool:
        from apps.identity.models import SoloIdentityLink

        return self.link.status == SoloIdentityLink.Status.PENDING

    @property
    def is_rejected(self) -> bool:
        from apps.identity.models import SoloIdentityLink

        return self.link.status == SoloIdentityLink.Status.REJECTED


@dataclass(frozen=True)
class SoloLinkRetry:
    """Исход повтора: что пробовали и чем кончилось — машинными именами."""

    #: Дошли ли до связи личности. ``False`` — провижининг отказал или связь
    #: не в ожидании (уже ``LINKED`` либо отклонена оператором).
    attempted: bool
    status: str
    catalog_refusal: str | None = None
    identity_link_refusal: str | None = None
    link_refusal: str | None = None

    @property
    def linked(self) -> bool:
        from apps.identity.models import SoloIdentityLink

        return self.status == SoloIdentityLink.Status.LINKED


def solo_workspace_link(bot_user: Any) -> SoloWorkspaceLink | None:
    """Связь соло-кабинета этой рабочей строки; ``None`` — человек не соло-мастер.

    Признак — строка ``SoloIdentityLink`` у карточки мастера, привязанной к
    этой строке ``BotUser``: её заводит только регистрация соло
    (``solo_identity_link.open_link``), у салонного мастера её нет по
    построению. Карточка читается в тенанте строки, тенантным менеджером.
    """

    from apps.catalog.models import CatalogMaster
    from apps.identity.models import SoloIdentityLink
    from apps.tenancy.context import tenant_scope

    tenant = getattr(bot_user, "tenant", None)
    if tenant is None:
        return None
    with tenant_scope(tenant):
        master = CatalogMaster.objects.filter(linked_bot_user=bot_user).first()
    if master is None:
        return None
    link = SoloIdentityLink.objects.filter(master=master).first()
    if link is None:
        return None
    return SoloWorkspaceLink(master=master, link=link)


def retry_solo_link(link: Any, *, master: Any, tenant: Any, bot_user: Any) -> SoloLinkRetry:
    """Повторить недоделанное: провижининг → связь личности → запись ключа.

    Лучшая попытка в том же смысле, что у первого хода: отказы каталога и сети
    возвращаются именами, а не исключениями. Что по-прежнему выходит
    исключением — ошибка настройки или своей базы (см.
    ``solo_link_attempt.attempt_solo_link``); вызывающий, которому нельзя
    падать, ловит её сам.
    """

    from apps.identity.models import SoloIdentityLink
    from apps.identity.services.solo_catalog_provisioning import provision_catalog_workspace

    catalog_refusal = provision_catalog_workspace(
        link,
        tenant=tenant,
        bot_user=bot_user,
        display_name=getattr(bot_user, "display_name", "") or master.name,
    )
    if catalog_refusal is not None or link.status != SoloIdentityLink.Status.PENDING:
        # Без подтверждённого профиля двери сверять не с чем; а связь не в
        # ожидании — либо готова, либо отклонена оператором, и бот её не трогает.
        return SoloLinkRetry(attempted=False, status=link.status, catalog_refusal=catalog_refusal)

    from apps.identity.services.solo_identity_link import record_attempt
    from apps.identity.services.solo_link_attempt import attempt_solo_link
    from apps.identity.services.specialist_identity_link import (
        bind_solo_identity_after_provisioning,
    )

    # Статус перечитывается ПОСЛЕ провижининга (до 30 с сети) и до двери: отказ
    # оператора, пришедший в это окно, должен остановить не только запись
    # статуса (её держит ``record_attempt``), но и побочные действия — привязку
    # личности в каталоге и запись настоящего ключа карточке и строке.
    link.refresh_from_db(fields=["catalog_specialist_id", "status"])
    if link.status != SoloIdentityLink.Status.PENDING:
        return SoloLinkRetry(attempted=False, status=link.status, catalog_refusal=catalog_refusal)
    identity_link_refusal = bind_solo_identity_after_provisioning(link, bot_user=bot_user)
    link_refusal = attempt_solo_link(master, bot_user)
    master.refresh_from_db(fields=["ayla_user_id"])
    record_attempt(link, refusal=link_refusal, ayla_user_id=master.ayla_user_id)
    logger.info(
        "identity.solo_link.retry master=%s status=%s identity_link_refusal=%s link_refusal=%s",
        getattr(master, "pk", None),
        link.status,
        identity_link_refusal,
        link_refusal,
    )
    return SoloLinkRetry(
        attempted=True,
        status=link.status,
        catalog_refusal=catalog_refusal,
        identity_link_refusal=identity_link_refusal,
        link_refusal=link_refusal,
    )


__all__ = [
    "SoloLinkRetry",
    "SoloWorkspaceLink",
    "retry_solo_link",
    "solo_workspace_link",
]
