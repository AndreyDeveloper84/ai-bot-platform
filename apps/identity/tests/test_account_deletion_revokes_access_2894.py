"""DRF-2894 — доступ не переживает аккаунт: удаление снимает роли и связь с карточкой.

Замер до правки (``1ec444b7``): бот-половина удаления аккаунта отчитывалась
успехом, а у мастера оставались роль ``admin``, связь с карточкой, ключ личности
и ник на ней; поиск мастера, которым пользуется кабинет, его находил.

Узлы:

* a1 — после удаления аккаунта у человека нет действующих ролей, карточка
  отвязана, на ней нет ``ayla_user_id``, ника и телефона приглашения; кабинет
  его как мастера не находит;
* a2 — имя на карточке, сама карточка и её место в расписании остаются: это
  запись салона (решение владельца ждётся);
* a3 — строки ролей не удаляются, а деактивируются: след «кто что держал»
  остаётся;
* a4 — сосед в том же салоне не затронут;
* a5 — человек с оболочками в двух салонах теряет доступ в обоих;
* a6 — карточка, у которой связь снята раньше, но остался ключ личности,
  тоже очищается;
* a7 — владелец салона: роль не снимается и это названо в шаге, а не молчит;
* a8 — повтор идемпотентен;
* a9 — сбой шага называется в ответе: каталог не получит ``all_ok``;
* a10 — «удалить мои данные» (отзыв хранения) роль и связь НЕ трогает.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services import account_deletion
from apps.identity.services.account_deletion import STAFF_ACCESS_STEP, execute_bot_half
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

HANDLE = "probe_handle_2894"
PHONE_KEY = "invite_phone"


@dataclass
class Staffer:
    tenant: Tenant
    shell: BotUser
    card: CatalogMaster
    ayla_user_id: uuid.UUID


def _tenant(slug: str) -> Tenant:
    return Tenant.objects.create(slug=slug, name=slug)


def _staffer(
    tenant: Tenant,
    label: str,
    *,
    role: str = TenantStaff.Role.ADMIN,
    ayla_user_id: uuid.UUID | None = None,
) -> Staffer:
    """Сотрудник салона: оболочка, роль и карточка мастера, привязанная к оболочке."""
    ayla_user_id = ayla_user_id or uuid.uuid4()
    shell = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"2894-{label}-{uuid.uuid4().hex[:8]}",
        ayla_user_id=ayla_user_id,
        display_name=f"Анна {label}",
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=shell, role=role)
    card = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=f"Анна {label}",
        external_id=uuid.uuid4().int % 10**7,
        external_updated_at=timezone.now(),
        linked_bot_user=shell,
        ayla_user_id=ayla_user_id,
        max_handle=HANDLE,
        raw={PHONE_KEY: "probe-phone", "other": "kept"},
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    return Staffer(tenant=tenant, shell=shell, card=card, ayla_user_id=ayla_user_id)


@pytest.fixture
def quiet_cascade(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Остальной каскад — не предмет: он подменён успешным, шаг доступа настоящий."""
    settings.STRICT_TENANT_SCOPE = "off"

    class _Done:
        steps: list = []
        failed_steps: list = []

    monkeypatch.setattr(account_deletion, "delete_personal_data", lambda *a, **kw: _Done())
    monkeypatch.setattr(account_deletion, "clear_deletion_flag", lambda ayla_user_id: True)


def _delete(person: Staffer) -> account_deletion.AccountDeletionOutcome:
    return execute_bot_half(
        ayla_user_id=person.ayla_user_id, external_user_ids=[], request_id="drf-2894"
    )


def _active_roles(shell: BotUser) -> list[str]:
    return list(
        TenantStaff.all_tenants.filter(bot_user=shell, deactivated_at__isnull=True).values_list(
            "role", flat=True
        )
    )


def _found_as_master(shell: BotUser) -> bool:
    """Тот же запрос, которым кабинет мастера находит человека (``master_api/auth.py``)."""
    return CatalogMaster.all_tenants.filter(linked_bot_user=shell).exists()


def _card(person: Staffer) -> CatalogMaster:
    return CatalogMaster.all_tenants.get(pk=person.card.pk)


def _step(outcome: account_deletion.AccountDeletionOutcome) -> dict:
    (step,) = [s for s in outcome.steps if s["step"] == STAFF_ACCESS_STEP]
    return step


def test_a1_after_account_deletion_the_person_holds_no_access(quiet_cascade: None) -> None:
    person = _staffer(_tenant("a1-salon"), "a1")
    assert _active_roles(person.shell) == ["admin"]
    assert _found_as_master(person.shell) is True

    outcome = _delete(person)

    assert outcome.all_ok is True
    assert _step(outcome) == {"step": STAFF_ACCESS_STEP, "ok": True, "detail": "roles=1 cards=1"}
    assert _active_roles(person.shell) == []  # empty-assert-ok: выше роль admin была
    assert _found_as_master(person.shell) is False
    card = _card(person)
    assert card.linked_bot_user_id is None
    assert card.ayla_user_id is None
    assert card.max_handle == ""
    assert PHONE_KEY not in card.raw  # empty-assert-ok: посев кладёт ключ; «other» ниже цел
    assert card.raw == {"other": "kept"}


