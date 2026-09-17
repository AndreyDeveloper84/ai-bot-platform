"""Один путь подключения мастера к салону — фасад над существующими авторитетами.

Поток A, пункт 3 (`CURRENT_DECISIONS_2026-09-16.md` §19): «фасад
``onboard_specialist_to_tenant``, зовущий существующие авторитеты и **не
скрывающий** их отказов». Его ждут двое — «Команда» в Mini App салона и
Django Admin для Platform Operations — и оба обязаны звать один путь, иначе
подключение мастера снова станет двумя операциями с разными правилами.

### Что фасад НЕ делает — и почему это не недоделка

* **Не заводит строку каталога.** ``CatalogMaster`` рождается в каталоге и
  приезжает синком, либо выписывается приглашением (``views_invite``); все
  четыре мастера пилота уже существуют, а дубль был бы невидим зеркалу
  записи (докстринг ``staff_invites``). Фасад берёт **существующую** строку.
* **Не заводит `TenantStaff`.** Роль мастера живёт на
  ``CatalogMaster.linked_bot_user`` (ADR-0008, решение 2); «членство»
  специалиста — это связь, а не строка в таблице сотрудников.
* **Не заводит `SpecialistProfile` в каталоге.** У бота ноль определений этой
  модели; дверь на этом пути — только у соло-кабинета (докстринг
  ``apps/catalog/identity.py``). Если identity создать нельзя, фасад говорит
  это **по имени** (``creation_unavailable``), а не молчит.
* **Не считает готовность заново.** Ответ «продаётся ли» — один,
  :func:`apps.catalog.master_state.sale_block`; шестого определения
  готовности здесь не появляется. Пятипунктовый чек-лист
  ``build_readiness`` — экран самого мастера (ходит в сеть за часами),
  в результат подключения не входит.

### Пять фаз и что каждая пишет

=================  ==============================================  ===========================
фаза               авторитет                                       пишет
=================  ==============================================  ===========================
контекст актора    :class:`OnboardingActor` (решение владельца:    ничего; отказ —
                   Platform Ops → ``cross_tenant=True``, салонный   :class:`ForeignTenantRefused`
                   админ → только свой тенант)
tenant активен     ``Tenant.is_active`` (§9 промпта: inactive      ничего; отказ —
                   tenant = parent kill-switch, активировать       :class:`TenantInactive`
                   мастера операторским действием нельзя)
membership         :func:`~apps.identity.services.staff_invites.   ``linked_bot_user``,
                   link_master_to_person` — то же ядро, что у      ``invite_status``, ``mode``,
                   кода приглашения; отказы те же:                 ``is_active``, ``accepted_at``
                   ``wrong_recipient``, ``person_already_master``,  (ставит ``save()`` модели)
                   ``invite_master_missing``
catalog identity   :func:`~apps.catalog.identity.                  ``catalog_specialist_id``
                   ensure_catalog_specialist_identity` —           (reuse / heal / create)
                   reuse → heal → create → именованный отказ
readiness          :func:`~apps.catalog.master_state.sale_block`   ничего — проекция
аудит              :func:`~apps.audit.services.write_audit`        одна строка на вызов
=================  ==============================================  ===========================

### Почему отказ identity — результат, а не исключение

Отказы **до** записи (чужой тенант, неактивный тенант, чужая карточка, человек
уже мастер) — исключения: ничего не записано, вызывающий обязан остановиться.

Отказ identity приходит **после** того, как связь уже зафиксирована, и
откатывать её нельзя по трём причинам. Первая — у салонного мастера двери
создания identity на этом пути нет вовсе (``creation_unavailable`` — норма, а
не сбой), и откат означал бы «салонного мастера подключить невозможно
никогда». Вторая — ``provision_catalog_workspace`` пишет машинную причину
отказа на связь, и откат стёр бы ровно ту запись, ради которой оператор туда
смотрит (тот же довод, что в ``apps/catalog/identity.py``). Третья —
повторный вызов после того, как оператор завёл identity руками,
**идемпотентен**: membership ответит ``already_linked``, identity — ``reuse``,
и статус станет ``success``. Поэтому исход identity едет в
:class:`OnboardingResult` **по имени**, а ``status`` бывает ``success`` только
при подтверждённой identity — скрытого частичного успеха нет: вызывающий
видит и то, что связь есть, и то, что identity нет, и почему.

### Аудит пишется после результата, не с попыткой

Строка описывает исход (обе фазы записи, причина отказа, ``sale_block``), а
не намерение. Цена названа: процесс, умерший между фиксацией связи и
записью аудита, оставит связь без строки. Строка аудита при этом — не
единственный след: ``accepted_at`` на карточке и лог
``identity.specialist_onboarding.done`` остаются.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Literal, get_args
from uuid import UUID

from django.db import transaction

from apps.catalog.identity import (
    CatalogIdentity,
    CatalogIdentityUnavailable,
    ensure_catalog_specialist_identity,
)
from apps.catalog.master_state import SaleBlock, sale_block
from apps.identity.services.staff_invites import link_master_to_person

logger = logging.getLogger(__name__)

#: Поверхность-источник — из промпта владельца (§8 «source_surface»). Закрытый
#: перечень: строка аудита обязана позволять найти актора без обращения к
#: контексту, а свободная строка здесь превратилась бы в «admin», «Admin»,
#: «django» — три написания одного места.
SourceSurface = Literal["django_admin", "operations_tool", "salon_miniapp"]
ALL_SOURCE_SURFACES: Final[tuple[str, ...]] = get_args(SourceSurface)

#: Исход подключения. ``success`` — ТОЛЬКО при подтверждённой catalog identity
#: (§15.10: «onboarding result не SUCCESS, если catalog identity не создана»).
OnboardingStatus = Literal["success", "identity_unavailable"]

STATUS_SUCCESS: Final = "success"
STATUS_IDENTITY_UNAVAILABLE: Final = "identity_unavailable"

#: Состояние связи до и после — словами, которые читает оператор.
MEMBERSHIP_NONE: Final = "none"
MEMBERSHIP_LINKED: Final = "linked"
MEMBERSHIP_LINKED_NOW: Final = "linked_now"
MEMBERSHIP_ALREADY_LINKED: Final = "already_linked"


class OnboardingRefused(Exception):
    """Отказ ДО записи: ничего не изменилось, продолжать нельзя.

    ``slug`` — устойчивое имя для ветвления вызывающего, как у
    :class:`apps.identity.services.staff_invites.InviteError`. Отказы самой
    связи (``wrong_recipient``, ``person_already_master``,
    ``invite_master_missing``) сюда НЕ переупаковываются: это тот же словарь,
    которым отвечает дверь кода, и второго не заводится.
    """

    slug = "onboarding_refused"


class ForeignTenantRefused(OnboardingRefused):
    """Салонный админ полез не в свой салон.

    Решение владельца 16.09: ``target_tenant`` — аргумент операции, а не поле
    контекста; при ``cross_tenant=False`` сервис требует ``target ==
    current_tenant`` и отказывает иначе. Это то, что превращает соглашение в
    сторож: чужой салон становится красным, а не незамеченным.
    """

    slug = "foreign_tenant"


class TenantInactive(OnboardingRefused):
    """Салон выключен — parent kill-switch (§9 промпта владельца).

    Связь ставит ``is_active=True`` на карточку; на неактивном салоне это было
    бы «случайно активировать через operator action». Реактивация салона —
    отдельное явное действие Platform Operations, не побочный эффект.
    """

    slug = "tenant_inactive"


class PersonInOtherTenant(OnboardingRefused):
    """``BotUser`` принадлежит другому тенанту.

    Строки ``BotUser`` уникальны по ``(tenant, channel, channel_user_id)`` —
    у одного человека по строке на салон. Связать карточку салона A со
    строкой салона B значит сделать её невидимой для ``resolve_role``,
    который фильтрует по тенанту самой строки (докстринг
    ``redeem_staff_invite``): человек услышал бы «вы мастер», а на следующем
    сообщении остался бы клиентом.
    """

    slug = "person_in_other_tenant"


class MasterInOtherTenant(OnboardingRefused):
    """Карточка мастера принадлежит другому тенанту, чем целевой."""

    slug = "master_in_other_tenant"


@dataclass(frozen=True)
class OnboardingActor:
    """Кто подключает и с какой поверхности — один контекст на обе двери.

    Решение владельца (16.09, F10): Platform Operations → ``cross_tenant=True``,
    салонный админ → только ``current_tenant``. Инвариант
    ``cross_tenant=True ⇒ current_tenant_id is None``: у оператора платформы
    цели в контексте нет, она — аргумент операции.

    ``audit_label`` обязан позволять найти актора **без обращения к
    контексту** — без исключения для оператора платформы, у которого
    ``BotUser`` нет вовсе (замер ayla-96: его строка в базе иначе неотличима
    от заведённой миграцией). Правило одно на обе ветви, потому что правило
    с исключением дрейфует. Что класть: ``django_admin:user=<pk>``,
    ``bot_user:<uuid>`` — идентификатор, не имя и не телефон.
    """

    surface: SourceSurface
    audit_label: str
    cross_tenant: bool
    current_tenant_id: UUID | None = None
    actor_id: UUID | None = None
    capability: str = ""

    def __post_init__(self) -> None:
        if self.surface not in ALL_SOURCE_SURFACES:
            raise ValueError(f"unknown source surface {self.surface!r}")
        if not (self.audit_label or "").strip():
            raise ValueError("audit_label is required: the row must name its actor")
        if self.cross_tenant and self.current_tenant_id is not None:
            raise ValueError("cross_tenant=True carries no current tenant — target is an argument")
        if not self.cross_tenant and self.current_tenant_id is None:
            raise ValueError("a tenant-scoped actor must name its current tenant")

    def may_act_on(self, tenant_id: Any) -> bool:
        return self.cross_tenant or self.current_tenant_id == tenant_id


@dataclass(frozen=True)
class OnboardingResult:
    """Что произошло — обе фазы записи по отдельности, потом общий статус.

    ``membership`` и ``identity``/``identity_reason`` отдаются врозь именно
    потому, что при ``identity_unavailable`` связь **есть**, и вызывающий
    обязан показать оба факта: «подключён; identity нет: <причина>». Одно
    слово «не удалось» спрятало бы первую половину.
    """

    status: OnboardingStatus
    tenant_id: UUID
    master_id: UUID
    bot_user_id: UUID
    membership: str
    identity: CatalogIdentity | None
    identity_reason: str | None
    sale_block: SaleBlock | None

    @property
    def success(self) -> bool:
        return self.status == STATUS_SUCCESS

    @property
    def specialist_id(self) -> str | None:
        return self.identity.specialist_id if self.identity is not None else None


def onboard_specialist_to_tenant(
    *,
    tenant: Any,
    master: Any,
    bot_user: Any,
    actor: OnboardingActor,
    http_client: Any | None = None,
) -> OnboardingResult:
    """Подключить человека как мастера салона одним путём.

    Идемпотентно в том смысле, который важен оператору: повтор для уже
    подключённого мастера отвечает ``already_linked`` + ``reuse``, ничего не
    переписывает и не заводит второй identity (три слоя защиты от дубля —
    докстринг ``apps/catalog/identity.py``).

    Args:
      tenant: целевой салон — **аргумент**, не поле контекста.
      master: существующая строка ``CatalogMaster`` этого салона.
      bot_user: строка ``BotUser`` человека **в этом салоне**.
      actor: кто и откуда — :class:`OnboardingActor`.
      http_client: подмена клиента каталога (тесты); прод — ``None``.

    Returns:
      :class:`OnboardingResult`; ``success`` только при подтверждённой identity.

    Raises:
      ForeignTenantRefused, TenantInactive, PersonInOtherTenant,
      MasterInOtherTenant — до записи, слуги на классе.
      InviteMasterMissing, MasterAlreadyLinked, PersonAlreadyMaster — из ядра
      связи, те же слуги, что у двери кода; связь откатывается целиком.
    """

    from apps.audit.services import write_audit
    from apps.events.vocabulary import STAFF_SPECIALIST_ONBOARDED
    from apps.tenancy.context import tenant_scope

    tenant_id = tenant.id

    # 1. Контекст актора — до любого чтения строк.
    if not actor.may_act_on(tenant_id):
        logger.warning(
            "identity.specialist_onboarding.foreign_tenant surface=%s actor=%s target=%s",
            actor.surface,
            actor.audit_label,
            tenant_id,
        )
        raise ForeignTenantRefused("actor may not act on this tenant")

    # 2. Parent kill-switch. Читается поле, а не менеджер: ``Tenant.objects``
    #    прячет неактивные, и «не нашли» здесь читалось бы как «нет салона».
    if not getattr(tenant, "is_active", False):
        raise TenantInactive("tenant is inactive — an operator reactivates it explicitly first")

    # 3. Обе строки — этого салона. Проверка ДО блокировки: под
    #    ``select_for_update`` фильтр по тенанту уже стоит в ядре, но отказ
    #    там читался бы как «карточка исчезла», а не «чужой салон».
    if getattr(master, "tenant_id", None) != tenant_id:
        raise MasterInOtherTenant("catalog master belongs to another tenant")
    if getattr(bot_user, "tenant_id", None) != tenant_id:
        raise PersonInOtherTenant("bot user row belongs to another tenant")

    membership_before = (
        MEMBERSHIP_LINKED if getattr(master, "linked_bot_user_id", None) else MEMBERSHIP_NONE
    )

    # 4. Membership — то же ядро, что у кода приглашения; своя транзакция,
    #    чтобы отказ ядра откатил ровно связь и ничего больше.
    with transaction.atomic():
        link = link_master_to_person(master_id=master.pk, tenant_id=tenant_id, bot_user=bot_user)
    membership = MEMBERSHIP_ALREADY_LINKED if link.already_had_role else MEMBERSHIP_LINKED_NOW

    # Ядро писало по своей копии строки под блокировкой — перечитать, чтобы
    # identity и гейт смотрели на то, что действительно легло.
    master.refresh_from_db()

    # 5. Catalog identity — вне транзакции связи намеренно (см. докстринг
    #    модуля). Отказ — по имени, в результат.
    identity: CatalogIdentity | None = None
    identity_reason: str | None = None
    try:
        identity = ensure_catalog_specialist_identity(
            master, bot_user=bot_user, http_client=http_client
        )
    except CatalogIdentityUnavailable as exc:
        identity_reason = exc.reason
        logger.warning(
            "identity.specialist_onboarding.identity_unavailable tenant=%s master=%s reason=%s",
            tenant_id,
            master.pk,
            exc.reason,
        )

    # 6. Готовность — единственный гейт продажи, ничего не пишет.
    master.refresh_from_db()
    block = sale_block(master)

    status: OnboardingStatus = (
        STATUS_SUCCESS if identity is not None else STATUS_IDENTITY_UNAVAILABLE
    )
    result = OnboardingResult(
        status=status,
        tenant_id=tenant_id,
        master_id=master.pk,
        bot_user_id=bot_user.pk,
        membership=membership,
        identity=identity,
        identity_reason=identity_reason,
        sale_block=block,
    )

    # 7. Аудит — после результата. ``write_audit`` читает ``current_tenant()``,
    #    а оператор платформы приходит без скоупа.
    with tenant_scope(tenant):
        write_audit(
            STAFF_SPECIALIST_ONBOARDED,
            target="catalog.CatalogMaster",
            target_id=master.pk,
            payload={
                "surface": actor.surface,
                "actor_label": actor.audit_label,
                "capability": actor.capability,
                "cross_tenant": actor.cross_tenant,
                "person_id": str(bot_user.pk),
                "membership_before": membership_before,
                "membership_after": membership,
                "identity_status": status,
                "identity_reason": identity_reason,
                "identity_created": identity.created if identity is not None else None,
                "specialist_id": result.specialist_id,
                "sale_block": block,
            },
            actor_id=actor.actor_id,
        )

    logger.info(
        "identity.specialist_onboarding.done tenant=%s master=%s person=%s surface=%s "
        "membership=%s status=%s reason=%s sale_block=%s",
        tenant_id,
        master.pk,
        bot_user.pk,
        actor.surface,
        membership,
        status,
        identity_reason or "-",
        block or "-",
    )
    return result


__all__ = [
    "ALL_SOURCE_SURFACES",
    "MEMBERSHIP_ALREADY_LINKED",
    "MEMBERSHIP_LINKED",
    "MEMBERSHIP_LINKED_NOW",
    "MEMBERSHIP_NONE",
    "STATUS_IDENTITY_UNAVAILABLE",
    "STATUS_SUCCESS",
    "ForeignTenantRefused",
    "MasterInOtherTenant",
    "OnboardingActor",
    "OnboardingRefused",
    "OnboardingResult",
    "OnboardingStatus",
    "PersonInOtherTenant",
    "SourceSurface",
    "TenantInactive",
    "onboard_specialist_to_tenant",
]
