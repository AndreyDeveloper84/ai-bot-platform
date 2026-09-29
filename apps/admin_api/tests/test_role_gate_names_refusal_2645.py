"""DRF-2645 — ворота admin Mini App называют отказ и ход оператора.

Отказ ворот был голым ``403 forbidden`` «admin or owner role required»: ни
причины, ни хода. А ход есть и назван — роль в салоне выдаёт оператор
``platform_operations`` («Выдать роль в салоне», «Сменить роль»).

Пары, которые обязаны различаться:

* человек с ролью в салоне проходит — человек без роли получает 403 с
  причиной ``no_salon_role`` и ходом ``operator_grants_salon_role``;
* роль есть, но не та (ресепшн на двери владельца/админа) — другая причина,
  ``role_insufficient``, и другой ход; та же ресепшн на чтении дня проходит.

Узел «без роли — 403» закрепил бы сегодняшнее состояние: он прошёл бы и при
голом отказе. Здесь проверяются причина и ход.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser

from .conftest import init_data_header

pytestmark = pytest.mark.django_db


def _get(client: Client, name: str, user_id: str):
    return client.get(reverse(f"admin_api:{name}"), HTTP_AUTHORIZATION=init_data_header(user_id))


class TestTheRefusalNamesItsReasonAndMove:
    def test_a_person_with_a_role_passes_one_without_is_told_who_grants_it(
        self, client: Client, owner_bot_user: BotUser, customer_bot_user: BotUser
    ) -> None:
        passed = _get(client, "masters_list", "5001")
        refused = _get(client, "masters_list", "5005")

        assert passed.status_code == 200, passed.content
        assert refused.status_code == 403
        body = refused.json()
        assert body["error"] == "forbidden"  # экраны держатся за код и статус
        assert body["details"] == {
            "reason": "no_salon_role",
            "remedy": "operator_grants_salon_role",
        }
        assert "Выдать роль в салоне" in body["detail"]

    def test_a_role_that_is_not_enough_is_a_different_reason_and_move(
        self, client: Client, receptionist_bot_user: BotUser
    ) -> None:
        reads_the_day = _get(client, "salon_day", "5003")
        refused = _get(client, "masters_list", "5003")

        assert reads_the_day.status_code == 200, reads_the_day.content
        assert refused.status_code == 403
        assert refused.json()["details"] == {
            "reason": "role_insufficient",
            "remedy": "operator_changes_salon_role",
        }
        assert "Сменить роль" in refused.json()["detail"]
