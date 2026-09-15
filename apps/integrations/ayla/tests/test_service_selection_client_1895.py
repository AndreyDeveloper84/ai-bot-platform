"""Клиент выбора услуг и предложения мастера — провод (DRF-1895, M10b).

Маршрут и заголовок субъекта сверяет таблица маршрутов
(``test_contract_route_table``); здесь — ЗНАЧЕНИЯ: путь с
``salon_service_id``, тело, какой статус считается успехом, что 201 первой
цены виден вызывающему (у каталога этот факт есть только в статусе), и что
отказ каталога приходит со своими ``code`` и ``details`` (``reason``,
``count``, ``template_ids``) нетронутыми.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla.booking_client import AylaBookingHTTPClient, BookingBadRequestError

SPEC = "2f1c1b8e-0000-4000-8000-000000001895"
SVC = "2f1c1b8e-0000-4000-8000-0000000018aa"
TPL = "2f1c1b8e-0000-4000-8000-0000000018bb"
ACTOR = "bot:max:1895001"
TOKEN = "test-internal-token-1895"  # noqa: S105  # pragma: allowlist secret

STATE = {
    "specialist_id": SPEC,
    "tenant_id": "2f1c1b8e-0000-4000-8000-0000000018cc",
    "selected": 1,
    "configured": 0,
    "services": [],
}


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


def _err(status: int, code: str, details: dict | None = None) -> httpx.Response:
    error: dict = {"code": code, "message": "x"}
    if details is not None:
        error["details"] = details
    return httpx.Response(status, json={"success": False, "error": error})


def test_selection_is_read_under_the_subject():
    c, seen = _client(lambda r: _ok(200, STATE))
    assert c.get_service_selection(specialist_id=SPEC, external_user_id=ACTOR) == STATE
    (req,) = seen
    assert (req.method, req.url.path) == (
        "GET",
        f"/api/v1/internal/specialists/{SPEC}/services/selection/",
    )
    assert req.headers["X-External-User-ID"] == ACTOR
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("status", [200, 201])
def test_select_posts_template_ids_and_accepts_200_and_201(status):
    created = 1 if status == 201 else 0
    c, seen = _client(lambda r: _ok(status, {**STATE, "created": created}))
    out = c.select_services(specialist_id=SPEC, external_user_id=ACTOR, template_ids=[TPL])
    assert out["created"] == created
    (req,) = seen
    assert (req.method, req.url.path) == (
        "POST",
        f"/api/v1/internal/specialists/{SPEC}/services/selection/",
    )
    assert req.headers["X-External-User-ID"] == ACTOR
    assert json.loads(req.content) == {"template_ids": [TPL]}


@pytest.mark.parametrize(("status", "created"), [(201, True), (200, False)])
def test_offer_put_reports_whether_the_catalog_created_it(status, created):
    c, seen = _client(lambda r: _ok(status, {**STATE, "offer_id": "o1"}))
    out = c.put_service_offer(
        specialist_id=SPEC,
        external_user_id=ACTOR,
        salon_service_id=SVC,
        price="1500",
        duration_minutes=45,
    )
    assert out["offer_id"] == "o1"
    assert out["created"] is created
    (req,) = seen
    assert (req.method, req.url.path) == (
        "PUT",
        f"/api/v1/internal/specialists/{SPEC}/services/{SVC}/offer/",
    )
    assert req.headers["X-External-User-ID"] == ACTOR
    assert json.loads(req.content) == {"price": "1500", "duration_minutes": 45}


def test_remove_deletes_the_selected_service():
    c, seen = _client(lambda r: _ok(200, {**STATE, "removal": "deactivated"}))
    out = c.remove_service(specialist_id=SPEC, external_user_id=ACTOR, salon_service_id=SVC)
    assert out["removal"] == "deactivated"
    (req,) = seen
    assert (req.method, req.url.path) == (
        "DELETE",
        f"/api/v1/internal/specialists/{SPEC}/services/{SVC}/",
    )
    assert req.headers["X-External-User-ID"] == ACTOR


@pytest.mark.parametrize(
    ("status", "code", "details"),
    [
        (409, "HAS_APPOINTMENTS", {"reason": "has_future_appointments", "count": 2}),
        (409, "SERVICE_SELECTION_REFUSED", {"reason": "service_removed"}),
        (404, "NOT_FOUND", {"reason": "service_not_selected"}),
        (404, "SPECIALIST_NOT_FOUND", None),
    ],
)
def test_catalog_refusal_keeps_code_and_details(status, code, details):
    c, _ = _client(lambda r: _err(status, code, details))
    with pytest.raises(BookingBadRequestError) as exc:
        c.remove_service(specialist_id=SPEC, external_user_id=ACTOR, salon_service_id=SVC)
    assert (exc.value.status_code, exc.value.code, exc.value.details) == (status, code, details)


def test_template_not_found_keeps_the_missing_ids():
    details = {"reason": "template_not_found", "template_ids": [TPL]}
    c, _ = _client(lambda r: _err(404, "NOT_FOUND", details))
    with pytest.raises(BookingBadRequestError) as exc:
        c.select_services(specialist_id=SPEC, external_user_id=ACTOR, template_ids=[TPL])
    assert exc.value.details == details
