"""Роли сотрудников салона из рук оператора — выдать, сменить (DRF-2082).

До этого листа роль в салоне появлялась только через код приглашения
(``staff_invites``) — оператор выписывал код с хоста, человек вводил его в
боте. Владелец потребовал управлять доступами из админки. Здесь два
действия над ``TenantStaff``, и оба зовут существующие авторитеты, а не
повторяют их:

* :func:`grant_role_by_operator` — ядро :func:`~apps.identity.services.
  staff_invites.grant_staff_role` (то же, что у кода приглашения: та же строка,
  те же два партиальных unique, тот же ответ «уже есть» под гонкой) + строка
  аудита с поверхностью и актором;
* :func:`change_staff_role` — в одной транзакции деактивирует активные строки
  человека (история остаётся, как у ``revoke_staff_access``) и выдаёт новую
  роль тем же ядром.

Отзыв доступа здесь не живёт — он уже есть:
:func:`apps.identity.services.staff_revoke.revoke_staff_access`.

### Fail-closed на владельце

Роль активного владельца не меняется этим путём (:class:`OwnerRoleLocked`).
Довод — из ``OwnerAlreadyExists``: «handover is deactivate-then-invite, not a
second row». Смена роли владельца одним действием оставила бы салон без
владельца — или с двумя, если новый уже назначен. Передача владения остаётся
явным путём из двух шагов, и этот модуль его не сокращает.

### Актор без ``BotUser``

Оператор платформы в ``TenantStaff.created_by`` не помещается (FK на
``BotUser``, у него его нет — замер ayla-96): его строка в базе неотличима от
заведённой миграцией. Авторство держит ``actor_label`` в аудите — правило одно
на все поверхности (тот же ``OnboardingActor``, что у ``specialist_onboarding``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.identity.models import BotUser
from apps.identity.services.specialist_onboarding import OnboardingActor
from apps.identity.services.staff_invites import RedeemResult, grant_staff_role
from apps.tenancy.models import Tenant, TenantStaff

logger = logging.getLogger(__name__)

#: Роли, которые оператор может выдать/сменить руками — словарь модели, не свой.
GRANTABLE_ROLES: tuple[str, ...] = tuple(TenantStaff.Role.values)


class StaffRoleError(Exception):
    """Отказ до записи; ``slug`` — устойчивое имя для вызывающего."""

    slug = "staff_role_error"


class UnknownRole(StaffRoleError):
    """Роль вне словаря ``TenantStaff.Role`` — отказ до чтения строк."""

    slug = "unknown_role"


class PersonInOtherTenant(StaffRoleError):
    """``BotUser`` из другого салона — строка была бы невидима ``resolve_role``."""

    slug = "person_in_other_tenant"


class ForeignTenantRefused(StaffRoleError):
    """Актор не вправе действовать в этом салоне (салонный админ — только свой)."""

    slug = "foreign_tenant"


class OwnerRoleLocked(StaffRoleError):
    """Роль активного владельца не меняется этим путём — передача владения отдельно."""

    slug = "owner_role_locked"


class NoActiveRole(StaffRoleError):
    """Менять нечего: активных строк у человека в этом салоне нет."""

    slug = "no_active_role"


@dataclass(frozen=True)
class RoleChange:
    tenant_id: Any
    bot_user_id: Any
    previous_roles: tuple[str, ...]
    role: str


def _check(tenant: Tenant, bot_user: BotUser, role: str, actor: OnboardingActor) -> None:
    if role not in GRANTABLE_ROLES:
        raise UnknownRole(f"role {role!r} is not in TenantStaff.Role")
    if not actor.may_act_on(tenant.id):
        raise ForeignTenantRefused("actor may not act on this tenant")
    if getattr(bot_user, "tenant_id", None) != tenant.id:
        raise PersonInOtherTenant("bot user row belongs to another tenant")


def _audit(
    action: str,
    *,
    tenant: Tenant,
    bot_user: BotUser,
    actor: OnboardingActor,
    payload: dict[str, Any],
) -> None:
    # Импорты на месте: audit тянет модели при загрузке, а этот модуль нужен
    # админке через urls.
    from apps.audit.services import write_audit
    from apps.tenancy.context import tenant_scope

    with tenant_scope(tenant):
        write_audit(
            action,
            target="identity.BotUser",
            target_id=bot_user.pk,
            payload={
                "surface": actor.surface,
                "actor_label": actor.audit_label,
                "capability": actor.capability,
                "person_id": str(bot_user.pk),
                **payload,
            },
            actor_id=actor.actor_id,
        )


def grant_role_by_operator(
    *, tenant: Tenant, bot_user: BotUser, role: str, actor: OnboardingActor
) -> RedeemResult:
    """Выдать роль руками — тем же ядром, что у кода приглашения.

    Повтор — ``already_had_role=True`` (вызывающий говорит «уже есть»), не
    дубль и не 500: то же под гонкой двух операторов, её держит база
    (``unique_active_staff_role``).

    Raises: UnknownRole, ForeignTenantRefused, PersonInOtherTenant,
    OwnerAlreadyExists (из ядра).
    """
    from apps.events.vocabulary import STAFF_ROLE_GRANTED

    _check(tenant, bot_user, role, actor)

    with transaction.atomic():
        result = grant_staff_role(
            tenant_id=tenant.id, bot_user=bot_user, role=role, created_by=None
        )

    _audit(
        STAFF_ROLE_GRANTED,
        tenant=tenant,
        bot_user=bot_user,
        actor=actor,
        payload={"role": role, "already_had_role": result.already_had_role},
    )
    logger.info(
        "identity.staff_roles.granted tenant=%s person=%s role=%s already=%s surface=%s",
        tenant.slug,
        bot_user.pk,
        role,
        result.already_had_role,
        actor.surface,
    )
    return result


def change_staff_role(
    *, tenant: Tenant, bot_user: BotUser, role: str, actor: OnboardingActor
) -> RoleChange:
    """Сменить роль: деактивировать активные строки человека, выдать новую.

    Одна транзакция: нет состояния «старая снята, новая не выдана» — отказ
    ядра (``OwnerAlreadyExists``) откатывает и деактивацию. Строки не
    удаляются: ``deactivated_at`` оставляет историю, как у отзыва. Та же
    роль повторно — не ошибка (ядро ответит «уже есть»), лишние роли при
    этом снимутся.

    Raises: UnknownRole, ForeignTenantRefused, PersonInOtherTenant,
    NoActiveRole, OwnerRoleLocked, OwnerAlreadyExists.
    """
    from apps.events.vocabulary import STAFF_ROLE_CHANGED

    _check(tenant, bot_user, role, actor)

    with transaction.atomic():
        rows = list(
            TenantStaff.all_tenants.select_for_update().filter(
                tenant_id=tenant.id, bot_user=bot_user, deactivated_at__isnull=True
            )
        )
        if not rows:
            raise NoActiveRole("the person holds no active role in this tenant")
        previous = tuple(sorted(row.role for row in rows))
        if TenantStaff.Role.OWNER in previous:
            raise OwnerRoleLocked("the active owner's role is not changed here")

        to_close = [row.pk for row in rows if row.role != role]
        if to_close:
            TenantStaff.all_tenants.filter(pk__in=to_close).update(deactivated_at=timezone.now())
        grant_staff_role(tenant_id=tenant.id, bot_user=bot_user, role=role, created_by=None)

    change = RoleChange(
        tenant_id=tenant.id, bot_user_id=bot_user.pk, previous_roles=previous, role=role
    )
    _audit(
        STAFF_ROLE_CHANGED,
        tenant=tenant,
        bot_user=bot_user,
        actor=actor,
        payload={"role": role, "previous_roles": list(previous)},
    )
    logger.info(
        "identity.staff_roles.changed tenant=%s person=%s from=%s to=%s surface=%s",
        tenant.slug,
        bot_user.pk,
        ",".join(previous),
        role,
        actor.surface,
    )
    return change


__all__ = [
    "GRANTABLE_ROLES",
    "ForeignTenantRefused",
    "NoActiveRole",
    "OwnerRoleLocked",
    "PersonInOtherTenant",
    "RoleChange",
    "StaffRoleError",
    "UnknownRole",
    "change_staff_role",
    "grant_role_by_operator",
]
