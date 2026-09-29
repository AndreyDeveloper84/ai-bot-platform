"""Место работы соло-мастера — прокси в каталог (DRF-1811, M19; макет 5).

Что сторожится:

* ответы — readback каталога как есть: ``shown_to_clients_after_publication``
  приходит от каталога и истинен ТОЛЬКО при ``CONFIRMED`` (узел листа —
  «бейдж „Будет виден клиентам“ только при CONFIRMED»); бот не дорисовывает;
* два формата = две записи (кабинет + выезд — два POST, узел карты §2.4);
* отказы каталога доезжают по имени (409 ``place_already_set`` и соседи,
  400 по полю, 403 → ``not_linked``), а не «что-то не так»;
* подсказки адреса: строка — в теле и не в логе; 503 каталога
  (геокодер не настроен — постоянное состояние стенда) → 503 с
  ``available: false`` и ПУСТЫМ списком, не 500 и не срабатывание
  выключателя клиента бронирования.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import BookingBadRequestError, BookingUnavailableError
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

PLACE_ID = "3b1a3b5e-0000-4000-8000-000000000001"
AREA_ID = "3b1a3b5e-0000-4000-8000-000000000002"


def _place(
    status: str = "review_required", *, lat: str | None = None, lon: str | None = None
) -> dict:
    return {
        "id": PLACE_ID,
        "kind": "private_studio",
        "label": "Студия у метро",
        "address": "Москва, Тверская, 1",
        "city": "Москва",
        "note_for_client": "Вход со двора",
        "status": status,
        "geocode_status": "pending",
        "latitude": lat,
        "longitude": lon,
        # Каталог считает его сам: только CONFIRMED. Прокси не дорисовывает.
        "shown_to_clients_after_publication": status == "confirmed",
    }


def _area(coverage: str = "later") -> dict:
    return {
        "id": AREA_ID,
        "kind": "mobile",
        "city": "Москва",
        "coverage": coverage,
        "configured": coverage == "whole_city",
    }


def _state(specialist_id: str, places=None, areas=None) -> dict:
    return {
        "specialist_id": specialist_id,
        "city": "Москва",
        "places": places or [],
        "areas": areas or [],
    }


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_service_locations.return_value = _state(str(accepted_master.id))
    client.create_service_location.return_value = _state(str(accepted_master.id), [_place()])
    client.patch_service_location.return_value = _state(str(accepted_master.id), [_place()])
    client.suggest_address.return_value = {
        "available": True,
        "reason": None,
        "city": "Москва",
        "suggestions": [
            {"value": "Тверская ул, 1", "unrestricted_value": "г Москва, Тверская ул, д 1"}
        ],
    }
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _auth() -> dict:
    return {"HTTP_AUTHORIZATION": init_data_header("12345")}


def _get(client: Client):
    return client.get(reverse("master_api:service_locations"), **_auth())


def _post(client: Client, body: dict):
    return client.post(
        reverse("master_api:service_locations"),
        data=json.dumps(body),
        content_type="application/json",
        **_auth(),
    )


def _patch(client: Client, item_id: str, body: dict):
    return client.patch(
        reverse("master_api:service_location_detail", kwargs={"item_id": item_id}),
        data=json.dumps(body),
        content_type="application/json",
        **_auth(),
    )


def _suggest(client: Client, q: str):
    return client.post(
        reverse("master_api:address_suggest"),
        data=json.dumps({"q": q}),
        content_type="application/json",
        **_auth(),
    )


class TestReadback:
    def test_get_names_the_master_as_subject_and_returns_the_catalog_state(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        ayla.get_service_locations.return_value = _state(
            str(accepted_master.id), [_place("confirmed", lat="55.76", lon="37.61")], [_area()]
        )
        resp = _get(client)
        assert resp.status_code == 200, resp.content
        kwargs = ayla.get_service_locations.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        data = resp.json()
        assert data["city"] == "Москва"
        assert data["places"][0]["latitude"] == "55.76"
        assert data["places"][0]["shown_to_clients_after_publication"] is True
        assert data["areas"][0]["coverage"] == "later"

    def test_shown_to_clients_is_the_catalogs_word_and_false_unless_confirmed(
        self, client: Client, accepted_master, ayla
    ):
        """Узел листа: «Будет виден клиентам» — только при CONFIRMED.

        ПРИСУТСТВИЕ впереди (узел выше: CONFIRMED → True); здесь та же
        строка под REVIEW_REQUIRED — False и координаты null: экран не
        должен ни рисовать бейдж, ни превью карты.
        """
        ayla.get_service_locations.return_value = _state(
            str(accepted_master.id), [_place("review_required")]
        )
        data = _get(client).json()
        place = data["places"][0]
        assert place["status"] == "review_required"
        assert place["shown_to_clients_after_publication"] is False
        assert place["latitude"] is None and place["longitude"] is None


class TestWrite:
    def test_two_formats_are_two_rows_place_and_area(self, client: Client, accepted_master, ayla):
        """Карта §2.4: «два формата = две строки» — кабинет и выезд идут двумя POST."""
        ayla.create_service_location.side_effect = [
            _state(str(accepted_master.id), [_place()]),
            _state(str(accepted_master.id), [_place()], [_area("whole_city")]),
        ]
        first = _post(client, {"kind": "private_studio", "address": "Москва, Тверская, 1"})
        assert first.status_code == 201, first.content
        second = _post(client, {"kind": "mobile", "coverage": "whole_city"})
        assert second.status_code == 201, second.content

        calls = [c.kwargs["fields"] for c in ayla.create_service_location.call_args_list]
        assert calls == [
            {"kind": "private_studio", "address": "Москва, Тверская, 1"},
            {"kind": "mobile", "coverage": "whole_city"},
        ]
        # Ответ — readback после ВТОРОЙ записи: и место, и зона.
        data = second.json()
        assert len(data["places"]) == 1 and len(data["areas"]) == 1
        assert data["areas"][0]["configured"] is True

    def test_patch_forwards_fields_to_the_own_item(self, client: Client, accepted_master, ayla):
        resp = _patch(client, PLACE_ID, {"note_for_client": "Второй этаж"})
        assert resp.status_code == 200, resp.content
        kwargs = ayla.patch_service_location.call_args.kwargs
        assert kwargs["item_id"] == PLACE_ID
        assert kwargs["fields"] == {"note_for_client": "Второй этаж"}

    def test_body_is_forwarded_as_is_so_the_catalog_names_the_bad_field(
        self, client: Client, accepted_master, ayla
    ):
        """Бот не фильтрует ввод: ``tenant_id`` уходит в каталог, и ОН отвечает 400 по имени."""
        ayla.create_service_location.side_effect = BookingBadRequestError(
            "http_400_validation_error",
            status_code=400,
            code="VALIDATION_ERROR",
            details={"field": "tenant_id"},
        )
        resp = _post(client, {"kind": "private_studio", "address": "x", "tenant_id": "other"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"
        assert "tenant_id" in resp.json()["detail"]


class TestRefusalsByName:
    @pytest.mark.parametrize(
        "code",
        [
            "salon_place_owner_managed",
            "no_workspace_tenant",
            "place_already_set",
            "place_outside_workspace",
            "area_already_set",
        ],
    )
    def test_409_from_the_catalog_keeps_its_name(self, client: Client, accepted_master, ayla, code):
        ayla.create_service_location.side_effect = BookingBadRequestError(
            f"http_409_{code}", status_code=409, code=code
        )
        resp = _post(client, {"kind": "private_studio", "address": "x"})
        assert resp.status_code == 409
        assert resp.json()["error"] == code

    def test_unlinked_master_gets_not_linked_not_a_500(self, client: Client, accepted_master, ayla):
        ayla.get_service_locations.side_effect = BookingBadRequestError("http_403", status_code=403)
        resp = _get(client)
        assert resp.status_code == 403
        assert resp.json()["error"] == "not_linked"

    def test_foreign_item_is_404(self, client: Client, accepted_master, ayla):
        ayla.patch_service_location.side_effect = BookingBadRequestError(
            "http_404", status_code=404
        )
        resp = _patch(client, AREA_ID, {"coverage": "later"})
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"

    def test_catalog_down_is_503_by_name(self, client: Client, accepted_master, ayla):
        ayla.get_service_locations.side_effect = BookingUnavailableError("http_502")
        resp = _get(client)
        assert resp.status_code == 503
        assert resp.json()["error"] == "locations_unavailable"


class TestAddressSuggest:
    def test_suggestions_come_back_when_the_geocoder_answers(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        resp = _suggest(client, "Тверская 1")
        assert resp.status_code == 200, resp.content
        kwargs = ayla.suggest_address.call_args.kwargs
        assert kwargs["q"] == "Тверская 1"
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        data = resp.json()
        assert data["available"] is True
        assert data["suggestions"][0]["value"] == "Тверская ул, 1"

    def test_unconfigured_geocoder_is_503_with_an_empty_list_not_a_500(
        self, client: Client, accepted_master, ayla, caplog
    ):
        """Стенд пилота: ключ геокодера пуст → каталог 503 misconfigured.

        Экран обязан упасть в ручной ввод: 503, ``available: false``, список
        пуст. И адрес мастера не попадает в лог бота — ни по ошибке, ни по
        успеху (ПРИСУТСТВИЕ: лог не пуст, причина в нём есть).
        """
        ayla.suggest_address.return_value = {
            "available": False,
            "reason": "misconfigured",
            "suggestions": [],
        }
        with caplog.at_level(logging.INFO):
            resp = _suggest(client, "Тверская 1, кв 7")
        assert resp.status_code == 503
        assert resp.json() == {"available": False, "reason": "misconfigured", "suggestions": []}
        assert "Тверская" not in caplog.text
        assert "кв 7" not in caplog.text

    def test_no_city_is_409_by_name(self, client: Client, accepted_master, ayla):
        ayla.suggest_address.return_value = {
            "available": False,
            "reason": "no_city",
            "suggestions": [],
        }
        resp = _suggest(client, "Тверская 1")
        assert resp.status_code == 409
        assert resp.json()["reason"] == "no_city"

    def test_empty_query_is_400_without_a_catalog_call(self, client: Client, accepted_master, ayla):
        resp = _suggest(client, "   ")
        assert resp.status_code == 400
        ayla.suggest_address.assert_not_called()


class TestReadinessLink:
    def test_location_item_leads_to_screen_05(self):
        """Пункт готовности «Место работы» ведёт на экран 05, не в настройки.

        Литералом, не константой модуля — сверка с константой сторожила бы
        модуль сам с собой.
        """
        from apps.master_api.services.onboarding_readiness import DEEP_LINKS

        assert DEEP_LINKS["location"] == "/solo/place"
        assert DEEP_LINKS["location"] != "/solo/settings"
