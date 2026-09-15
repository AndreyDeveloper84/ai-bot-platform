"""Мини-апп клиента: запись и котировка шлют в каталог id профиля (DRF-1933, часть 2).

Клиент выбирает мастера по первичному ключу зеркала (``CatalogMaster.id``), и
локально он так и остаётся. В каталог уходит ``catalog_specialist_id`` —
у склеенного приглашения и соло-мастера это другой UUID.

* запись: пустая колонка — ``409 master_unbookable``, каталог не зовётся;
* котировка: пустая колонка — ребро не спрашивается, ответ из зеркала
  (``source: service``) — ровно как при недоступном ребре.

Красный до правки: склеенная строка (уходит pk), отказ (каталог зовут).
Зелёный в обе стороны: строка синка.
"""

from __future__ import annotations

import uuid

import pytest

from apps.catalog.models import CatalogMaster
from apps.miniapp_api.tests import test_quote_changed_1708 as _quote_module

# Фикстуры и помощники соседнего модуля — присваиванием, а не импортом по
# имени: pytest находит их по атрибутам модуля, а параметры тестов не
# «переопределяют» импорт (ruff F811).
SERVICE_AYLA_ID = _quote_module.SERVICE_AYLA_ID
_post = _quote_module._post
_quote = _quote_module._quote
_settings = _quote_module._settings
bot_user = _quote_module.bot_user
master = _quote_module.master
service = _quote_module.service
stub = _quote_module.stub
tenant = _quote_module.tenant

pytestmark = pytest.mark.django_db


def _set_column(master: CatalogMaster, value: uuid.UUID | None) -> None:
    CatalogMaster.all_tenants.filter(pk=master.pk).update(catalog_specialist_id=value)


class TestCreate:
    def test_glued_row_books_by_the_catalog_id(self, client, bot_user, master, service, stub):
        catalog_id = uuid.uuid4()
        _set_column(master, catalog_id)

        resp = _post(client, service, master)

        assert resp.status_code == 201, resp.content
        assert stub.calls[0]["specialist_id"] == str(catalog_id)

    def test_empty_column_refuses_without_calling_the_catalog(
        self, client, bot_user, master, service, stub
    ):
        _set_column(master, None)

        resp = _post(client, service, master)

        assert resp.status_code == 409, resp.content
        assert resp.json()["error"] == "master_unbookable"
        assert len(stub.calls) == 0

    def test_synced_row_still_books_by_its_primary_key(
        self, client, bot_user, master, service, stub
    ):
        _set_column(master, master.pk)

        resp = _post(client, service, master)

        assert resp.status_code == 201, resp.content
        assert stub.calls[0]["specialist_id"] == str(master.pk)


class TestQuote:
    def test_glued_row_reads_the_edge_by_the_catalog_id(
        self, client, bot_user, master, service, stub
    ):
        catalog_id = uuid.uuid4()
        _set_column(master, catalog_id)
        stub.edges = [{"price": "1700.00", "duration_minutes": 45}]

        r = _quote(client, service, master)

        assert r.status_code == 200, r.content
        assert r.json()["quote"]["source"] == "edge"
        assert stub.edge_calls == [
            {"specialist_id": str(catalog_id), "service_id": str(SERVICE_AYLA_ID)}
        ]

    def test_empty_column_quotes_from_the_mirror_without_the_edge(
        self, client, bot_user, master, service, stub
    ):
        _set_column(master, None)
        stub.edges = [{"price": "1700.00", "duration_minutes": 45}]

        r = _quote(client, service, master)

        assert r.status_code == 200, r.content
        assert r.json()["quote"]["source"] == "service"
        assert len(stub.edge_calls) == 0

    def test_synced_row_still_reads_the_edge_by_its_primary_key(
        self, client, bot_user, master, service, stub
    ):
        _set_column(master, master.pk)
        stub.edges = [{"price": "1700.00", "duration_minutes": 45}]

        r = _quote(client, service, master)

        assert r.json()["quote"]["source"] == "edge"
        assert stub.edge_calls[0]["specialist_id"] == str(master.pk)
