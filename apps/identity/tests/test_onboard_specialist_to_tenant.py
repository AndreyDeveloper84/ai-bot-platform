"""Фасад ``onboard_specialist_to_tenant`` — один путь, отказы по имени, аудит по исходу.

Предмет — ``apps.identity.services.specialist_onboarding``. Стенд для identity
продолжает идиому ``apps/catalog/tests/test_ensure_catalog_specialist_identity.py``
(``_FakeCatalog`` через ``monkeypatch.setattr(solo_catalog_provisioning,
"CatalogHttpClient", ...)``) — второго стенда для того же предмета не заводится.

Что доказывается и чем:

* «связь и identity — две половины, обе видны» — при ``creation_unavailable``
  связь **стоит** (перечитано из базы), статус ``identity_unavailable``, причина
  по имени, ``success=False``; узел с ``success=True`` на той же фикстуре —
  положительная стража: без него предыдущий зеленел бы и на фасаде, который
  никогда не отвечает успехом;
* «повтор дубля не создаёт» — счётчиком: второй вызов отвечает
  ``already_linked`` + ``reuse``, каталог не зван ни разу, строка аудита на
  каждый вызов — по одной;
* «отказ до записи ничего не пишет» — для каждого из четырёх отказов фасада
  и для отказа ядра связи проверяется **после** исключения, что карточка не
  изменилась и строк аудита ноль;
* «дверь кода не сломана обобщением» — ``_link_master`` по-прежнему зовётся
  через ``redeem_staff_invite`` и ставит связь: регрессия на извлечение ядра
  живёт здесь, рядом с тем, ради чего его извлекали.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Any

import pytest

from apps.audit.models import AuditLog
from apps.catalog.identity import REASON_CREATION_UNAVAILABLE
from apps.catalog.master_state import sale_block
from apps.catalog.models import CatalogMaster
from apps.events.vocabulary import CANONICAL_EVENTS, STAFF_SPECIALIST_ONBOARDED
from apps.identity.models import BotUser
from apps.identity.services import solo_catalog_provisioning
from apps.identity.services.specialist_onboarding import (
    ALL_SOURCE_SURFACES,
    MEMBERSHIP_ALREADY_LINKED,
    MEMBERSHIP_LINKED_NOW,
    STATUS_IDENTITY_UNAVAILABLE,
    STATUS_SUCCESS,
    ForeignTenantRefused,
    MasterInOtherTenant,
    OnboardingActor,
    PersonInOtherTenant,
    TenantInactive,
    onboard_specialist_to_tenant,
)
from apps.identity.services.staff_invites import (
    MasterAlreadyLinked,
    issue_staff_invite,
    redeem_staff_invite,
)
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db

SPECIALIST_ID = uuid.UUID("c0a10600-0000-4000-8000-000000000042")


def _ts() -> datetime:
    return datetime(2026, 9, 17, 9, 0, tzinfo=dt_timezone.utc)


class _CountingCatalog:
    """Подмена ``CatalogHttpClient``: только считает, отвечать не должна."""

    calls = 0

    def __enter__(self) -> "_CountingCatalog":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_solo_workspace(self, **kwargs: Any) -> Any:
        type(self).calls += 1
        raise AssertionError("catalog must not be called on this path")


@pytest.fixture(autouse=True)
def _no_real_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _CountingCatalog.calls = 0
    monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", _CountingCatalog)


@pytest.fixture
def tenant(db: Any) -> Tenant:
    return Tenant.objects.create(slug="onboard-a", name="Onboard A")


@pytest.fixture
def other_tenant(db: Any) -> Tenant:
    return Tenant.objects.create(slug="onboard-b", name="Onboard B")


def _person(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"onboard-{uuid.uuid4().hex[:8]}",
    )


@pytest.fixture
def person(tenant: Tenant) -> BotUser:
    return _person(tenant)


def _master(tenant: Tenant, **extra: Any) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=-int(uuid.uuid4().int % 1_000_000) - 1,
        external_updated_at=_ts(),
        name="ТЕСТ Мастер",
        is_active=False,
        **extra,
    )


@pytest.fixture
def salon_master(tenant: Tenant) -> CatalogMaster:
    """Мастер салона: ключа нет, ``SoloIdentityLink`` нет — двери создания нет."""
    return _master(tenant)


@pytest.fixture
def keyed_master(tenant: Tenant) -> CatalogMaster:
    """Мастер, чей ключ уже приехал синком — ветвь reuse."""
    return _master(tenant, catalog_specialist_id=SPECIALIST_ID, ayla_user_id=uuid.uuid4())


def _operator() -> OnboardingActor:
    return OnboardingActor(
        surface="django_admin",
        audit_label="django_admin:user=7",
        cross_tenant=True,
        capability="platform_operations",
    )


def _salon_admin(tenant: Tenant, actor: BotUser | None = None) -> OnboardingActor:
    return OnboardingActor(
        surface="salon_miniapp",
        audit_label=f"bot_user:{actor.pk if actor else uuid.uuid4()}",
        cross_tenant=False,
        current_tenant_id=tenant.id,
        actor_id=actor.pk if actor else None,
        capability="owner",
    )


def _audit_rows(master: CatalogMaster) -> list[AuditLog]:
    return list(
        AuditLog.all_tenants.filter(
            action=STAFF_SPECIALIST_ONBOARDED, target_id=master.pk
        ).order_by("created_at")
    )


# ─── успех ───────────────────────────────────────────────────────────────────


def test_success_links_person_confirms_identity_and_writes_one_audit_row(
    tenant: Tenant, keyed_master: CatalogMaster, person: BotUser
) -> None:
    result = onboard_specialist_to_tenant(
        tenant=tenant, master=keyed_master, bot_user=person, actor=_operator()
    )

    assert result.success is True
    assert result.status == STATUS_SUCCESS
    assert result.membership == MEMBERSHIP_LINKED_NOW
    assert result.identity is not None
    assert result.specialist_id == str(SPECIALIST_ID)
    assert result.identity.created is False  # reuse — каталог не зван
    assert result.identity_reason is None
    assert _CountingCatalog.calls == 0

    row = CatalogMaster.all_tenants.get(pk=keyed_master.pk)
    assert row.linked_bot_user_id == person.pk
    assert row.invite_status == CatalogMaster.InviteStatus.ACCEPTED
    assert row.is_active is True
    assert row.accepted_at is not None  # штамп ставит save() модели — даром
    # Один гейт продажи: результат и модуль отвечают одно и то же.
    assert result.sale_block == sale_block(row)

    rows = _audit_rows(keyed_master)
    assert len(rows) == 1
    payload = rows[0].payload
    assert payload["surface"] == "django_admin"
    assert payload["actor_label"] == "django_admin:user=7"
    assert payload["cross_tenant"] is True
    assert payload["membership_before"] == "none"
    assert payload["membership_after"] == MEMBERSHIP_LINKED_NOW
    assert payload["identity_status"] == STATUS_SUCCESS
    assert payload["specialist_id"] == str(SPECIALIST_ID)
    assert rows[0].tenant_id == tenant.id  # оператор пришёл без скоупа — строка всё равно салона


def test_repeat_is_idempotent_already_linked_reuse_no_second_link_write(
    tenant: Tenant, keyed_master: CatalogMaster, person: BotUser
) -> None:
    first = onboard_specialist_to_tenant(
        tenant=tenant, master=keyed_master, bot_user=person, actor=_operator()
    )
    accepted_at = CatalogMaster.all_tenants.get(pk=keyed_master.pk).accepted_at

    second = onboard_specialist_to_tenant(
        tenant=tenant, master=keyed_master, bot_user=person, actor=_operator()
    )

    assert first.membership == MEMBERSHIP_LINKED_NOW
    assert second.membership == MEMBERSHIP_ALREADY_LINKED
    assert second.success is True
    assert second.specialist_id == first.specialist_id
    assert _CountingCatalog.calls == 0
    row = CatalogMaster.all_tenants.get(pk=keyed_master.pk)
    assert row.linked_bot_user_id == person.pk
    assert row.accepted_at == accepted_at  # дата события, не перештампована
    rows = _audit_rows(keyed_master)
    assert [r.payload["membership_before"] for r in rows] == ["none", "linked"]
    assert [r.payload["membership_after"] for r in rows] == [
        MEMBERSHIP_LINKED_NOW,
        MEMBERSHIP_ALREADY_LINKED,
    ]


# ─── identity нет: связь стоит, статус — не успех, причина по имени ─────────


def test_salon_master_without_door_links_but_is_not_success(
    tenant: Tenant, salon_master: CatalogMaster, person: BotUser
) -> None:
    result = onboard_specialist_to_tenant(
        tenant=tenant, master=salon_master, bot_user=person, actor=_salon_admin(tenant)
    )

    assert result.success is False
    assert result.status == STATUS_IDENTITY_UNAVAILABLE
    assert result.identity is None
    assert result.identity_reason == REASON_CREATION_UNAVAILABLE
    # Связь — вторая половина ответа — стоит, и это перечитано из базы.
    assert result.membership == MEMBERSHIP_LINKED_NOW
    row = CatalogMaster.all_tenants.get(pk=salon_master.pk)
    assert row.linked_bot_user_id == person.pk
    assert row.invite_status == CatalogMaster.InviteStatus.ACCEPTED
    assert row.catalog_specialist_id is None
    assert _CountingCatalog.calls == 0

    rows = _audit_rows(salon_master)
    assert len(rows) == 1
    assert rows[0].payload["identity_status"] == STATUS_IDENTITY_UNAVAILABLE
    assert rows[0].payload["identity_reason"] == REASON_CREATION_UNAVAILABLE
    assert rows[0].payload["membership_after"] == MEMBERSHIP_LINKED_NOW
    assert rows[0].payload["surface"] == "salon_miniapp"


def test_after_operator_fills_the_key_the_repeat_becomes_success(
    tenant: Tenant, salon_master: CatalogMaster, person: BotUser
) -> None:
    """Путь восстановления: «заводится оператором в админке» → повтор → успех."""

    first = onboard_specialist_to_tenant(
        tenant=tenant, master=salon_master, bot_user=person, actor=_operator()
    )
    assert first.success is False

    CatalogMaster.all_tenants.filter(pk=salon_master.pk).update(catalog_specialist_id=SPECIALIST_ID)
    salon_master.refresh_from_db()

    second = onboard_specialist_to_tenant(
        tenant=tenant, master=salon_master, bot_user=person, actor=_operator()
    )
    assert second.success is True
    assert second.membership == MEMBERSHIP_ALREADY_LINKED
    assert second.specialist_id == str(SPECIALIST_ID)
    assert _CountingCatalog.calls == 0


# ─── отказы до записи: ничего не изменилось, аудита ноль ────────────────────


def _assert_untouched(master: CatalogMaster) -> None:
    row = CatalogMaster.all_tenants.get(pk=master.pk)
    assert row.linked_bot_user_id is None
    assert row.invite_status == CatalogMaster.InviteStatus.PENDING
    assert row.is_active is False
    assert row.accepted_at is None
    assert _audit_rows(master) == []


def test_salon_admin_of_another_tenant_is_refused_before_any_write(
    tenant: Tenant, other_tenant: Tenant, keyed_master: CatalogMaster, person: BotUser
) -> None:
    with pytest.raises(ForeignTenantRefused) as exc_info:
        onboard_specialist_to_tenant(
            tenant=tenant,
            master=keyed_master,
            bot_user=person,
            actor=_salon_admin(other_tenant),
        )
    assert exc_info.value.slug == "foreign_tenant"
    _assert_untouched(keyed_master)


def test_platform_operator_crosses_tenants_where_salon_admin_cannot(
    tenant: Tenant, other_tenant: Tenant, keyed_master: CatalogMaster, person: BotUser
) -> None:
    """Положительная стража к предыдущему узлу: тот же салон, другой контекст — проходит."""

    result = onboard_specialist_to_tenant(
        tenant=tenant, master=keyed_master, bot_user=person, actor=_operator()
    )
    assert result.success is True


def test_inactive_tenant_is_refused_before_any_write(
    tenant: Tenant, keyed_master: CatalogMaster, person: BotUser
) -> None:
    Tenant.all_objects.filter(pk=tenant.pk).update(is_active=False)
    tenant.refresh_from_db()

    with pytest.raises(TenantInactive) as exc_info:
        onboard_specialist_to_tenant(
            tenant=tenant, master=keyed_master, bot_user=person, actor=_operator()
        )
    assert exc_info.value.slug == "tenant_inactive"
    _assert_untouched(keyed_master)


def test_person_row_of_another_tenant_is_refused(
    tenant: Tenant, other_tenant: Tenant, keyed_master: CatalogMaster
) -> None:
    stranger = _person(other_tenant)
    with pytest.raises(PersonInOtherTenant):
        onboard_specialist_to_tenant(
            tenant=tenant, master=keyed_master, bot_user=stranger, actor=_operator()
        )
    _assert_untouched(keyed_master)


def test_master_of_another_tenant_is_refused(
    tenant: Tenant, other_tenant: Tenant, person: BotUser
) -> None:
    foreign_master = _master(other_tenant, catalog_specialist_id=SPECIALIST_ID)
    with pytest.raises(MasterInOtherTenant):
        onboard_specialist_to_tenant(
            tenant=tenant, master=foreign_master, bot_user=person, actor=_operator()
        )
    _assert_untouched(foreign_master)


def test_card_owned_by_someone_else_keeps_the_code_door_vocabulary(
    tenant: Tenant, keyed_master: CatalogMaster, person: BotUser
) -> None:
    """Отказ ядра связи проходит как есть — ``wrong_recipient``, не третье слово."""

    owner = _person(tenant)
    CatalogMaster.all_tenants.filter(pk=keyed_master.pk).update(linked_bot_user=owner)
    keyed_master.refresh_from_db()

    with pytest.raises(MasterAlreadyLinked) as exc_info:
        onboard_specialist_to_tenant(
            tenant=tenant, master=keyed_master, bot_user=person, actor=_operator()
        )
    assert exc_info.value.slug == "wrong_recipient"
    row = CatalogMaster.all_tenants.get(pk=keyed_master.pk)
    assert row.linked_bot_user_id == owner.pk  # чужая связь не переписана
    # empty-assert-ok: отказ ядра — до записи; тот же запрос даёт 1 строку на успехе (первый узел)
    assert _audit_rows(keyed_master) == []


# ─── контекст актора: инварианты — исключение, не молчание ──────────────────


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cross_tenant": True, "current_tenant_id": uuid.uuid4()},
        {"cross_tenant": False, "current_tenant_id": None},
        {"cross_tenant": True, "audit_label": "   "},
        {"cross_tenant": True, "surface": "admin"},
    ],
    ids=["cross_tenant_with_tenant", "scoped_without_tenant", "blank_label", "unknown_surface"],
)
def test_actor_invariants_raise_at_construction(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {"surface": "django_admin", "audit_label": "django_admin:user=1"}
    with pytest.raises(ValueError):
        OnboardingActor(**{**base, **kwargs})


def test_source_surfaces_are_the_owner_s_three() -> None:
    assert set(ALL_SOURCE_SURFACES) == {"django_admin", "operations_tool", "salon_miniapp"}


def test_audit_action_is_in_the_vocabulary() -> None:
    assert STAFF_SPECIALIST_ONBOARDED in CANONICAL_EVENTS


# ─── дверь кода не сломана обобщением ядра ──────────────────────────────────


def test_redeem_code_still_links_through_the_shared_core(
    tenant: Tenant, keyed_master: CatalogMaster, person: BotUser
) -> None:
    _invite, code = issue_staff_invite(
        tenant=tenant, role=StaffInvite.Role.MASTER, catalog_master=keyed_master
    )
    result = redeem_staff_invite(code=code, bot_user=person, tenant=tenant)

    assert result.role == StaffInvite.Role.MASTER
    assert result.already_had_role is False
    assert result.catalog_master_id == str(keyed_master.pk)
    row = CatalogMaster.all_tenants.get(pk=keyed_master.pk)
    assert row.linked_bot_user_id == person.pk
    assert row.is_active is True
    # Дверь кода не пишет строку фасада — это разные операции с одним ядром.
    # empty-assert-ok: тот же запрос даёт 1 строку на успехе фасада (первый узел)
    assert _audit_rows(keyed_master) == []
