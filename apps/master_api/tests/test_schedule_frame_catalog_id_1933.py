"""Рамка расписания читает Ayla по id профиля каталога (DRF-1933, часть 1).

``schedule_frame._load_ayla`` делал ``specialist_id = str(master.id)`` и слал
его в три вызова салонного клиента. Перепись по вызываемому (15.09) нашла
его, а поиск по написанию ``specialist_id=str(master.id)`` — нет: значение
шло через переменную.

Пустая колонка — ``SalonNotConfigured`` с причиной ``catalog_specialist_unresolved``:
этот класс вызывающие уже разбирают (расписание мастера, готовность, экраны
администратора), и пустота не превращается в «весь день свободен».

Красный до правки: «соло» и «приглашение» (уходит pk), «отказ» (уходит pk).
Зелёный в обе стороны: салонная строка синка.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.salon_client import SalonNotConfigured
from apps.master_api.services import schedule_frame

pytestmark = pytest.mark.django_db

_HAS_COLUMN = any(f.name == "catalog_specialist_id" for f in CatalogMaster._meta.get_fields())
CALLS = ("get_master_schedule", "list_schedule_exceptions", "list_time_off")


@pytest.fixture
def salon(monkeypatch, bot_user) -> MagicMock:
    client = MagicMock()
    client.get_master_schedule.return_value = []
    client.list_schedule_exceptions.return_value = []
    client.list_time_off.return_value = []
    monkeypatch.setattr(schedule_frame, "get_salon_client", lambda: client)
    monkeypatch.setattr(schedule_frame, "_ayla_read_actor", lambda tenant: bot_user)
    return client


def _set_catalog_id(master: CatalogMaster, *, raw_id: str | None, column: str | None) -> None:
    CatalogMaster.all_tenants.filter(pk=master.pk).update(raw={"id": raw_id} if raw_id else {})
    if _HAS_COLUMN:
        CatalogMaster.all_tenants.filter(pk=master.pk).update(catalog_specialist_id=column)
    master.refresh_from_db()


def _load(master: CatalogMaster, settings) -> None:
    settings.BOOKING_VIA_AYLA_REST = True
    schedule_frame.load_day_frame(
        master, from_date=date(2026, 9, 15), to_date=date(2026, 9, 21), tz=ZoneInfo("Europe/Moscow")
    )


@pytest.mark.parametrize("kind", ["solo", "invite_glued"])
def test_all_three_reads_use_the_catalog_id(accepted_master, salon, settings, kind):
    catalog_id = str(uuid.uuid4())
    _set_catalog_id(
        accepted_master, raw_id=None if kind == "solo" else catalog_id, column=catalog_id
    )

    _load(accepted_master, settings)

    sent = {name: str(getattr(salon, name).call_args.kwargs["specialist_id"]) for name in CALLS}
    assert sent == {name: catalog_id for name in CALLS}


def test_an_unresolved_catalog_id_is_named_not_read(accepted_master, salon, settings):
    _set_catalog_id(accepted_master, raw_id=None, column=None)

    with pytest.raises(SalonNotConfigured, match="catalog_specialist_unresolved"):
        _load(accepted_master, settings)
    assert [getattr(salon, name).call_count for name in CALLS] == [0, 0, 0]


def test_a_synced_salon_row_still_reads_by_its_primary_key(accepted_master, salon, settings):
    pk = str(accepted_master.pk)
    _set_catalog_id(accepted_master, raw_id=pk, column=pk)

    _load(accepted_master, settings)

    assert {str(getattr(salon, n).call_args.kwargs["specialist_id"]) for n in CALLS} == {pk}
