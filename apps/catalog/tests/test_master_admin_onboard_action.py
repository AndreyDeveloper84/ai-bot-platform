"""Действие «Подключить человека» на карточке мастера — тем же правом, тем же путём.

Два условия главного окна (17.09) и по узлу на каждое:

* действие **гейтится тем же правом**, что карточки доступов
  (``tenancy.platform_operations``, #1802) — главный узел отрицательный:
  сотрудник с ``is_staff`` и правом на карточку мастера, но без права
  оператора, действия **не видит**; парная положительная стража — с правом
  видит;
* действие **зовёт фасад**, а не повторяет его логику: доказательство —
  строка аудита ``staff.specialist_onboarded`` с ``surface=django_admin`` и
  ``actor_label=django_admin:user=<pk>``, которую пишет только фасад;
  карточка пишет лишь ``LogEntry`` со своим текстом.

Остальное — то, что принадлежит админке: ровно одна строка, кандидаты только
из салона мастера, чужой человек — отказ, и у каждого слуга отказа фасада и
ядра связи есть слово для оператора (узел полноты по образцу
``test_every_sale_block_has_a_word_for_the_operator``).
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
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.admin import CatalogMasterAdmin
from apps.catalog.identity import REASON_CREATION_UNAVAILABLE
from apps.catalog.models import CatalogMaster
from apps.events.vocabulary import STAFF_SPECIALIST_ONBOARDED
from apps.identity.models import BotUser
from apps.identity.services import specialist_onboarding as facade
from apps.identity.services import staff_invites
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CHANGELIST_URL = "admin:catalog_catalogmaster_changelist"
ACTION = "onboard_person"
PERM_OPS = "platform_operations"


def _user(username: str, *, ops: bool):  # noqa: ANN202
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(username=username, password=password, is_staff=True)
    for codename, app in (("change_catalogmaster", "catalog"), ("view_catalogmaster", "catalog")):
        user.user_permissions.add(
            Permission.objects.get(codename=codename, content_type__app_label=app)
        )
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
    return Tenant.objects.create(slug="onboard-action-a", name="Салон A")


@pytest.fixture
def other_salon() -> Tenant:
    return Tenant.objects.create(slug="onboard-action-b", name="Салон B")


def _master(salon: Tenant, name: str, **extra) -> CatalogMaster:  # noqa: ANN003
    return CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=None,
        external_updated_at=timezone.now(),
        name=name,
        is_active=False,
        **extra,
    )


def _person(salon: Tenant, name: str) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=salon,
        channel="max",
        channel_user_id=f"act-{uuid.uuid4().hex[:8]}",
        display_name=name,
    )


def _actions_for(user) -> set[str]:  # noqa: ANN001
    request = RequestFactory().get(reverse(CHANGELIST_URL))
    request.user = user
    model_admin = admin.site._registry[CatalogMaster]  # noqa: SLF001
    return set(model_admin.get_actions(request))


def _post(client: Client, masters: list[CatalogMaster], **extra):  # noqa: ANN003, ANN202
    return client.post(
        reverse(CHANGELIST_URL),
        {"action": ACTION, "_selected_action": [str(m.pk) for m in masters], **extra},
        follow=True,
    )


def _messages(response) -> list[tuple[int, str]]:  # noqa: ANN001
    return [(m.level, str(m.message)) for m in get_messages(response.wsgi_request)]


def _audit_rows(master: CatalogMaster) -> list[AuditLog]:
    return list(AuditLog.all_tenants.filter(action=STAFF_SPECIALIST_ONBOARDED, target_id=master.pk))


# --- право: тем же, что карточки --------------------------------------------


def test_action_is_absent_without_platform_operations(salon: Tenant) -> None:
    """Главный узел: вход в админку и право на мастера — не право оператора."""
    user, _ = _user("no-ops", ops=False)
    assert "verify_masters" in _actions_for(user)  # право на мастера есть — стража
    assert ACTION not in _actions_for(user)


def test_action_is_present_with_platform_operations(salon: Tenant) -> None:
    user, _ = _user("with-ops", ops=True)
    assert ACTION in _actions_for(user)


# --- промежуточная страница --------------------------------------------------


def test_page_lists_only_people_of_the_master_s_salon(salon: Tenant, other_salon: Tenant) -> None:
    client, _ = _client("ops-page", ops=True)
    master = _master(salon, "Мастер A")
    own = _person(salon, "Своя")
    foreign = _person(other_salon, "Чужая")

    response = _post(client, [master])

    assert response.status_code == 200
    candidates = {str(p.pk) for p in response.context["candidates"]}
    assert str(own.pk) in candidates  # присутствие впереди отсутствия
    assert str(foreign.pk) not in candidates
    assert response.context["master"].pk == master.pk


def test_more_than_one_master_is_refused_without_a_page(salon: Tenant) -> None:
    client, _ = _client("ops-two", ops=True)
    first, second = _master(salon, "Первая"), _master(salon, "Вторая")

    response = _post(client, [first, second])

    levels = [level for level, _ in _messages(response)]
    assert 40 in levels  # ERROR
    assert "candidates" not in (response.context or {})


# --- применение: через фасад, с журналом -------------------------------------


def test_apply_links_person_through_the_facade_and_journals(salon: Tenant) -> None:
    client, user = _client("ops-apply", ops=True)
    master = _master(
        salon, "С ключом", catalog_specialist_id=uuid.uuid4(), ayla_user_id=uuid.uuid4()
    )
    person = _person(salon, "Человек")

    response = _post(client, [master], apply="1", bot_user_id=str(person.pk))

    row = CatalogMaster.all_tenants.get(pk=master.pk)
    assert row.linked_bot_user_id == person.pk
    assert row.invite_status == CatalogMaster.InviteStatus.ACCEPTED
    assert row.is_active is True

    # Доказательство «через фасад»: строку аудита пишет только он.
    rows = _audit_rows(master)
    assert len(rows) == 1
    assert rows[0].payload["surface"] == "django_admin"
    assert rows[0].payload["actor_label"] == f"django_admin:user={user.pk}"
    assert rows[0].payload["identity_status"] == facade.STATUS_SUCCESS

    # След админки — LogEntry с автором.
    entry = LogEntry.objects.filter(object_id=str(master.pk), user=user).order_by("-id").first()
    assert entry is not None
    assert facade.STATUS_SUCCESS in entry.get_change_message()

    levels = {level for level, _ in _messages(response)}
    assert 25 in levels  # SUCCESS


def test_apply_reports_identity_unavailable_as_warning_with_the_reason(
    salon: Tenant,
) -> None:
    """Связь есть, identity нет — оператору обе половины и причина по имени."""
    client, _ = _client("ops-warn", ops=True)
    master = _master(salon, "Без ключа")
    person = _person(salon, "Человек")

    response = _post(client, [master], apply="1", bot_user_id=str(person.pk))

    row = CatalogMaster.all_tenants.get(pk=master.pk)
    assert row.linked_bot_user_id == person.pk
    assert row.catalog_specialist_id is None
    msgs = _messages(response)
    warning = [text for level, text in msgs if level == 30]
    assert len(warning) == 1
    assert REASON_CREATION_UNAVAILABLE in warning[0]
    assert "заводится оператором" in warning[0]  # текст восстановления, а не «никогда»
    assert _audit_rows(master)[0].payload["identity_reason"] == REASON_CREATION_UNAVAILABLE


def test_person_of_another_salon_is_refused_and_nothing_is_written(
    salon: Tenant, other_salon: Tenant
) -> None:
    client, _ = _client("ops-foreign", ops=True)
    master = _master(salon, "Мастер A", catalog_specialist_id=uuid.uuid4())
    foreign = _person(other_salon, "Чужая")

    response = _post(client, [master], apply="1", bot_user_id=str(foreign.pk))

    row = CatalogMaster.all_tenants.get(pk=master.pk)
    assert row.linked_bot_user_id is None
    assert [level for level, _ in _messages(response)] == [40]
    # empty-assert-ok: тот же запрос даёт 1 строку в узле применения выше
    assert _audit_rows(master) == []


def test_manual_id_that_is_not_a_uuid_is_refused_not_500(salon: Tenant) -> None:
    """Поле руками принимает что угодно; кривой id — отказ словами, не 500."""
    client, _ = _client("ops-badid", ops=True)
    master = _master(salon, "Мастер A", catalog_specialist_id=uuid.uuid4())

    response = _post(client, [master], apply="1", bot_user_id_manual="not-a-uuid")

    assert response.status_code == 200
    assert [level for level, _ in _messages(response)] == [40]
    assert CatalogMaster.all_tenants.get(pk=master.pk).linked_bot_user_id is None


def test_card_of_someone_else_answers_with_the_code_door_word(salon: Tenant) -> None:
    client, _ = _client("ops-wrong", ops=True)
    owner = _person(salon, "Владелица карточки")
    master = _master(salon, "Занятая", linked_bot_user=owner, catalog_specialist_id=uuid.uuid4())
    person = _person(salon, "Другой")

    response = _post(client, [master], apply="1", bot_user_id=str(person.pk))

    row = CatalogMaster.all_tenants.get(pk=master.pk)
    assert row.linked_bot_user_id == owner.pk
    errors = [text for level, text in _messages(response) if level == 40]
    assert len(errors) == 1
    assert CatalogMasterAdmin.ONBOARD_REFUSAL_WORDS["wrong_recipient"] in errors[0]


# --- полнота словаря отказов ---------------------------------------------------


def test_every_refusal_slug_has_a_word_for_the_operator() -> None:
    facade_slugs = {
        cls.slug
        for cls in (
            facade.ForeignTenantRefused,
            facade.TenantInactive,
            facade.PersonInOtherTenant,
            facade.MasterInOtherTenant,
        )
    }
    core_slugs = {
        cls.slug
        for cls in (
            staff_invites.InviteMasterMissing,
            staff_invites.MasterAlreadyLinked,
            staff_invites.PersonAlreadyMaster,
        )
    }
    words = CatalogMasterAdmin.ONBOARD_REFUSAL_WORDS
    assert facade_slugs | core_slugs <= set(words)
    assert all(text.strip() for text in words.values())
