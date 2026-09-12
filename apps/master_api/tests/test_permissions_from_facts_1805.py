"""Права в ``/me`` — из проводки, не константы (DRF-1805, M13).

Что заперто:

- сегодня: ``can_edit_services`` ложно (ручки M10 нет), заявка о
  недоступности и ответ клиенту — истинны;
- право следует за URLconf: появился маршрут ``services_write`` — право
  истинно без правки ``/me``; исчез маршрут ответа клиенту — право ложно;
- сторож против возврата констант: подмена ``route_exists`` меняет ответ
  ``/me`` — то есть ``/me`` читает факт, а не число.
"""

from __future__ import annotations

import pytest
from django.http import HttpResponse
from django.test import Client
from django.urls import path, reverse

from apps.master_api.services import permissions as perms
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db


def _me(client: Client) -> dict:
    resp = client.get(reverse("master_api:me"), HTTP_AUTHORIZATION=init_data_header("12345"))
    assert resp.status_code == 200, resp.content
    return resp.json()["permissions"]


class TestToday:
    def test_services_write_is_not_built_so_the_right_is_false(
        self, client: Client, accepted_master
    ):
        permissions = _me(client)
        assert permissions == {
            "can_edit_schedule": True,
            "can_edit_services": False,
            "can_message_customers": True,
        }

    def test_route_table_names_only_routes_that_exist_or_are_named_for_m10(self):
        assert perms.route_exists("master_api:availability_request")
        assert perms.route_exists("master_api:conversation_send_message")
        assert not perms.route_exists("master_api:services_write")
        assert not perms.route_exists("master_api:no_such_route")


class TestRightFollowsTheWiring:
    def test_adding_the_m10_route_turns_the_right_on(self, client: Client, accepted_master):
        """M10 добавляет маршрут — право становится истиной без правки ``/me``."""
        from apps.master_api import urls as master_urls

        master_urls.urlpatterns.append(
            path("services", lambda request: HttpResponse(status=204), name="services_write")
        )
        try:
            from django.urls import clear_url_caches

            clear_url_caches()
            assert _me(client)["can_edit_services"] is True
        finally:
            master_urls.urlpatterns.pop()
            clear_url_caches()

    def test_me_reads_the_fact_not_a_constant(self, client: Client, accepted_master, monkeypatch):
        """Подмена проверки маршрута меняет ответ — значит, ``/me`` её читает."""
        monkeypatch.setattr(perms, "route_exists", lambda name: False)
        assert _me(client) == {
            "can_edit_schedule": False,
            "can_edit_services": False,
            "can_message_customers": False,
        }
