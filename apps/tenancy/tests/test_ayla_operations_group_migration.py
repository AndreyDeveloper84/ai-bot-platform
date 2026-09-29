"""Группа «Ayla Operations» заведена МИГРАЦИЕЙ, а не тестом.

Узел существует потому, что соседний набор про право эту группу **создаёт
сам** (``_login(group=True)``). Значит он остался бы зелёным и при
миграции, которая не делает ничего: обе стороны проверки пришли бы из
одного источника — из самого теста.

Здесь предмет другой: то, что появилось в базе ПОСЛЕ применения миграций
и до единой строки, написанной тестом.

Обратная операция проверяется отдельно и ровно в той форме, в какой
задумана: право снимается, **группа остаётся**. Откат миграции — про
схему, а группа к этому моменту может нести выданные людям членства;
снести её значило бы отобрать доступы у тех, кого миграция не заводила.
"""

from __future__ import annotations

from importlib import import_module

import pytest
from django.apps import apps as django_apps
from django.contrib.auth.models import Group, Permission

#: Модуль миграции ввозится по имени: оно начинается с цифры, и обычный
#: ``import`` его не возьмёт. Нужен он ради обратной операции — её
#: проверяют вызовом, а не рассказом о ней.
MIGRATION = import_module("apps.tenancy.migrations.0018_ayla_operations_group")

pytestmark = pytest.mark.django_db

GROUP_NAME = "Ayla Operations"
PERM_CODENAME = "platform_operations"


def _permission() -> Permission:
    return Permission.objects.get(codename=PERM_CODENAME, content_type__app_label="tenancy")


class TestTheMigrationDidIt:
    def test_the_group_exists_without_any_test_creating_it(self) -> None:
        """Ни одна строка выше не заводила группу — она из миграции."""
        assert Group.objects.filter(name=GROUP_NAME).exists()

    def test_the_group_carries_the_platform_operations_permission(self) -> None:
        group = Group.objects.get(name=GROUP_NAME)
        codenames = {p.codename for p in group.permissions.all()}

        # ПРИСУТСТВИЕ: у группы вообще есть права — иначе проверка
        # вхождения ниже говорила бы лишь о пустом наборе.
        assert codenames
        assert PERM_CODENAME in codenames

    def test_the_permission_is_attached_to_the_tenant_content_type(self) -> None:
        """Право висит на ``Tenant``, а не «где-то в auth».

        Имя права уникально только вместе с типом содержимого: одноимённое
        право на другой модели дало бы ``has_perm`` по другому ключу и
        молча не совпало бы с проверкой в админке.
        """
        assert _permission().content_type.app_label == "tenancy"
        assert _permission().content_type.model == "tenant"


class TestTheReverseKeepsTheGroup:
    def test_reverse_removes_the_right_but_not_the_group(self) -> None:
        """Откат отбирает право и оставляет людей на месте.

        Проверяется ВЫЗОВОМ обратной функции, а не рассказом о ней:
        «обратная операция написана» и «обратная операция делает то, что
        обещает» — разные утверждения, и дорогое здесь второе.
        """
        group = Group.objects.get(name="Ayla Operations")

        # ПРИСУТСТВИЕ: до отката право у группы есть.
        assert PERM_CODENAME in {p.codename for p in group.permissions.all()}

        MIGRATION.drop_permission_from_group(django_apps, None)

        group.refresh_from_db()
        # Право снято…
        assert PERM_CODENAME not in {p.codename for p in group.permissions.all()}
        # …а группа на месте: у неё могут быть выданные людям членства,
        # которых эта миграция не заводила.
        assert Group.objects.filter(name="Ayla Operations").exists()

    def test_the_reverse_is_safe_when_the_group_is_already_gone(self) -> None:
        """Откат на дереве, где группу удалили руками, не падает.

        Иначе обратная миграция превратилась бы в ловушку: один удалённый
        оператором объект — и назад не откатиться вовсе.
        """
        Group.objects.filter(name="Ayla Operations").delete()
        MIGRATION.drop_permission_from_group(django_apps, None)  # не бросает
