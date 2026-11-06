"""DRF-2666 — словарь из тела заявки о разрыве канона не доезжает до каталога.

``name`` / ``description`` заявки уходят в каталог (``create_canon_gap_request``)
и становятся чужими данными, которые читает куратор. Было:
``str(body.get("name"))`` — словарь превращался в текст ``"{'a': 1}"``.

Свойство: ни один нестроковый объект не доезжает до каталога ни как значение,
ни как его строковое представление. Узлы проверяют аргументы вызова каталога,
а не только то, что дверь ответила 400. Пара: строка доезжает дословно.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.master_api.tests.test_canon_gap_requests_1802 import BODY, _auth, ayla  # noqa: F401

pytestmark = pytest.mark.django_db

DICT = {"a": 1}


def _post(client: Client, **over):
    return client.post(
        reverse("master_api:canon_gap_requests"),
        data=json.dumps({**BODY, **over}),
        content_type="application/json",
        **_auth(),
    )


class TestNoDictReachesTheCatalog:
    @pytest.mark.parametrize("field", ["name", "description"])
    def test_a_dict_is_refused_at_the_door_and_the_catalog_is_not_called(
        self,
        client: Client,
        accepted_master,
        bot_user,
        ayla,  # noqa: F811
        field,
    ):
        resp = _post(client, **{field: DICT})

        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"
        assert ayla.create_canon_gap_request.call_count == 0

    def test_a_string_reaches_the_catalog_verbatim(
        self,
        client: Client,
        accepted_master,
        bot_user,
        ayla,  # noqa: F811
    ):
        resp = _post(client, name="Татуаж губ", description="акварельный")

        assert resp.status_code == 201, resp.content
        kwargs = ayla.create_canon_gap_request.call_args.kwargs
        assert (kwargs["name"], kwargs["description"]) == ("Татуаж губ", "акварельный")
        assert "{'a': 1}" not in json.dumps(kwargs, default=str)
