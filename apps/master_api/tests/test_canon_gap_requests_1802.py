"""«Своя услуга» мастера — прокси заявки о разрыве канона в каталог (DRF-1802, M10).

Каталожная половина — beautygo_backend#437 (M9): ``/internal/specialists/{id}/
canon-gap-requests/`` под субъектом; решает только владелец в Django-admin.
Здесь — то, что заперто в боте:

- мастер заводит и читает свои заявки через ``/api/v1/master/canon-gap-requests``;
  субъект — его bot-личность (``X-External-User-ID``), профиль — его
  ``CatalogMaster.id``; второго хранилища заявок в боте нет, ответ — то, что
  вернул каталог;
- подсказка «похожая услуга» — только чтение, без связи;
- решить заявку из бота нельзя: у прокси нет PATCH/PUT/DELETE;
- тело без названия / длительности / цены → 400 без вызова каталога;
- отказы каталога по имени: 403 → ``not_linked``, 404 → ``not_found``
  (чужая заявка неотличима от несуществующей), 400 → ``validation_error``,
  недоступность → 503 ``catalog_unavailable``.
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

REQ_ID = str(uuid.uuid4())


def _catalog_request(**over) -> dict:
    row = {
        "id": REQ_ID,
        "name": "Татуаж бровей пудровый",
        "description": "",
        "duration_minutes": 120,
        "price": "4500.00",
        "status": "pending",
        "status_label": "На проверке",
        "resolved_template_id": None,
        "clarification_question": None,
        "rejection_reason": None,
        "decided_at": None,
        "created_at": "2026-09-15T10:00:00+00:00",
    }
    row.update(over)
    return row


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.list_canon_gap_requests.return_value = {"requests": [_catalog_request()]}
    client.create_canon_gap_request.return_value = {
        "request": _catalog_request(),
        "similar": [
            {"template_id": "t1", "name": "Перманентный макияж бровей", "matched_by": "synonym"}
        ],
    }
    client.get_canon_gap_request.return_value = {"request": _catalog_request()}
    client.similar_canon_templates.return_value = {"similar": []}
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _auth() -> dict:
    return {"HTTP_AUTHORIZATION": init_data_header("12345")}


BODY = {
    "name": "Татуаж бровей пудровый",
    "description": "",
    "duration_minutes": 120,
    "price": "4500",
}


class TestProxy:
    def test_post_names_the_master_as_subject_and_returns_the_catalog_answer(
        self, client: Client, accepted_master, bot_user: BotUser, ayla
    ):
        resp = client.post(
            reverse("master_api:canon_gap_requests"),
            data=json.dumps(BODY),
            content_type="application/json",
            **_auth(),
        )
        assert resp.status_code == 201, resp.content
        kwargs = ayla.create_canon_gap_request.call_args.kwargs
        assert kwargs["specialist_id"] == str(accepted_master.id)
        assert kwargs["external_user_id"].endswith(str(bot_user.channel_user_id))
        assert (kwargs["name"], kwargs["duration_minutes"], kwargs["price"]) == (
            BODY["name"],
            120,
            "4500",
        )
        data = resp.json()
        assert data["request"]["status_label"] == "На проверке"
        # Подсказка приходит как есть — и ничего не связывает.
        assert data["similar"][0]["matched_by"] == "synonym"

    def test_list_and_detail_are_the_masters_own(self, client: Client, accepted_master, ayla):
        listed = client.get(reverse("master_api:canon_gap_requests"), **_auth())
        assert listed.status_code == 200, listed.content
        assert [r["id"] for r in listed.json()["requests"]] == [REQ_ID]
        detail = client.get(
            reverse("master_api:canon_gap_request_detail", args=[REQ_ID]), **_auth()
        )
        assert detail.status_code == 200, detail.content
        assert ayla.get_canon_gap_request.call_args.kwargs["request_id"] == REQ_ID
        assert ayla.get_canon_gap_request.call_args.kwargs["specialist_id"] == str(
            accepted_master.id
        )

    def test_similar_reads_only(self, client: Client, accepted_master, ayla):
        resp = client.get(reverse("master_api:canon_gap_similar"), {"name": "татуаж"}, **_auth())
        assert resp.status_code == 200, resp.content
        assert ayla.similar_canon_templates.call_args.kwargs["name"] == "татуаж"
        assert client.get(reverse("master_api:canon_gap_similar"), **_auth()).status_code == 400

    @pytest.mark.parametrize(
        "bad",
        [
            {**BODY, "name": "  "},
            {**BODY, "duration_minutes": 0},
            {k: v for k, v in BODY.items() if k != "price"},
            ["not", "an", "object"],
        ],
    )
    def test_invalid_body_is_400_and_the_catalog_is_not_called(
        self, client: Client, accepted_master, ayla, bad
    ):
        resp = client.post(
            reverse("master_api:canon_gap_requests"),
            data=json.dumps(bad),
            content_type="application/json",
            **_auth(),
        )
        assert resp.status_code == 400
        ayla.create_canon_gap_request.assert_not_called()

    def test_the_bot_cannot_decide(self, client: Client, accepted_master, ayla):
        for method in ("patch", "put", "delete"):
            url = reverse("master_api:canon_gap_request_detail", args=[REQ_ID])
            call = getattr(client, method)
            assert (
                call(
                    url,
                    data=json.dumps({"status": "approved"}),
                    content_type="application/json",
                    **_auth(),
                ).status_code
                == 405
            )
        assert client.put(reverse("master_api:canon_gap_requests"), **_auth()).status_code == 405


class TestRefusalsByName:
    @pytest.mark.parametrize(
        ("status", "slug", "http"),
        [
            (403, "not_linked", 403),
            (404, "not_found", 404),
            (400, "validation_error", 400),
        ],
    )
    def test_catalog_refusal_is_named(
        self, client: Client, accepted_master, ayla, status, slug, http
    ):
        ayla.get_canon_gap_request.side_effect = BookingBadRequestError("x", status_code=status)
        resp = client.get(reverse("master_api:canon_gap_request_detail", args=[REQ_ID]), **_auth())
        assert resp.status_code == http
        assert resp.json()["error"] == slug

    def test_unavailable_is_503(self, client: Client, accepted_master, ayla):
        ayla.list_canon_gap_requests.side_effect = BookingUnavailableError("circuit_open")
        resp = client.get(reverse("master_api:canon_gap_requests"), **_auth())
        assert resp.status_code == 503
        assert resp.json()["error"] == "catalog_unavailable"
