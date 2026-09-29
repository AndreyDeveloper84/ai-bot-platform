"""Канон для экрана 03 — прокси в каталог (DRF-1799, M7).

Каталожная половина (beautygo_backend, «Задача: DRF-1799 (половина
каталога)»): ``/internal/services/templates/?direction_id=`` отдаёт всё
поддерево направления одним запросом. Здесь — то, что заперто в боте:

- направления — ровно список каталога, в его порядке; ни числа, ни кодов
  направлений бот не держит (фикстуры с произвольным N, оговорка #454 / G7);
- строки — белым списком: цены и длительности до экрана 03 не доходят;
- ``direction_id`` обязателен и UUID — иначе 400 без вызова каталога;
- отказы по имени: ``not_a_direction``, ``direction_not_found``;
  недоступность — 503; непрочитанный ответ — 502, а не пустой список;
- только GET.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.booking_client import BookingBadRequestError, BookingUnavailableError
from apps.master_api import views
from apps.master_api.tests.conftest import init_data_header

pytestmark = pytest.mark.django_db

DIRECTION = str(uuid.uuid4())


def _directions(n: int) -> list[dict]:
    return [
        {
            "id": str(uuid.uuid4()),
            "name": f"Направление {i}",
            "slug": f"dir-{i}",
            "icon": "",
            "sort_order": i,
            "specialists_count": 7,
        }
        for i in range(n)
    ]


TEMPLATE_ROW = {
    "id": "t1",
    "name": "Аппаратный маникюр",
    "name_short": "Аппаратный",
    "is_popular": True,
    "category_id": "c1",
    "category_name": "Маникюр",
}


@pytest.fixture
def ayla(monkeypatch, accepted_master: CatalogMaster) -> MagicMock:
    client = MagicMock()
    client.get_service_directions.return_value = _directions(3)
    client.get_service_templates.return_value = {
        "region": "default",
        "region_name": "Другой город",
        "templates": [
            dict(
                TEMPLATE_ROW,
                recommended_price_min=1500,
                recommended_price_max=2500,
                duration_default=60,
                duration_min=45,
                duration_max=90,
            )
        ],
    }
    monkeypatch.setattr(views, "get_ayla_booking_client", lambda: client)
    return client


def _get(client: Client, name: str, **params):
    return client.get(
        reverse(f"master_api:{name}"), params, HTTP_AUTHORIZATION=init_data_header("12345")
    )


class TestDirections:
    @pytest.mark.parametrize("n", [1, 3, 17])
    def test_the_list_is_exactly_the_catalogs(self, client: Client, ayla, n):
        rows = _directions(n)
        ayla.get_service_directions.return_value = rows
        resp = _get(client, "service_directions")
        assert resp.status_code == 200, resp.content
        body = resp.json()["directions"]
        assert [d["id"] for d in body] == [r["id"] for r in rows]
        assert set(body[0]) == {"id", "name", "slug", "icon", "sort_order"}

    def test_an_unreadable_answer_is_502_not_empty(self, client: Client, ayla):
        ayla.get_service_directions.return_value = {"oops": True}
        resp = _get(client, "service_directions")
        assert resp.status_code == 502
        assert resp.json()["error"] == "catalog_unavailable"

    def test_unavailable_is_503(self, client: Client, ayla):
        ayla.get_service_directions.side_effect = BookingUnavailableError("down")
        assert _get(client, "service_directions").status_code == 503


class TestTemplates:
    def test_rows_carry_no_price_and_no_minutes(self, client: Client, ayla):
        resp = _get(client, "service_templates", direction_id=DIRECTION)
        assert resp.status_code == 200, resp.content
        assert ayla.get_service_templates.call_args.kwargs == {"direction_id": DIRECTION}
        body = resp.json()
        assert body["direction_id"] == DIRECTION
        assert body["templates"] == [TEMPLATE_ROW]
        raw = resp.content.decode("utf-8")
        assert "price" not in raw and "duration" not in raw

    @pytest.mark.parametrize("params", [{}, {"direction_id": "not-a-uuid"}])
    def test_direction_id_is_required_before_the_catalog(self, client: Client, ayla, params):
        resp = _get(client, "service_templates", **params)
        assert resp.status_code == 400
        assert resp.json()["error"] == "validation_error"
        ayla.get_service_templates.assert_not_called()

    def test_not_a_direction_is_named(self, client: Client, ayla):
        ayla.get_service_templates.side_effect = BookingBadRequestError(
            "http_400", status_code=400, code="NOT_A_DIRECTION"
        )
        resp = _get(client, "service_templates", direction_id=DIRECTION)
        assert resp.status_code == 400
        assert resp.json()["error"] == "not_a_direction"

    def test_unknown_direction_is_named(self, client: Client, ayla):
        ayla.get_service_templates.side_effect = BookingBadRequestError(
            "http_404", status_code=404, code="NOT_FOUND"
        )
        resp = _get(client, "service_templates", direction_id=DIRECTION)
        assert resp.status_code == 404
        assert resp.json()["error"] == "direction_not_found"

    def test_an_unreadable_answer_is_502_not_empty(self, client: Client, ayla):
        ayla.get_service_templates.return_value = {"region": "default"}
        resp = _get(client, "service_templates", direction_id=DIRECTION)
        assert resp.status_code == 502
        assert resp.json()["error"] == "catalog_unavailable"


def test_only_get(client: Client, ayla):
    for name in ("service_directions", "service_templates"):
        resp = client.post(
            reverse(f"master_api:{name}"), HTTP_AUTHORIZATION=init_data_header("12345")
        )
        assert resp.status_code == 405
    ayla.get_service_directions.assert_not_called()
    ayla.get_service_templates.assert_not_called()
