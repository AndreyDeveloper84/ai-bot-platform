"""«Принимаю записи» — прокси в каталог (DRF-1845, K1b).

Что заперто:

- GET/PATCH ``/accepting-bookings`` идут в ``/internal/specialists/{id}/availability/``
  каталога с bot-личностью мастера как субъектом; ответ — readback каталога;
- тело: только булево ``accepting_bookings`` — иначе 400 без вызова каталога;
- отказы переводятся честно: 403 → ``not_linked``, 409 → ``profile_not_active``,
  недоступность → 503; ответ каталога без флага — 502, а не «не принимаю»
  (экран не должен нарисовать паузу, которой нет).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import (
    BookingBadRequestError,
    BookingUnavailableError,
)
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_accepting_bookings.return_value = {
        "specialist_id": str(accepted_master.id),
        "accepting_bookings": True,
        "status": "active",
    }
    client.set_accepting_bookings.return_value = {
        "specialist_id": str(accepted_master.id),
        "accepting_bookings": False,
        "status": "active",
    }
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _get(client: Client):
    return client.get(
        reverse("master_api:accepting_bookings"), HTTP_AUTHORIZATION=init_data_header("12345")
    )


def _patch(client: Client, body):
    return client.patch(
        reverse("master_api:accepting_bookings"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )


class TestProxy:
    def test_get_names_the_master_as_subject(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        resp = _get(client)
        assert resp.status_code == 200, resp.content
        kwargs = ayla.get_accepting_bookings.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        assert resp.json() == {"accepting_bookings": True, "status": "active"}

    def test_patch_sends_the_boolean_and_returns_the_readback(self, client: Client, ayla):
        resp = _patch(client, {"accepting_bookings": False})
        assert resp.status_code == 200, resp.content
        assert ayla.set_accepting_bookings.call_args.kwargs["accepting"] is False
        assert resp.json()["accepting_bookings"] is False

    @pytest.mark.parametrize(
        "body", [{"accepting_bookings": "false"}, {"accepting_bookings": 1}, {}]
    )
    def test_a_non_boolean_is_400_and_the_catalog_is_not_called(self, client: Client, ayla, body):
        resp = _patch(client, body)
        assert resp.status_code == 400
        ayla.set_accepting_bookings.assert_not_called()


class TestRefusals:
    def test_403_is_not_linked(self, client: Client, ayla):
        ayla.get_accepting_bookings.side_effect = BookingBadRequestError(
            "http_403", status_code=403, code="PERMISSION_DENIED"
        )
        resp = _get(client)
        assert resp.status_code == 403
        assert resp.json()["error"] == "not_linked"

    def test_409_is_profile_not_active(self, client: Client, ayla):
        ayla.set_accepting_bookings.side_effect = BookingBadRequestError(
            "http_409", status_code=409, code="PROFILE_NOT_ACTIVE"
        )
        resp = _patch(client, {"accepting_bookings": True})
        assert resp.status_code == 409
        assert resp.json()["error"] == "profile_not_active"

    def test_unavailable_is_503(self, client: Client, ayla):
        ayla.get_accepting_bookings.side_effect = BookingUnavailableError("circuit_open")
        resp = _get(client)
        assert resp.status_code == 503

    def test_a_readback_without_the_flag_is_not_a_pause(self, client: Client, ayla):
        ayla.get_accepting_bookings.return_value = {"status": "active"}
        resp = _get(client)
        assert resp.status_code == 502
