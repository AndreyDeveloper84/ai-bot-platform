"""DRF-2666 — словарь из тела отгула / исключения не доезжает до каталога.

``reason`` отгула и ``note`` исключения по дате уходят в каталог на токене
администратора и становятся чужими данными. Было ``str(body.get("reason"))`` —
словарь превращался в текст ``"{'a': 1}"`` в отгуле мастера.

Свойство проверяется по запросу, ушедшему в каталог, а не по коду ответа:
при словаре в каталог не ушло НИЧЕГО; при строке — ушла она, дословно.
"""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.test_salon_schedule_writes_2607 import (  # noqa: F401
    _empty_token_cache,
    _salon_bot_signs,
    _Wire,
    synced_master,  # noqa: F811
)

from .conftest import init_data_header

pytestmark = pytest.mark.django_db

DICT = {"a": 1}


def _time_off(client: Client, master, reason):
    return client.post(
        reverse("admin_api:master_time_off", args=[str(master.id)]),
        data=json.dumps(
            {
                "start_at": "2031-01-10T09:00:00+03:00",
                "end_at": "2031-01-10T18:00:00+03:00",
                "reason": reason,
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


def _exception(client: Client, master, note):
    return client.put(
        reverse("admin_api:master_date_exception", args=[str(master.id)]),
        data=json.dumps({"date": "2031-01-11", "is_working_day": False, "note": note}),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("5001"),
    )


def _sent_bodies(wire) -> list[dict]:
    return [json.loads(r.content or b"{}") for r in wire.sent]


@pytest.mark.parametrize("send", [_time_off, _exception], ids=["time-off-reason", "exception-note"])
class TestNoDictReachesTheCatalog:
    def test_a_dict_is_refused_and_nothing_leaves_for_the_catalog(
        self,
        client: Client,
        owner_bot_user,
        synced_master,  # noqa: F811
        monkeypatch,
        send,
    ):
        wire = _Wire(monkeypatch, catalog_status=201)
        resp = send(client, synced_master, DICT)

        assert resp.status_code == 400
        assert resp.json()["error"] == "validation"
        assert wire.sent == []  # empty-assert-ok: паре ниже строка уходит — прибор видит запросы

    def test_a_string_leaves_for_the_catalog_verbatim(
        self,
        client: Client,
        owner_bot_user,
        synced_master,  # noqa: F811
        monkeypatch,
        send,
    ):
        wire = _Wire(monkeypatch, catalog_status=201)
        resp = send(client, synced_master, "плановый отгул")

        assert resp.status_code in (200, 201), resp.content
        (body,) = _sent_bodies(wire)
        assert "плановый отгул" in body.values()
        assert "{'a': 1}" not in json.dumps(body)
