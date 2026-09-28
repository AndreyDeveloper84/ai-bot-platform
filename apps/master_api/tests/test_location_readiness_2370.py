"""DRF-2370 — пункт «Место работы» готовности читает каталог по его же правилу.

До DRF-2370 ``_location_item()`` отвечал ``unavailable``/``capability_not_built``
безусловно, хотя способ указать место построен целиком (каталог #502,
``/service-locations``, экран 05). Пункт входит в обязательные — и ``ready`` не
был истинным ни у кого.

Правило — каталожное, этап отправки профиля (``users/publication.py``): место
есть и не ``inactive``. ``review_required`` — готово к отправке; ``confirmed``
ставит модератор при одобрении. Зона выезда место не заменяет.

Узлы:

* места нет → ``missing`` / ``location_not_assigned`` — и зона выезда этого не меняет;
* место ``review_required`` и ``confirmed`` → ``done``;
* место ``inactive`` → ``missing`` / ``location_inactive``;
* каталог молчит или отказывает → ``unknown`` с именем причины, не ``missing``;
* без субъекта (карточка оператора) → ``unknown`` / ``no_subject``, каталог не спрашивается;
* каталог спрошен под тем мастером и тем субъектом;
* с местом и остальным настроенным ``ready`` наконец истинно.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.booking_client import BookingUnavailableError
from apps.master_api.services.onboarding_readiness import build_readiness
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

CLIENT = "apps.master_api.services.onboarding_readiness.get_ayla_booking_client"


def _client(*, places=(), areas=(), error=None) -> MagicMock:
    fake = MagicMock()
    if error is not None:
        fake.get_service_locations.side_effect = error
    else:
        fake.get_service_locations.return_value = {
            "specialist_id": "s",
            "city": "Москва",
            "places": list(places),
            "areas": list(areas),
        }
    return fake


def _location(master: CatalogMaster, fake: MagicMock, *, actor: str | None = "max:12345"):
    with patch(CLIENT, return_value=fake):
        readiness = build_readiness(master, actor=actor)
    return next(item for item in readiness.items if item.key == "location")


def test_no_place_is_missing_and_a_mobile_area_does_not_replace_it(accepted_master):
    item = _location(accepted_master, _client(areas=[{"kind": "mobile", "coverage": "whole_city"}]))

    assert item.state == "missing"
    assert item.reason == "location_not_assigned"
    assert item.detail == {"place_status": None, "areas": 1}


@pytest.mark.parametrize("status", ["review_required", "confirmed"])
def test_a_place_that_is_not_inactive_is_done(accepted_master, status):
    item = _location(accepted_master, _client(places=[{"id": "p", "status": status}]))

    assert item.state == "done"
    assert item.reason is None
    assert item.detail["place_status"] == status


def test_an_inactive_place_is_missing(accepted_master):
    item = _location(accepted_master, _client(places=[{"id": "p", "status": "inactive"}]))

    assert item.state == "missing"
    assert item.reason == "location_inactive"


def test_a_silent_catalog_is_unknown_not_missing(accepted_master):
    item = _location(accepted_master, _client(error=BookingUnavailableError("down")))

    assert item.state == "unknown"
    assert item.reason == "BookingUnavailableError"


def test_without_a_subject_the_catalog_is_not_asked(accepted_master):
    fake = _client(places=[{"id": "p", "status": "confirmed"}])

    item = _location(accepted_master, fake, actor=None)

    assert item.state == "unknown"
    assert item.reason == "no_subject"
    fake.get_service_locations.assert_not_called()


def test_the_catalog_is_asked_as_this_master(accepted_master):
    from apps.catalog.specialist_ref import catalog_specialist_id

    fake = _client(places=[{"id": "p", "status": "review_required"}])

    _location(accepted_master, fake, actor="max:12345")

    fake.get_service_locations.assert_called_once_with(
        specialist_id=catalog_specialist_id(accepted_master), external_user_id="max:12345"
    )


def test_the_endpoint_passes_the_masters_subject(client: Client, accepted_master):
    """Ручка — не только функция: субъект обязан доехать из запроса."""
    fake = _client(places=[{"id": "p", "status": "review_required"}])

    with patch(CLIENT, return_value=fake):
        resp = client.get(
            reverse("master_api:onboarding_readiness"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )

    assert resp.status_code == 200, resp.content
    location = next(i for i in resp.json()["items"] if i["key"] == "location")
    assert location["state"] == "done"
    assert fake.get_service_locations.call_count == 1
    assert fake.get_service_locations.call_args.kwargs["external_user_id"]


def test_with_a_place_and_everything_else_set_ready_is_finally_true(
    tenant, accepted_master, settings
):
    """Положительная сторона: до DRF-2370 это состояние было недостижимо ни для кого."""
    from datetime import datetime, timezone

    from apps.catalog.models import CatalogService, MasterService
    from apps.scheduling.models import WorkingHours

    settings.BOOKING_VIA_AYLA_REST = False
    svc = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=2370,
        external_updated_at=datetime.now(tz=timezone.utc),
        slug="readiness-2370",
        name="Маникюр (2370)",
        duration_min=60,
        price_from=1500,
        is_active=True,
    )
    MasterService.all_tenants.create(tenant=tenant, master=accepted_master, service=svc)
    WorkingHours.all_tenants.create(
        tenant=tenant,
        master=accepted_master,
        day_of_week=2,
        is_working=True,
        start_time="10:00",
        end_time="19:00",
    )
    accepted_master.photo_url = "/media/master_photos/y.jpg"
    accepted_master.save(update_fields=["photo_url"])

    fake = _client(places=[{"id": "p", "status": "review_required"}])
    with patch(CLIENT, return_value=fake):
        readiness = build_readiness(accepted_master, actor="max:12345")

    assert {item.key: item.state for item in readiness.items} == {
        "services": "done",
        "location": "done",
        "hours": "done",
        "profile": "done",
    }
    assert readiness.blocking == []
    assert readiness.ready is True


def test_an_unconfigured_client_is_unknown_not_a_crash(accepted_master, settings):
    settings.AYLA_BASE_URL = ""

    readiness = build_readiness(accepted_master, actor="max:12345")
    item = next(i for i in readiness.items if i.key == "location")

    assert item.state == "unknown"
    assert item.reason == "booking_client_not_configured"
