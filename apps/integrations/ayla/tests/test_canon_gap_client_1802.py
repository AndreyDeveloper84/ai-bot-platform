"""Клиент заявок мастера о разрыве канона — провод (DRF-1802, M10).

Маршрут и заголовок субъекта проверяет таблица маршрутов
(``test_contract_route_table``); здесь — ЗНАЧЕНИЯ, которых таблица
намеренно не смотрит: чей ``X-External-User-ID``, какое тело, какой
``?name=``, и что 404 каталога приходит как отказ с ``status_code=404``,
а не как пустой ответ.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla.booking_client import (
    AylaBookingHTTPClient,
    BookingBadRequestError,
    BookingUnavailableError,
)

SPEC = "2f1c1b8e-0000-4000-8000-000000000001"
REQ = "2f1c1b8e-0000-4000-8000-0000000000aa"
ACTOR = "bot:max:1802001"
TOKEN = "test-internal-token-1802"  # noqa: S105  # pragma: allowlist secret


def _client(handler) -> tuple[AylaBookingHTTPClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return (
        AylaBookingHTTPClient(
            base_url="https://ayla.test", api_token=TOKEN, transport=httpx.MockTransport(record)
        ),
        seen,
    )


def _ok(status: int, data: dict) -> httpx.Response:
    return httpx.Response(status, json={"success": True, "data": data})


def test_create_posts_the_body_under_the_subject_and_expects_201():
    c, seen = _client(
        lambda r: _ok(201, {"request": {"id": REQ, "status": "pending"}, "similar": []})
    )
    out = c.create_canon_gap_request(
        specialist_id=SPEC,
        external_user_id=ACTOR,
        name="Татуаж",
        description="пудра",
        duration_minutes=120,
        price="4500",
    )
    assert out["request"]["id"] == REQ
    (req,) = seen
    assert req.method == "POST"
    assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/canon-gap-requests/"
    assert req.headers["X-External-User-ID"] == ACTOR
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(req.content) == {
        "name": "Татуаж",
        "description": "пудра",
        "duration_minutes": 120,
        "price": "4500",
    }


def test_create_treats_200_as_not_created():
    """201 — единственный успех создания: 200 значило бы «ничего не завели»."""
    c, _ = _client(lambda r: _ok(200, {"request": {"id": REQ}}))
    with pytest.raises(BookingBadRequestError):
        c.create_canon_gap_request(
            specialist_id=SPEC,
            external_user_id=ACTOR,
            name="X",
            description="",
            duration_minutes=60,
            price="1",
        )


def test_similar_passes_name_as_a_query_param_not_in_the_path():
    c, seen = _client(lambda r: _ok(200, {"similar": []}))
    c.similar_canon_templates(specialist_id=SPEC, external_user_id=ACTOR, name="татуаж бровей")
    (req,) = seen
    assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/canon-gap-requests/similar/"
    assert req.url.params["name"] == "татуаж бровей"
    assert req.headers["X-External-User-ID"] == ACTOR


def test_detail_404_is_a_named_refusal_not_an_empty_answer():
    c, seen = _client(
        lambda r: httpx.Response(404, json={"success": False, "error": {"code": "NOT_FOUND"}})
    )
    with pytest.raises(BookingBadRequestError) as exc:
        c.get_canon_gap_request(specialist_id=SPEC, external_user_id=ACTOR, request_id=REQ)
    assert exc.value.status_code == 404
    assert seen[0].url.path == f"/api/v1/internal/specialists/{SPEC}/canon-gap-requests/{REQ}/"


def test_list_403_and_5xx_keep_their_classes():
    c, _ = _client(lambda r: httpx.Response(403, json={"success": False}))
    with pytest.raises(BookingBadRequestError) as exc:
        c.list_canon_gap_requests(specialist_id=SPEC, external_user_id=ACTOR)
    assert exc.value.status_code == 403
    c5, _ = _client(lambda r: httpx.Response(503))
    with pytest.raises(BookingUnavailableError):
        c5.list_canon_gap_requests(specialist_id=SPEC, external_user_id=ACTOR)