def test_a2_the_salon_card_and_its_name_stay(quiet_cascade: None) -> None:
    person = _staffer(_tenant("a2-salon"), "a2")

    _delete(person)

    card = _card(person)
    assert card.name == "Анна a2"
    assert card.is_active is True
    assert card.invite_status == CatalogMaster.InviteStatus.ACCEPTED


def test_a3_role_rows_are_deactivated_not_deleted(quiet_cascade: None) -> None:
    person = _staffer(_tenant("a3-salon"), "a3")

    _delete(person)

    (row,) = TenantStaff.all_tenants.filter(bot_user=person.shell)
    assert row.role == "admin"
    assert row.deactivated_at is not None


def test_a4_a_colleague_in_the_same_salon_is_untouched(quiet_cascade: None) -> None:
    tenant = _tenant("a4-salon")
    person, colleague = _staffer(tenant, "a4"), _staffer(tenant, "a4-other")

    _delete(person)

    assert _active_roles(colleague.shell) == ["admin"]
    assert _found_as_master(colleague.shell) is True
    card = _card(colleague)
    assert card.ayla_user_id == colleague.ayla_user_id
    assert card.max_handle == HANDLE


def test_a5_access_goes_in_every_salon_the_person_works_in(quiet_cascade: None) -> None:
    first = _staffer(_tenant("a5-one"), "a5-one")
    second = _staffer(
        _tenant("a5-two"),
        "a5-two",
        role=TenantStaff.Role.RECEPTIONIST,
        ayla_user_id=first.ayla_user_id,
    )

    outcome = _delete(first)

    assert _step(outcome)["detail"] == "roles=2 cards=2"
    for person in (first, second):
        assert _active_roles(person.shell) == []  # empty-assert-ok: роли посеяны _staffer
        assert _found_as_master(person.shell) is False
        assert _card(person).ayla_user_id is None


def test_a6_a_card_unlinked_earlier_but_still_keyed_is_cleaned(quiet_cascade: None) -> None:
    person = _staffer(_tenant("a6-salon"), "a6")
    CatalogMaster.all_tenants.filter(pk=person.card.pk).update(linked_bot_user=None)

    _delete(person)

    card = _card(person)
    assert card.ayla_user_id is None
    assert card.max_handle == ""


def test_a7_the_salon_owner_keeps_the_role_and_the_step_says_so(quiet_cascade: None) -> None:
    owner = _staffer(_tenant("a7-salon"), "a7", role=TenantStaff.Role.OWNER)

    outcome = _delete(owner)

    step = _step(outcome)
    assert step["ok"] is True
    assert step["detail"] == "roles=0 cards=0 owner_role_kept=1"
    assert _active_roles(owner.shell) == ["owner"]
    card = _card(owner)
    assert card.linked_bot_user_id == owner.shell.id
    assert card.ayla_user_id == owner.ayla_user_id


def test_a8_a_second_run_changes_nothing(quiet_cascade: None) -> None:
    person = _staffer(_tenant("a8-salon"), "a8")
    assert _step(_delete(person))["detail"] == "roles=1 cards=1"

    again = _delete(person)

    assert again.all_ok is True
    assert _step(again)["detail"] == "roles=0 cards=0"


def test_a9_a_failed_step_is_named_and_the_catalog_gets_no_all_ok(
    quiet_cascade: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = _staffer(_tenant("a9-salon"), "a9")

    def down(**kwargs: Any) -> None:
        raise RuntimeError("database down")

    monkeypatch.setattr("apps.identity.services.staff_revoke.revoke_staff_access", down)

    outcome = _delete(person)

    assert outcome.all_ok is False
    assert outcome.failed_steps == [STAFF_ACCESS_STEP]
    assert outcome.flag_cleared is False
    assert _active_roles(person.shell) == ["admin"]


def test_a10_deleting_my_data_does_not_take_the_job_away(settings: Any) -> None:
    """Отзыв хранения — не удаление аккаунта: роль — доступ, не память (владелец 20.09, §58)."""
    from apps.identity.services.privacy import delete_personal_data

    settings.STRICT_TENANT_SCOPE = "off"
    person = _staffer(_tenant("a10-salon"), "a10")

    delete_personal_data(person.shell, erased_by_catalog=True, catalog_request_id="drf-2894")

    assert _active_roles(person.shell) == ["admin"]
    assert _found_as_master(person.shell) is True
