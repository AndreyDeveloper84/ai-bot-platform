"""Право оператора платформы: вход в админку — не бизнес-право.

Решение владельца разделяет две вещи, которые до сих пор были одной:

* ``is_staff`` — **только** «может войти в Django Admin»;
* ``tenancy.platform_operations`` — «может выполнять действия поперёк
  салонов».

Карточки доступов и приглашений показывают строки ВСЕХ салонов
(``get_queryset`` берёт ``all_tenants``), то есть это ровно тот
cross-tenant доступ, ради которого право заведено.

**Главный узел здесь — отрицательный:** сотрудник с ``is_staff`` и без
права карточек не видит. Без него разделение существовало бы на словах:
объявить право и не проверить его — то же самое, что не объявлять.

Право выдаётся ГРУППОЙ «Ayla Operations», а не поштучно, и узел про
группу проверяет именно этот путь: поштучная выдача не переживает второго
оператора.
"""

from __future__ import annotations

import secrets

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client
from django.urls import reverse

from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PERM_CODENAME = "platform_operations"
PERM_FULL = f"tenancy.{PERM_CODENAME}"

#: Обе карточки закрыты одним правом — проверяем обе, а не одну:
#: право, забытое на второй, невидимо ровно до боевого случая.
CARD_URLS = (
    "admin:tenancy_tenantstaff_changelist",
    "admin:tenancy_staffinvite_changelist",
)


def _login(username: str, *, is_staff: bool, perms: bool = False, group: bool = False) -> Client:
    password = secrets.token_urlsafe(24)
    user = get_user_model().objects.create_user(
        username=username, password=password, is_staff=is_staff
    )
    if perms or group:
        permission = Permission.objects.get(
            codename=PERM_CODENAME, content_type__app_label="tenancy"
        )
        if group:
            # ``get_or_create``, а не ``create``: группу заводит миграция
            # 0018, а имя группы уникально. ``create`` здесь падал бы —
            # и падал бы НЕ по предмету теста, а по тому, что предмет
            # уже существует.
            ops, _ = Group.objects.get_or_create(name="Ayla Operations")
            ops.permissions.add(permission)
            user.groups.add(ops)
        else:
            user.user_permissions.add(permission)
    client = Client()
    assert client.login(username=username, password=password)
    return client


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="perm-salon", name="Салон прав")


class TestThePermissionExistsAtAll:
    def test_the_permission_row_is_created_by_the_migration(self) -> None:
        """ПРИСУТСТВИЕ впереди отказов.

        Все узлы ниже проверяют, что без права не пускают. Если бы права
        не существовало вовсе, они были бы зелены по той же причине — и
        не сказали бы ничего.
        """
        assert Permission.objects.filter(
            codename=PERM_CODENAME, content_type__app_label="tenancy"
        ).exists()


class TestIsStaffAloneIsNotEnough:
    @pytest.mark.parametrize("url_name", CARD_URLS)
    def test_staff_without_the_permission_cannot_open_the_card(
        self, url_name: str, salon: Tenant
    ) -> None:
        """Тот самый узел, ради которого разделение и вводилось."""
        client = _login("ops.plain", is_staff=True)
        response = client.get(reverse(url_name))
        assert response.status_code in (302, 403), (
            f"карточка открылась без права: {response.status_code}"
        )

    def test_staff_without_the_permission_does_not_see_them_in_the_index(self) -> None:
        """Список имён — тоже сведения.

        Карточка, светящаяся в индексе приложения тому, кто открыть её не
        может, сообщает и о существовании доступов, и о том, где смотреть.
        """
        client = _login("ops.plain2", is_staff=True)
        body = client.get(reverse("admin:index")).content.decode("utf-8")

        # ПРИСУТСТВИЕ: индекс отрисовался — иначе отсутствие ссылок
        # означало бы пустую страницу.
        assert "<body" in body
        assert "tenancy/tenantstaff" not in body
        assert "tenancy/staffinvite" not in body


class TestThePermissionOpensBothCards:
    @pytest.mark.parametrize("url_name", CARD_URLS)
    def test_direct_permission_opens_the_card(self, url_name: str, salon: Tenant) -> None:
        client = _login("ops.direct", is_staff=True, perms=True)
        assert client.get(reverse(url_name)).status_code == 200

    @pytest.mark.parametrize("url_name", CARD_URLS)
    def test_the_group_is_a_working_path_not_a_convention(
        self, url_name: str, salon: Tenant
    ) -> None:
        """Выдача через группу «Ayla Operations» — то, как это делают.

        Узел существует потому, что «выдавать через группу» — договор, а
        договор без проверки держится до первого исполнителя, который
        выдаст право поштучно.
        """
        client = _login("ops.group", is_staff=True, group=True)
        assert client.get(reverse(url_name)).status_code == 200
