"""Админка бота: выдать / сменить / отозвать роль в салоне (DRF-2082, PR-1).

Условия листа, по узлу на каждое:

* **гейт тем же правом**, что карточки (`tenancy.platform_operations`): без
  права действий нет в списке и view «выдать роль» — 403; с правом — есть и 200
  (пара);
* **через сервисы, не мимо**: строку пишут `grant_role_by_operator` /
  `change_staff_role` / `revoke_staff_access` — доказательство: аудит с
  `surface=django_admin`, `actor_label=django_admin:user=<pk>`, который пишут
  только они; админка пишет `LogEntry`;
* **повтор выдачи = отказ по имени** «уже есть», строка одна;
* **человек чужого салона** в id — отказ, строки нет;
* **владелец** — смена роли `owner_role_locked`, отзыв `OwnerRevokeRefused` —
  ничего не изменилось;
* **отозвать = `deactivated_at`**, не удаление; строка остаётся историей.

Узел «initData того же человека → 403 после отзыва» живёт в
`apps/admin_api/tests/test_staff_revoke_from_admin_403_2082.py` — там стенд
подписи initData.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import pytest
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.events.vocabulary import STAFF_ACCESS_REVOKED, STAFF_ROLE_CHANGED, STAFF_ROLE_GRANTED
from apps.identity.models import BotUser
from apps.identity.services.role_resolver import resolve_role
from apps.tenancy.admin import TenantStaffAdmin
from apps.tenancy.models import Tenant, TenantStaff

# DRF-2085: роль admin, выданная ОПЕРАТОРОМ, спрашивает каталог (свежая учётка +
# TUR + связь) до записи TenantStaff; здесь каталог — заглушка, его половина
# доказывается в apps/identity/tests/test_salon_admin_link_2085.py.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("catalog_admin_link_stub")]

CHANGELIST = "admin:tenancy_tenantstaff_changelist"
GRANT_URL = "admin:tenancy_tenantstaff_grant"
ACTION_CHANGE = "change_staff_role"
ACTION_REVOKE = "revoke_staff_access"
PERM_OPS = "platform_operations"


def _user(username: str, *, ops: bool):  # noqa: ANN202
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(username=username, password=password, is_staff=True)
    if ops:
        user.user_permissions.add(
            Permission.objects.get(codename=PERM_OPS, content_type__app_label="tenancy")
        )
    return user, password


def _client(username: str, *, ops: bool) -> tuple[Client, Any]:
    user, password = _user(username, ops=ops)
    client = Client()
    assert client.login(username=username, password=password)
    return client, user


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="roles-admin-a", name="Салон A")


@pytest.fixture
def other_salon() -> Tenant:
    return Tenant.objects.create(slug="roles-admin-b", name="Салон B")


def _person(salon: Tenant, name: str = "Человек") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=salon,
        channel="max",
        channel_user_id=f"ra-{uuid.uuid4().hex[:8]}",
        display_name=name,
    )


def _grant(salon: Tenant, person: BotUser, role: str) -> TenantStaff:
    return TenantStaff.all_tenants.create(tenant=salon, bot_user=person, role=role)


def _actions_for(user) -> set[str]:  # noqa: ANN001
    request = RequestFactory().get(reverse(CHANGELIST))
    request.user = user
    model_admin = admin.site._registry[TenantStaff]  # noqa: SLF001
    assert isinstance(model_admin, TenantStaffAdmin)
    return set(model_admin.get_actions(request))


def _post_action(client: Client, action: str, rows: list[TenantStaff], **extra):  # noqa: ANN003, ANN202
    return client.post(
        reverse(CHANGELIST),
        {"action": action, "_selected_action": [str(r.pk) for r in rows], **extra},
        follow=True,
    )


def _messages(response) -> list[tuple[int, str]]:  # noqa: ANN001
    return [(m.level, str(m.message)) for m in get_messages(response.wsgi_request)]


def _active_roles(salon: Tenant, person: BotUser) -> list[str]:
    return sorted(
        TenantStaff.all_tenants.filter(
            tenant=salon, bot_user=person, deactivated_at__isnull=True
        ).values_list("role", flat=True)
    )


def _audit(action: str, person: BotUser) -> list[AuditLog]:
    return list(AuditLog.all_tenants.filter(action=action, target_id=person.pk))


# ─── право ─────────────────────────────────────────────────────────────────


def test_a1_without_platform_operations_no_actions_and_grant_view_is_403(salon: Tenant) -> None:
    client, user = _client("staff-no-ops", ops=False)
    with_ops, _ = _user("staff-with-ops-pair", ops=True)
    # Присутствие впереди отсутствия: тот же реестр для человека с правом их содержит.
    assert {ACTION_CHANGE, ACTION_REVOKE} <= _actions_for(with_ops)
    actions = _actions_for(user)
    # empty-assert-ok: положительная пара — тот же реестр с правом строкой выше и узел a2
    assert ACTION_CHANGE not in actions
    # empty-assert-ok: то же
    assert ACTION_REVOKE not in actions
    assert client.get(reverse(GRANT_URL)).status_code == 403


def test_a2_with_platform_operations_actions_and_grant_view_are_there(salon: Tenant) -> None:
    client, user = _client("staff-ops", ops=True)
    assert {ACTION_CHANGE, ACTION_REVOKE} <= _actions_for(user)
    assert client.get(reverse(GRANT_URL)).status_code == 200


# ─── выдать роль (view) ────────────────────────────────────────────────────


def test_g1_grant_page_lists_only_people_of_the_chosen_salon(
    salon: Tenant, other_salon: Tenant
) -> None:
    client, _ = _client("ops-g1", ops=True)
    own = _person(salon, "Своя")
    foreign = _person(other_salon, "Чужая")

    response = client.get(reverse(GRANT_URL), {"tenant": str(salon.pk)})

    assert response.status_code == 200
    candidates = {str(p.pk) for p in response.context["candidates"]}
    assert str(own.pk) in candidates
    assert str(foreign.pk) not in candidates


def test_g2_apply_grants_through_the_service_and_journals(salon: Tenant) -> None:
    client, user = _client("ops-g2", ops=True)
    person = _person(salon)

    response = client.post(
        reverse(GRANT_URL),
        {"tenant": str(salon.pk), "bot_user_id": str(person.pk), "role": "admin", "apply": "1"},
        follow=True,
    )

    assert _active_roles(salon, person) == ["admin"]
    assert resolve_role(person).is_admin is True
    rows = _audit(STAFF_ROLE_GRANTED, person)
    assert len(rows) == 1
    assert rows[0].payload["surface"] == "django_admin"
    assert rows[0].payload["actor_label"] == f"django_admin:user={user.pk}"
    entry = LogEntry.objects.filter(user=user).order_by("-id").first()
    assert entry is not None
    assert "admin" in entry.get_change_message()
    assert 25 in {level for level, _ in _messages(response)}  # SUCCESS


def test_g2b_catalog_refusal_is_told_to_the_operator_and_no_row_is_written(
    salon: Tenant, catalog_admin_link_stub
) -> None:
    """DRF-2085: на отказ каталога — слово оператору с причиной и «что сделать», TenantStaff нет."""
    client, _ = _client("ops-g2b", ops=True)
    person = _person(salon)
    catalog_admin_link_stub.refuse_with = "credential_refused"

    response = client.post(
        reverse(GRANT_URL),
        {"tenant": str(salon.pk), "bot_user_id": str(person.pk), "role": "admin", "apply": "1"},
        follow=True,
    )

    # empty-assert-ok: слово оператору с причиной — присутствие ниже
    assert _active_roles(salon, person) == []
    errors = [text for level, text in _messages(response) if level == 40]
    assert len(errors) == 1
    assert "credential_refused" in errors[0] and "Роль в боте не выдана" in errors[0]
    assert "AYLA_SALON_ADMIN_LINK_TOKEN" in errors[0]  # «что сделать» — по имени переменной
    assert catalog_admin_link_stub.calls == [
        (salon.slug, str(person.pk), f"django_admin:user={_user_pk('ops-g2b')}")
    ]
    # empty-assert-ok: вызов каталога зафиксирован строкой выше, ошибка оператору — выше
    assert _audit(STAFF_ROLE_GRANTED, person) == []


def _user_pk(username: str):  # noqa: ANN202
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(username=username).pk


def test_g3_repeat_is_a_named_refusal_not_a_second_row(salon: Tenant) -> None:
    client, _ = _client("ops-g3", ops=True)
    person = _person(salon)
    _grant(salon, person, "admin")

    response = client.post(
        reverse(GRANT_URL),
        {"tenant": str(salon.pk), "bot_user_id": str(person.pk), "role": "admin", "apply": "1"},
        follow=True,
    )

    assert TenantStaff.all_tenants.filter(tenant=salon, bot_user=person).count() == 1
    msgs = _messages(response)
    assert [level for level, _ in msgs] == [30]  # WARNING, не ошибка и не «выдано»
    assert TenantStaffAdmin.ROLE_WORDS["already_had_role"] in msgs[0][1]


def test_g4_person_of_another_salon_is_refused_and_nothing_is_written(
    salon: Tenant, other_salon: Tenant
) -> None:
    client, _ = _client("ops-g4", ops=True)
    foreign = _person(other_salon)

    response = client.post(
        reverse(GRANT_URL),
        {"tenant": str(salon.pk), "bot_user_id": str(foreign.pk), "role": "admin", "apply": "1"},
        follow=True,
    )

    assert TenantStaff.all_tenants.filter(tenant=salon).count() == 0
    assert [level for level, _ in _messages(response)] == [40]
    # empty-assert-ok: тот же запрос даёт 1 строку в узле g2
    assert _audit(STAFF_ROLE_GRANTED, foreign) == []


# ─── сменить роль (action) ──────────────────────────────────────────────────


def test_c1_change_role_closes_the_old_row_and_grants_the_new(salon: Tenant) -> None:
    client, user = _client("ops-c1", ops=True)
    person = _person(salon)
    row = _grant(salon, person, "receptionist")

    page = _post_action(client, ACTION_CHANGE, [row])
    assert page.status_code == 200
    assert page.context["row"].pk == row.pk

    response = _post_action(client, ACTION_CHANGE, [row], apply="1", role="admin")

    assert _active_roles(salon, person) == ["admin"]
    row.refresh_from_db()
    assert row.deactivated_at is not None  # история, не удаление
    rows = _audit(STAFF_ROLE_CHANGED, person)
    assert len(rows) == 1
    assert rows[0].payload["previous_roles"] == ["receptionist"]
    assert rows[0].payload["actor_label"] == f"django_admin:user={user.pk}"
    assert 25 in {level for level, _ in _messages(response)}


def test_c2_owner_role_is_locked_by_name(salon: Tenant) -> None:
    client, _ = _client("ops-c2", ops=True)
    person = _person(salon)
    row = _grant(salon, person, "owner")

    response = _post_action(client, ACTION_CHANGE, [row], apply="1", role="admin")

    assert _active_roles(salon, person) == ["owner"]
    errors = [text for level, text in _messages(response) if level == 40]
    assert len(errors) == 1
    assert TenantStaffAdmin.ROLE_WORDS["owner_role_locked"] in errors[0]


def test_c3_more_than_one_row_is_refused(salon: Tenant) -> None:
    client, _ = _client("ops-c3", ops=True)
    a, b = _person(salon), _person(salon)
    rows = [_grant(salon, a, "admin"), _grant(salon, b, "admin")]

    response = _post_action(client, ACTION_CHANGE, rows, apply="1", role="receptionist")

    assert 40 in [level for level, _ in _messages(response)]
    assert _active_roles(salon, a) == ["admin"] and _active_roles(salon, b) == ["admin"]


# ─── отозвать доступ (action) ───────────────────────────────────────────────


def test_r1_revoke_sets_deactivated_at_with_a_reason_and_keeps_the_row(salon: Tenant) -> None:
    client, user = _client("ops-r1", ops=True)
    person = _person(salon)
    row = _grant(salon, person, "admin")

    page = _post_action(client, ACTION_REVOKE, [row])
    assert page.status_code == 200

    response = _post_action(client, ACTION_REVOKE, [row], apply="1", reason="уволилась")

    row.refresh_from_db()
    assert row.deactivated_at is not None
    assert TenantStaff.all_tenants.filter(pk=row.pk).exists()
    assert resolve_role(person).primary_role == "customer"
    rows = _audit(STAFF_ACCESS_REVOKED, person)
    assert len(rows) == 1
    assert rows[0].payload["surface"] == "django_admin"
    assert rows[0].payload["actor_label"] == f"django_admin:user={user.pk}"
    assert rows[0].payload["reason"] == "уволилась"
    assert 25 in {level for level, _ in _messages(response)}


def test_r2_revoke_without_a_reason_does_not_revoke(salon: Tenant) -> None:
    client, _ = _client("ops-r2", ops=True)
    person = _person(salon)
    row = _grant(salon, person, "admin")

    response = _post_action(client, ACTION_REVOKE, [row], apply="1", reason="   ")

    row.refresh_from_db()
    assert row.deactivated_at is None
    assert 40 in [level for level, _ in _messages(response)]


def test_r3_owner_is_refused_by_name_and_keeps_the_access(salon: Tenant) -> None:
    client, _ = _client("ops-r3", ops=True)
    person = _person(salon)
    row = _grant(salon, person, "owner")

    response = _post_action(client, ACTION_REVOKE, [row], apply="1", reason="проба")

    row.refresh_from_db()
    assert row.deactivated_at is None
    assert resolve_role(person).is_owner is True
    errors = [text for level, text in _messages(response) if level == 40]
    assert len(errors) == 1
    assert TenantStaffAdmin.ROLE_WORDS["owner_revoke_refused"] in errors[0]
