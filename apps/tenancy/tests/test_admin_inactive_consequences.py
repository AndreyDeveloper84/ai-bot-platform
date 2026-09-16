"""Неактивный салон: галочку не повернуть, последствия названы.

Два предмета, и оба — про то, чего карточка не делала.

**A. Случайной активации больше нет.** Решение владельца дословно:
«нельзя случайно активировать operator-действием». До этой правки
``is_active`` стоял в форме обычным полем — салон включался одним
движением, мимо всяких проверок и без следа о том, кто это сделал.
Возможность не отнята насовсем: явная реактивация уезжает в доменную
операцию, где у неё будут проверки и авторство, а командный путь
остаётся. Закрыто именно СЛУЧАЙНОЕ действие.

**B. Последствия названы словами.** Флаг был показан и раньше — и не
говорил оператору ни одного из четырёх последствий. «Флаг показан» и
«человек знает, что произойдёт» — разные вещи, и дорогая здесь вторая:
по ней решают, чинить салон или заводить заново.

Проверяется ОТРИСОВАННЫЙ ответ, а не объявление полей. Сверять
``readonly_fields`` с собственным списком — проверка, которая не может
провалиться: обе её стороны из одного источника.
"""

from __future__ import annotations

import re
import secrets

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.tenancy.admin import TenantAdmin
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

#: Четыре последствия из решения владельца. Держим списком, потому что
#: узел ниже требует ВСЕ: три из четырёх — это молча потерянное
#: ограничение, а не «почти всё показали».
CONSEQUENCES = (
    "мастера нельзя сделать публичным",
    "новую запись начать нельзя",
    "привязка локации или услуги не снимает запрет на публикацию",
    "случайно активировать салон действием оператора нельзя",
)


@pytest.fixture
def superuser_client() -> Client:
    password = secrets.token_urlsafe(24)
    get_user_model().objects.create_superuser(username="ops.inactive", email="", password=password)
    client = Client()
    assert client.login(username="ops.inactive", password=password)
    return client


@pytest.fixture
def dead_salon() -> Tenant:
    return Tenant.objects.create(slug="dead-salon", name="Закрытый салон", is_active=False)


@pytest.fixture
def live_salon() -> Tenant:
    return Tenant.objects.create(slug="live-salon", name="Работающий салон", is_active=True)


def _change_form(client: Client, tenant: Tenant) -> str:
    url = reverse("admin:tenancy_tenant_change", args=[tenant.pk])
    return client.get(url).content.decode("utf-8")


class TestTheCheckboxCannotActivate:
    def test_the_form_has_no_editable_is_active_input(
        self, superuser_client: Client, dead_salon: Tenant
    ) -> None:
        """Проверяем ОТРИСОВКУ: редактируемого поля в форме нет.

        Суперпользователь взят намеренно: если бы запрет держался на
        роли, обошёл бы его именно он.
        """
        body = _change_form(superuser_client, dead_salon)

        # ПРИСУТСТВИЕ: форма открылась и это форма нужного салона —
        # иначе отсутствие поля означало бы просто пустую страницу.
        assert "Закрытый салон" in body

        # ОТСУТСТВИЕ: ни одного редактируемого ввода с этим именем.
        assert not re.search(r'<input[^>]+name="is_active"', body)

    def test_the_flag_is_still_visible_to_the_operator(
        self, superuser_client: Client, dead_salon: Tenant
    ) -> None:
        """Скрыть — не то же самое, что запретить править.

        Оператор обязан ВИДЕТЬ состояние салона; у него отняли ручку, а
        не показания прибора.
        """
        assert "is_active" in TenantAdmin.readonly_fields
        body = _change_form(superuser_client, dead_salon)
        assert "Салон неактивен" in body


class TestConsequencesAreNamed:
    def test_all_four_are_named_for_an_inactive_salon(
        self, superuser_client: Client, dead_salon: Tenant
    ) -> None:
        body = _change_form(superuser_client, dead_salon)
        missing = [item for item in CONSEQUENCES if item not in body]
        assert not missing, f"не названы последствия: {missing}"

    def test_an_active_salon_says_so_instead_of_listing_bans(
        self, superuser_client: Client, live_salon: Tenant
    ) -> None:
        body = _change_form(superuser_client, live_salon)

        assert "Салон активен" in body
        # Перечня запретов у живого салона быть не должно: список,
        # который висит всегда, читается как всегда действующий.
        assert CONSEQUENCES[0] not in body

    def test_an_unsaved_row_says_no_data_rather_than_allowed(self) -> None:
        """Третий ответ, а не второй.

        У несохранённой строки состояния нет вовсе. Ответить ей «салон
        активен, ограничений нет» значило бы утверждать измеренное там,
        где ничего не измеряли.
        """
        rendered = TenantAdmin.inactive_consequences(TenantAdmin, Tenant())
        assert rendered == "нет данных"
