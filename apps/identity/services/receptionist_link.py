"""Провести ресепшна салона в каталог — действие владельца в Mini App (DRF-2833, B3).

Решение владельца 07.10 (лист решений п.22, вариант B): «администратор
назначает ресепшна отдельным действием в Mini App». Главное окно 20.10
развело механику:

* **Кого.** Только человека, у которого УЖЕ есть активная ``TenantStaff``
  ``receptionist`` в этом салоне — роль в боте выдают код приглашения и
  «Сменить роль», и они не меняются. Это действие дозаводит каталожную
  половину, без которой гейт бота (``require_booking_desk``) пускает, а
  каталог на стойке записи отвечает 403. Выбрать произвольного клиента
  нельзя — новой поверхности с людьми салона здесь нет.
* **Кто.** Владелец салона (ruling 22.09: выдача ролей — у владельца;
  расширение на admin — отдельное решение). Каталог ещё раз проверяет его
  сам: назначающий — активный admin салона В КАТАЛОГЕ
  (``assigner_not_admin``), owner в боте ≠ admin в каталоге. Fail-closed.
* **Снять.** Не здесь: ``staff/revoke/`` снимает ``TenantStaff``, и гейт
  бота закрывает стойку сразу. Каталожная учётка ресепшна без пароля и
  телефона, её личность в каталог присылает только бот — дороги мимо бота
  нет. Повторное назначение тем же ключом — ``replayed``.

Ruling DRF-2085 п.1 (ввод кода каталог не зовёт) не задет: код по-прежнему
``link_catalog=False``, это отдельная дверь с живым назначающим.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction

from apps.identity.services import salon_admin_link
from apps.identity.services.staff_invites import InviteError
from apps.tenancy.models import TenantStaff

logger = logging.getLogger(__name__)


class NotAReceptionist(InviteError):
    """У человека нет активной роли ресепшна в этом салоне — проводить нечего."""

    slug = "not_a_receptionist"


@dataclass(frozen=True)
class ReceptionistLinkResult:
    #: ``False`` — каталог ответил ``replayed``: стойка уже была открыта.
    created: bool
    correlation_id: str


def link_receptionist_to_catalog(
    *,
    tenant: Any,
    person: Any,
    assigned_by: Any,
    actor_label: str,
) -> ReceptionistLinkResult:
    """Открыть ``person`` стойку записи ``tenant`` в каталоге от имени ``assigned_by``.

    Raises:
      NotAReceptionist — нет активной ``TenantStaff`` ``receptionist`` здесь.
      CatalogAdminLinkRefused — каталог отказал (причина по имени); отказ
        записан в аудит после отката.
    """

    with salon_admin_link.recording_refusals(), transaction.atomic():
        # Под блокировкой: снятие роли параллельно с проводкой не должно
        # оставить каталожную половину человеку, у которого роли уже нет.
        held = (
            TenantStaff.all_tenants.select_for_update()
            .filter(
                tenant_id=tenant.id,
                bot_user=person,
                role=TenantStaff.Role.RECEPTIONIST,
                deactivated_at__isnull=True,
            )
            .first()
        )
        if held is None:
            raise NotAReceptionist("the person holds no active receptionist role here")
        outcome = salon_admin_link.ensure_catalog_salon_receptionist(
            tenant=tenant,
            bot_user=person,
            assigned_by=assigned_by,
            actor_label=actor_label,
        )
        if outcome.created:
            salon_admin_link.audit_linked(
                outcome,
                tenant=tenant,
                bot_user=person,
                actor_label=actor_label,
                role="receptionist",
            )

    logger.info(
        "identity.receptionist_link.%s tenant=%s person=%s by=%s correlation_id=%s",
        "created" if outcome.created else "replayed",
        tenant.slug,
        person.pk,
        assigned_by.pk,
        outcome.correlation_id,
    )
    return ReceptionistLinkResult(created=outcome.created, correlation_id=outcome.correlation_id)


__all__ = ["NotAReceptionist", "ReceptionistLinkResult", "link_receptionist_to_catalog"]
