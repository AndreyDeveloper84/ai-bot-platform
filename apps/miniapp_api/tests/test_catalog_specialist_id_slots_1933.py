"""Выбор времени клиентом: окна Ayla читаются по id профиля каталога (DRF-1933, часть 2).

Красный до правки: склеенная строка (уходит pk), пустая колонка (каталог
зовут). Зелёный в обе стороны: строка синка.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from apps.catalog.models import CatalogMaster
from apps.miniapp_api.tests import test_slots_ayla_source_1062 as _slots_module

# Фикстуры и помощники соседнего модуля — присваиванием, а не импортом по
# имени: pytest находит их по атрибутам модуля, а параметры тестов не
# «переопределяют» импорт (ruff F811).
CLIENT_PATH = _slots_module.CLIENT_PATH
_ayla_path = _slots_module._ayla_path
_bot_token = _slots_module._bot_token
_fake_client = _slots_module._fake_client
_get = _slots_module._get
bot_user = _slots_module.bot_user
master = _slots_module.master
master_service = _slots_module.master_service
open_every_day = _slots_module.open_every_day
service = _slots_module.service
sunday = _slots_module.sunday
tenant = _slots_module.tenant

pytestmark = pytest.mark.django_db


def _set_column(master: CatalogMaster, value: uuid.UUID | None) -> None:
    CatalogMaster.all_tenants.filter(pk=master.pk).update(catalog_specialist_id=value)


def test_glued_row_reads_slots_by_the_catalog_id(
    client, bot_user, master, service, master_service, open_every_day, sunday
):
    catalog_id = uuid.uuid4()
    _set_column(master, catalog_id)
    fake, calls = _fake_client({})

    with patch(CLIENT_PATH, return_value=fake):
        resp = _get(client, master, service, sunday)

    assert resp.status_code == 200, resp.content
    assert {c["specialist_id"] for c in calls} == {str(catalog_id)}


def test_empty_column_is_master_unbookable_without_calling_the_catalog(
    client, bot_user, master, service, master_service, open_every_day, sunday
):
    _set_column(master, None)
    fake, calls = _fake_client({})

    with patch(CLIENT_PATH, return_value=fake):
        resp = _get(client, master, service, sunday)

    assert resp.status_code == 409, resp.content
    assert resp.json()["error"] == "master_unbookable"
    assert len(calls) == 0


def test_synced_row_still_reads_slots_by_its_primary_key(
    client, bot_user, master, service, master_service, open_every_day, sunday
):
    _set_column(master, master.pk)
    fake, calls = _fake_client({})

    with patch(CLIENT_PATH, return_value=fake):
        resp = _get(client, master, service, sunday)

    assert resp.status_code == 200, resp.content
    assert {c["specialist_id"] for c in calls} == {str(master.pk)}
