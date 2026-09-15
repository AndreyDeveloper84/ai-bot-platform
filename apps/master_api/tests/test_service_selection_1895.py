"""Выбор канонических услуг и цена мастера — прокси в каталог (DRF-1895, M10b).

Каталожная половина — beautygo_backend #443 (M8a) и #444 (M8b):
``/internal/specialists/{id}/services/selection/``, ``…/services/{salon_service_id}/offer/``,
``…/services/{salon_service_id}/`` под субъектом. Здесь — то, что заперто в боте:

- мастер читает и меняет СВОЙ выбор через ``/api/v1/master/services/…``;
  субъект — его bot-личность, профиль — его ``CatalogMaster.id``;
- ответ — состояние выбора, как его прислал каталог: счётчики ``selected`` /
  ``configured`` бот не пересчитывает (даже если они «не сходятся»);
- 201/200 — от каталога: ``created`` у выбора, первая цена у предложения;
- тело вне пределов каталога → 400 без вызова каталога;
- каждый отказ каталога — по имени, с его ``reason`` / ``count`` / ``template_ids``;
- недоступность каталога → 503 ``catalog_unavailable``.
"""

from __future__ import annotations

import json
import uuid
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

SVC = str(uuid.uuid4())
TPL = str(uuid.uuid4())

#: Нарочно «не сходится»: 7 выбранных при пустом списке. Бот обязан отдать
#: это как есть — пересчёт на стороне бота был бы вторым источником счётчика.
STATE = {
    "specialist_id": "spec",
    "tenant_id": "tenant",
    "selected": 7,
    "configured": 3,
    "services": [],
}


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_service_selection.return_value = dict(STATE)
    client.select_services.return_value = {**STATE, "created": 1}
    client.put_service_offer.return_value = {**STATE, "offer_id": "o1", "created": True}
    client.remove_service.return_value = {**STATE, "removal": "deleted"}
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _auth() -> dict:
    return {"HTTP_AUTHORIZATION": init_data_header("12345")}


def _json(client: Client, method: str, url: str, body) -> object:
    return getattr(client, method)(
        url, data=json.dumps(body), content_type="application/json", **_auth()
    )


class TestSelection:
    def test_get_passes_the_catalog_state_through_untouched(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        resp = client.get(reverse("master_api:service_selection"), **_auth())
        assert resp.status_code == 200, resp.content
        assert resp.json() == STATE
        kwargs = ayla.get_service_selection.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))

    @pytest.mark.parametrize(("created", "http"), [(2, 201), (0, 200)])
    def test_post_status_follows_the_catalogs_created(
        self, client: Client, accepted_master, ayla, created, http
    ):
        ayla.select_services.return_value = {**STATE, "created": created}
        resp = _json(
            client, "post", reverse("master_api:service_selection"), {"template_ids": [TPL]}
        )
        assert resp.status_code == http, resp.content
        assert resp.json()["created"] == created
        assert ayla.select_services.call_args.kwargs["template_ids"] == [TPL]
        assert ayla.select_services.call_args.kwargs["specialist_id"] == str(accepted_master.id)

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"template_ids": []},
            {"template_ids": ["not-a-uuid"]},
            {"template_ids": TPL},
            {"template_ids": [str(uuid.uuid4()) for _ in range(201)]},
            ["not", "an", "object"],
        ],
    )
    def test_invalid_selection_body_is_400_and_the_catalog_is_not_called(
        self, client: Client, accepted_master, ayla, bad
    ):
        resp = _json(client, "post", reverse("master_api:service_selection"), bad)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"
        ayla.select_services.assert_not_called()


class TestOffer:
    @pytest.mark.parametrize(("created", "http"), [(True, 201), (False, 200)])
    def test_put_status_follows_the_first_price(
        self, client: Client, accepted_master, ayla, created, http
    ):
        ayla.put_service_offer.return_value = {**STATE, "offer_id": "o1", "created": created}
        resp = _json(
            client,
            "put",
            reverse("master_api:service_offer", args=[SVC]),
            {"price": "1500", "duration_minutes": 45},
        )
        assert resp.status_code == http, resp.content
        data = resp.json()
        assert data["offer_id"] == "o1"
        assert data["selected"] == 7  # state as the catalog sent it
        kwargs = ayla.put_service_offer.call_args.kwargs
        assert (kwargs["salon_service_id"], kwargs["price"], kwargs["duration_minutes"]) == (
            SVC,
            "1500",
            45,
        )
        assert kwargs["specialist_id"] == str(accepted_master.id)

    @pytest.mark.parametrize(
        "bad",
        [
            {"duration_minutes": 45},
            {"price": "1500"},
            {"price": "0.99", "duration_minutes": 45},
            {"price": "abc", "duration_minutes": 45},
            {"price": "1500.555", "duration_minutes": 45},
            {"price": "1500", "duration_minutes": 4},
            {"price": "1500", "duration_minutes": 481},
            {"price": "1500", "duration_minutes": "45"},
            {"price": "1500", "duration_minutes": True},
        ],
    )
    def test_invalid_offer_body_is_400_and_the_catalog_is_not_called(
        self, client: Client, accepted_master, ayla, bad
    ):
        resp = _json(client, "put", reverse("master_api:service_offer", args=[SVC]), bad)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"
        ayla.put_service_offer.assert_not_called()


