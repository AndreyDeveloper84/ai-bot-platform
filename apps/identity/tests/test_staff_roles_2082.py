"""Роли из рук оператора — тем же ядром, что код приглашения (DRF-2082, PR-1).

Что доказывается и чем:

* **одно ядро** — ``grant_role_by_operator`` и дверь кода (``redeem_staff_invite``)
  пишут одну и ту же строку с теми же отказами; регрессия двери кода после
  извлечения ядра стоит здесь же;
* **повтор = «уже есть», не дубль и не 500** — и под гонкой: предпроверка
  «уже есть» подменяется пустой, партиальный unique срабатывает, ответ тот же
  ``already_had_role=True`` (условие главного окна: показать, что база держит,
  а отказ — по имени);
* **второй владелец** — ``OwnerAlreadyExists`` по имени, строки нет;
* **смена роли** — одна транзакция: старые строки закрыты ``deactivated_at``
  (история), новая выдана; владелец — ``owner_role_locked``, ничего не
  изменилось; ``OwnerAlreadyExists`` из ядра откатывает и деактивацию;
* **аудит** — строка с ``surface``/``actor_label`` на каждое действие.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from apps.audit.models import AuditLog
from apps.events.vocabulary import STAFF_ROLE_CHANGED, STAFF_ROLE_GRANTED
from apps.identity.models import BotUser
from apps.identity.services import staff_invites
from apps.identity.services.role_resolver import resolve_role
from apps.identity.services.specialist_onboarding import OnboardingActor
from apps.identity.services.staff_invites import (
    OwnerAlreadyExists,
    issue_staff_invite,
    redeem_staff_invite,
)
from apps.identity.services.staff_roles import (
    GRANTABLE_ROLES,
    ForeignTenantRefused,
    NoActiveRole,
    OwnerRoleLocked,
    PersonInOtherTenant,
    UnknownRole,
    change_staff_role,
    grant_role_by_operator,
)
from apps.tenancy.models import StaffInvite, Tenant, TenantStaff

# DRF-2085: роль admin, выданная ОПЕРАТОРОМ, спрашивает каталог (свежая учётка +
# TUR + связь) до записи TenantStaff; здесь каталог — заглушка, его половина
# доказывается в apps/identity/tests/test_salon_admin_link_2085.py.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("catalog_admin_link_stub")]

ADMIN = TenantStaff.Role.ADMIN
RECEPTIONIST = TenantStaff.Role.RECEPTIONIST
OWNER = TenantStaff.Role.OWNER


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="roles-2082", name="Роли 2082")


@pytest.fixture
def other_tenant() -> Tenant:
    return Tenant.objects.create(slug="roles-2082-b", name="Роли 2082 B")


def _person(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=f"r-{uuid.uuid4().hex[:8]}"
    )


def _operator() -> OnboardingActor:
    return OnboardingActor(
        surface="django_admin",
        audit_label="django_admin:user=7",
        cross_tenant=True,
        capability="platform_operations",
    )


def _active_roles(tenant: Tenant, person: BotUser) -> list[str]:
    return sorted(
        TenantStaff.all_tenants.filter(
            tenant=tenant, bot_user=person, deactivated_at__isnull=True
        ).values_list("role", flat=True)
    )


def _audit_rows(action: str, person: BotUser) -> list[AuditLog]:
    return list(AuditLog.all_tenants.filter(action=action, target_id=person.pk))


# ─── выдать ────────────────────────────────────────────────────────────────


class TestGrant:
    def test_grants_the_role_and_writes_the_audit_row(self, tenant):
        person = _person(tenant)

        result = grant_role_by_operator(
            tenant=tenant, bot_user=person, role=ADMIN, actor=_operator()
        )

        assert result.already_had_role is False
        assert _active_roles(tenant, person) == [ADMIN]
        assert resolve_role(person).is_admin is True
        rows = _audit_rows(STAFF_ROLE_GRANTED, person)
        assert len(rows) == 1
        assert rows[0].payload["surface"] == "django_admin"
        assert rows[0].payload["actor_label"] == "django_admin:user=7"
        assert rows[0].payload["role"] == ADMIN
        assert rows[0].tenant_id == tenant.id

    def test_repeat_is_already_had_role_not_a_duplicate(self, tenant):
        person = _person(tenant)
        grant_role_by_operator(tenant=tenant, bot_user=person, role=ADMIN, actor=_operator())

        again = grant_role_by_operator(
            tenant=tenant, bot_user=person, role=ADMIN, actor=_operator()
        )

        assert again.already_had_role is True
        assert TenantStaff.all_tenants.filter(tenant=tenant, bot_user=person).count() == 1

    def test_race_between_two_operators_is_the_same_named_answer_not_a_500(self, tenant):
        """Предпроверку «уже есть» второго оператора подменяем пустой — как если бы
        первый вставил строку между его проверкой и записью. Партиальный unique
        срабатывает, ответ — «уже есть», не IntegrityError."""

        person = _person(tenant)
        grant_role_by_operator(tenant=tenant, bot_user=person, role=ADMIN, actor=_operator())

        real_filter = TenantStaff.all_tenants.filter

        def blind_filter(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            qs = real_filter(*args, **kwargs)
            if kwargs.get("deactivated_at__isnull") is True and kwargs.get("role") == ADMIN:
                return qs.none()  # «ещё нет» — гонка
            return qs

        with patch.object(
            staff_invites.TenantStaff.all_tenants, "filter", side_effect=blind_filter
        ):
            result = grant_role_by_operator(
                tenant=tenant, bot_user=person, role=ADMIN, actor=_operator()
            )

        assert result.already_had_role is True
        assert TenantStaff.all_tenants.filter(tenant=tenant, bot_user=person).count() == 1

    def test_second_owner_is_refused_by_name_and_no_row_is_written(self, tenant):
        first, second = _person(tenant), _person(tenant)
        grant_role_by_operator(tenant=tenant, bot_user=first, role=OWNER, actor=_operator())

        with pytest.raises(OwnerAlreadyExists):
            grant_role_by_operator(tenant=tenant, bot_user=second, role=OWNER, actor=_operator())

        assert _active_roles(tenant, first) == [OWNER]  # присутствие на тех же данных
        assert _active_roles(tenant, second) == []
        assert _audit_rows(STAFF_ROLE_GRANTED, second) == []  # empty-assert-ok: успех выше пишет 1

    def test_unknown_role_and_foreign_person_are_refused_before_any_write(
        self, tenant, other_tenant
    ):
        person = _person(tenant)
        stranger = _person(other_tenant)

        with pytest.raises(UnknownRole):
            grant_role_by_operator(tenant=tenant, bot_user=person, role="master", actor=_operator())
        with pytest.raises(PersonInOtherTenant):
            grant_role_by_operator(tenant=tenant, bot_user=stranger, role=ADMIN, actor=_operator())
        scoped = OnboardingActor(
            surface="salon_miniapp",
            audit_label="bot_user:x",
            cross_tenant=False,
            current_tenant_id=other_tenant.id,
        )
        with pytest.raises(ForeignTenantRefused):
            grant_role_by_operator(tenant=tenant, bot_user=person, role=ADMIN, actor=scoped)

        assert TenantStaff.all_tenants.filter(tenant=tenant).count() == 0
        assert "master" not in GRANTABLE_ROLES and set(GRANTABLE_ROLES) == {
            OWNER,
            ADMIN,
            RECEPTIONIST,
        }

    def test_the_code_door_still_grants_through_the_shared_core(self, tenant):
        person = _person(tenant)
        _invite, code = issue_staff_invite(tenant=tenant, role=StaffInvite.Role.ADMIN)

        result = redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

        assert result.already_had_role is False
        assert _active_roles(tenant, person) == [ADMIN]
        # …и повторная выдача руками той же роли — «уже есть», одна строка.
        again = grant_role_by_operator(
            tenant=tenant, bot_user=person, role=ADMIN, actor=_operator()
        )
        assert again.already_had_role is True
        assert TenantStaff.all_tenants.filter(tenant=tenant, bot_user=person).count() == 1


# ─── сменить ───────────────────────────────────────────────────────────────


class TestChange:
    def test_old_row_is_closed_new_is_granted_in_one_transaction(self, tenant):
        person = _person(tenant)
        grant_role_by_operator(tenant=tenant, bot_user=person, role=RECEPTIONIST, actor=_operator())

        change = change_staff_role(tenant=tenant, bot_user=person, role=ADMIN, actor=_operator())

        assert change.previous_roles == (RECEPTIONIST,)
        assert _active_roles(tenant, person) == [ADMIN]
        # История: старая строка есть, закрыта датой, не удалена.
        closed = TenantStaff.all_tenants.get(tenant=tenant, bot_user=person, role=RECEPTIONIST)
        assert closed.deactivated_at is not None
        assert resolve_role(person).is_admin is True
        assert resolve_role(person).is_receptionist is False
        rows = _audit_rows(STAFF_ROLE_CHANGED, person)
        assert len(rows) == 1
        assert rows[0].payload["previous_roles"] == [RECEPTIONIST]
        assert rows[0].payload["role"] == ADMIN

    def test_owner_role_is_locked_and_nothing_changes(self, tenant):
        person = _person(tenant)
        grant_role_by_operator(tenant=tenant, bot_user=person, role=OWNER, actor=_operator())

        with pytest.raises(OwnerRoleLocked) as exc_info:
            change_staff_role(tenant=tenant, bot_user=person, role=ADMIN, actor=_operator())

        assert exc_info.value.slug == "owner_role_locked"
        assert _active_roles(tenant, person) == [OWNER]
        assert resolve_role(person).is_owner is True

    def test_no_active_role_is_refused_by_name(self, tenant):
        person = _person(tenant)
        with pytest.raises(NoActiveRole):
            change_staff_role(tenant=tenant, bot_user=person, role=ADMIN, actor=_operator())

    def test_owner_conflict_from_the_core_rolls_the_deactivation_back(self, tenant):
        """Перевод в owner при живом владельце: отказ ядра — и старая роль НЕ снята."""

        owner, person = _person(tenant), _person(tenant)
        grant_role_by_operator(tenant=tenant, bot_user=owner, role=OWNER, actor=_operator())
        grant_role_by_operator(tenant=tenant, bot_user=person, role=ADMIN, actor=_operator())

        with pytest.raises(OwnerAlreadyExists):
            change_staff_role(tenant=tenant, bot_user=person, role=OWNER, actor=_operator())

        assert _active_roles(tenant, person) == [ADMIN]
        assert TenantStaff.all_tenants.get(tenant=tenant, bot_user=person).deactivated_at is None
