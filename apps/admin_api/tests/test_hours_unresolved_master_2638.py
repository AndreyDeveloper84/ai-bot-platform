"""DRF-2638 — экран часов называет причину: «мастер не заведён» ≠ «источник не настроен».

Ниже по пути (``catalog/services/schedule_confirmation`` и
``master_api/services/schedule_frame``) незаведённый в каталоге мастер
превращается в ``SalonNotConfigured``, и обе ручки часов отвечали на него
503 ``schedule_source_not_configured``: человек шёл настраивать источник
расписания, которого настраивать не нужно.

Узел — пара, которая обязана различаться, на каждой из двух ручек: мастер
не заведён → 409 ``catalog_profile_unresolved`` (каталог не зовётся);
источник реально не настроен → 503 ``schedule_source_not_configured``.
«Экран часов отказывает» прошло бы при самом дефекте.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from apps.admin_api.tests.conftest import init_data_header
from apps.admin_api.tests.test_catalog_specialist_id_admin_1933 import _prepare, _Recorder
from apps.integrations.ayla.salon_client import SalonNotConfigured

pytestmark = pytest.mark.django_db

ROUTES = ["admin_api:master_schedule", "admin_api:master_day_schedule"]


def _source_not_configured(*_a, **_kw):
    raise SalonNotConfigured("no active owner/admin staff to read the weekly template")


@pytest.mark.parametrize("route", ROUTES)
def test_an_unregistered_master_is_named_not_the_source(
    client: Client, tenant, owner_bot_user, monkeypatch, settings, route
):
    settings.BOOKING_VIA_AYLA_REST = True
    master, expected = _prepare(tenant, "unresolved")
    assert expected is None
    rec = _Recorder()
    monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: rec)

    resp = client.get(
        reverse(route, args=[str(master.pk)]), HTTP_AUTHORIZATION=init_data_header("5001")
    )

    assert resp.status_code == 409, resp.content
    assert resp.json()["error"] == "catalog_profile_unresolved"
    assert len(rec.calls) == 0


@pytest.mark.parametrize("route", ROUTES)
def test_a_really_unconfigured_source_is_still_named_as_the_source(
    client: Client, tenant, owner_bot_user, monkeypatch, settings, route
):
    settings.BOOKING_VIA_AYLA_REST = True
    master, expected = _prepare(tenant, "synced")
    assert expected is not None
    monkeypatch.setattr(
        "apps.catalog.services.schedule_confirmation.read_weekly_template", _source_not_configured
    )
    monkeypatch.setattr("apps.master_api.services.schedule.build_schedule", _source_not_configured)

    resp = client.get(
        reverse(route, args=[str(master.pk)]), HTTP_AUTHORIZATION=init_data_header("5001")
    )

    assert resp.status_code == 503, resp.content
    assert resp.json()["error"] == "schedule_source_not_configured"