class TestRemove:
    def test_delete_returns_the_state_and_the_removal(self, client: Client, accepted_master, ayla):
        resp = client.delete(reverse("master_api:selected_service", args=[SVC]), **_auth())
        assert resp.status_code == 200, resp.content
        assert resp.json()["removal"] == "deleted"
        assert ayla.remove_service.call_args.kwargs["salon_service_id"] == SVC


class TestRefusalsByName:
    @pytest.mark.parametrize(
        ("status", "code", "details", "http", "slug", "extra"),
        [
            (403, None, None, 403, "not_linked", {}),
            (404, "SPECIALIST_NOT_FOUND", None, 404, "specialist_not_found", {}),
            (
                404,
                "NOT_FOUND",
                {"reason": "service_not_selected"},
                404,
                "service_not_selected",
                {},
            ),
            (
                409,
                "SERVICE_SELECTION_REFUSED",
                {"reason": "salon_catalog_owner_managed"},
                409,
                "salon_catalog_owner_managed",
                {"reason": "salon_catalog_owner_managed"},
            ),
            (
                409,
                "SERVICE_SELECTION_REFUSED",
                {"reason": "no_workspace_tenant"},
                409,
                "no_workspace_tenant",
                {"reason": "no_workspace_tenant"},
            ),
            (
                409,
                "SERVICE_SELECTION_REFUSED",
                {"reason": "service_removed"},
                409,
                "service_removed",
                {"reason": "service_removed"},
            ),
            (
                409,
                "HAS_APPOINTMENTS",
                {"reason": "has_future_appointments", "count": 2},
                409,
                "has_future_appointments",
                {"count": 2},
            ),
            (400, "VALIDATION_ERROR", {"price": ["bad"]}, 400, "validation_error", {}),
        ],
    )
    def test_catalog_refusal_is_named_with_its_reason(
        self, client: Client, accepted_master, ayla, status, code, details, http, slug, extra
    ):
        ayla.remove_service.side_effect = BookingBadRequestError(
            "x", status_code=status, code=code, details=details
        )
        resp = client.delete(reverse("master_api:selected_service", args=[SVC]), **_auth())
        assert resp.status_code == http
        data = resp.json()
        assert data["error"] == slug
        # Данные отказа — под ``details``, как их читает ApiError Mini App (DRF-1708).
        for key, value in extra.items():
            assert data["details"][key] == value

    def test_template_not_found_forwards_the_missing_ids(
        self, client: Client, accepted_master, ayla
    ):
        ayla.select_services.side_effect = BookingBadRequestError(
            "x",
            status_code=404,
            code="NOT_FOUND",
            details={"reason": "template_not_found", "template_ids": [TPL]},
        )
        resp = _json(
            client, "post", reverse("master_api:service_selection"), {"template_ids": [TPL]}
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "template_not_found"
        assert resp.json()["details"]["template_ids"] == [TPL]

    def test_unavailable_is_503(self, client: Client, accepted_master, ayla):
        ayla.get_service_selection.side_effect = BookingUnavailableError("circuit_open")
        resp = client.get(reverse("master_api:service_selection"), **_auth())
        assert resp.status_code == 503
        assert resp.json()["error"] == "catalog_unavailable"

    def test_wrong_methods_are_405(self, client: Client, accepted_master, ayla):
        assert client.patch(reverse("master_api:service_selection"), **_auth()).status_code == 405
        assert (
            client.get(reverse("master_api:service_offer", args=[SVC]), **_auth()).status_code
            == 405
        )
        assert (
            client.post(reverse("master_api:selected_service", args=[SVC]), **_auth()).status_code
            == 405
        )
