# ruff: noqa: F811 — фикстуры соседних наборов импортируются и принимаются параметрами
"""DRF-2967 — перепись ручек плана Mini App: каждая пишущая стоит под гейтом.

Гейт плана (согласие на хранение + нет заявки на удаление) ставится в каждой
ручке вручную, первой проверкой. Новая ручка без него была бы дырой, которую
не видит ни один узел, написанный под конкретное имя. Здесь имён нет: ручки
читаются из ``miniapp_api/urls.py``, и каждой задаётся один и тот же вопрос
под каждым из трёх закрытых состояний.

Три рода, и каждая ручка обязана быть ровно в одном:

* **читающая** (не принимает POST) — названа в :data:`READ_ONLY`;
* **открытая** пишущая — названа в :data:`OPEN_WRITES` с причиной;
* **пишущая под гейтом** — все остальные: под закрытым состоянием отвечает
  отказом гейта, и каталог не спрошен.

Новая ручка сама попадает в третий род. Открыть её можно только строкой в
одном из двух списков — то есть решением, а не забывчивостью.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.miniapp_api import urls as miniapp_urls
from apps.miniapp_api.tests.test_plan_basis_gate_2967 import (  # noqa: F401 — fixtures
    BLOCKED,
    MINIAPP_REFUSALS,
    _miniapp,
    _proxy_person_in,
)
from apps.miniapp_api.tests.test_plan_decision_proxy_2879 import CLIENT, NO_PLAN, _auth
from apps.miniapp_api.tests.test_plan_decision_proxy_2879 import (  # noqa: F401 — fixtures
    bot_user as proxy_person,
)

#: Человек этих узлов назван в списке приёмки Плана; сам замок — test_plan_access_2885.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("plan_open_to_everyone")]

#: Клиент каталога в ядре (чат и экран зовут его через модуль клиента).
CORE_CLIENT = "apps.integrations.ayla.plan_engine_client.PlanEngineHttpClient"

#: Читающие ручки: POST не принимают. Чтение своего сохранённого плана под
#: отзывом и заявкой открыто (решение владельца 09.10).
READ_ONLY = frozenset({"customer_plan_current"})

#: Пишущие ручки, открытые под гейтом, — с причиной.
OPEN_WRITES = {
    # «Оставить текущий»: отказ от предложения, данных о человеке после него
    # меньше; под отзывом человеку надо дать его убрать (главное окно 09.10).
    "customer_plan_keep": "архив предложения — отказ, не обработка",
}

#: Ошибки, которыми отвечает гейт (и только он).
GATE_ERRORS = frozenset(error for _, error in MINIAPP_REFUSALS.values())


def _plan_route_names() -> list[str]:
    """Имена ручек плана нового механизма — как они объявлены в urls.py."""
    names = [str(getattr(pattern, "name", "") or "") for pattern in miniapp_urls.urlpatterns]
    return sorted(
        name
        for name in names
        if name.startswith("customer_plan_") and not name.startswith("customer_plan_lite")
    )


ROUTES = _plan_route_names()
GATED = [name for name in ROUTES if name not in READ_ONLY and name not in OPEN_WRITES]


def _post(client: Client, name: str):
    return client.post(
        reverse(f"miniapp_api:{name}"),
        data="{}",
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(),
    )


def test_n0_the_census_sees_the_routes_it_is_about() -> None:
    """Перепись не пуста и видит известные ручки — иначе она ничего не держит."""
    assert {"customer_plan_decision", "customer_plan_save", "customer_plan_step"} <= set(GATED)
    assert READ_ONLY <= set(ROUTES)
    assert set(OPEN_WRITES) <= set(ROUTES)


@pytest.mark.parametrize("name", sorted(READ_ONLY))
def test_n1_a_read_only_route_does_not_accept_a_write(
    name: str, client: Client, proxy_person: BotUser, _miniapp
) -> None:
    assert _post(client, name).status_code == 405


@pytest.mark.parametrize("state", sorted(BLOCKED))
@pytest.mark.parametrize("name", GATED)
def test_n2_every_writing_plan_route_refuses_without_a_basis(
    name: str, state: str, client: Client, proxy_person: BotUser, _miniapp
) -> None:
    _proxy_person_in(state, proxy_person)
    status, error = MINIAPP_REFUSALS[state]

    with patch(CLIENT) as in_view, patch(CORE_CLIENT) as in_core:
        response = _post(client, name)

    assert (response.status_code, response.json()["error"]) == (status, error), response.content
    in_view.assert_not_called()
    in_core.assert_not_called()


@pytest.mark.parametrize("name", GATED)
def test_n2_with_a_basis_the_same_request_gets_past_the_gate(
    name: str, client: Client, proxy_person: BotUser, _miniapp
) -> None:
    """Близнец: тот же пустой запрос согласного человека отказом гейта не
    кончается — значит, отказ выше вызван состоянием, а не формой запроса."""
    with patch(CLIENT) as in_view, patch(CORE_CLIENT):
        # Сборке каталог отвечает исходом «плана нет» — ответ должен сложиться.
        in_view.return_value.compose_decision.return_value = NO_PLAN
        response = _post(client, name)

    assert response.status_code != 405
    body = response.json() if response["Content-Type"].startswith("application/json") else {}
    assert body.get("error") not in GATE_ERRORS, response.content


@pytest.mark.parametrize("state", sorted(BLOCKED))
@pytest.mark.parametrize("name", sorted(OPEN_WRITES))
def test_n3_an_open_write_is_not_stopped_by_the_gate(
    name: str, state: str, client: Client, proxy_person: BotUser, _miniapp
) -> None:
    _proxy_person_in(state, proxy_person)

    with patch(CLIENT), patch(CORE_CLIENT):
        response = _post(client, name)

    assert response.status_code != 405  # ручка пишущая
    body = response.json() if response["Content-Type"].startswith("application/json") else {}
    assert body.get("error") not in GATE_ERRORS, response.content
