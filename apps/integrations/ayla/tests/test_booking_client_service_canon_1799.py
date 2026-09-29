"""Клиент каталога — канон для экрана 03 (DRF-1799, M7).

Провод: ``GET internal/services/directions/`` и
``GET internal/services/templates/?direction_id=`` под общим Bearer БЕЗ
``X-External-User-ID`` (канон не принадлежит мастеру); ответ разворачивается
из конверта; ``NOT_A_DIRECTION`` каталога — ``BookingBadRequestError`` со
своим кодом.
"""

from __future__ import annotations

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

DIRECTION = "11111111-1111-1111-1111-111111111799"


class _Wire:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self.payload = payload
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(self.status, json=self.payload)


def _client(wire: _Wire) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test",
        api_token="secret-tok",  # noqa: S106  # pragma: allowlist secret
        transport=httpx.MockTransport(wire),
    )


def test_directions_are_read_without_a_subject_and_unwrapped() -> None:
    rows = [{"id": "d1", "name": "Ногти"}, {"id": "d2", "name": "Брови"}]
    wire = _Wire(200, {"data": rows})

    data = _client(wire).get_service_directions()

    (req,) = wire.seen
    assert req.method == "GET"
    assert req.url.path == "/api/v1/internal/services/directions/"
    assert req.headers["Authorization"] == "Bearer secret-tok"
    assert "X-External-User-ID" not in req.headers
    assert data == rows


def test_templates_are_asked_by_direction() -> None:
    wire = _Wire(200, {"data": {"region": "default", "region_name": "", "templates": []}})

    data = _client(wire).get_service_templates(direction_id=DIRECTION)

    (req,) = wire.seen
    assert req.url.path == "/api/v1/internal/services/templates/"
    assert req.url.params["direction_id"] == DIRECTION
    assert "category_id" not in req.url.params
    assert "X-External-User-ID" not in req.headers
    assert data["templates"] == []


def test_not_a_direction_keeps_its_code() -> None:
    wire = _Wire(400, {"error": {"code": "NOT_A_DIRECTION", "message": "…"}})
    with pytest.raises(bc.BookingBadRequestError) as exc:
        _client(wire).get_service_templates(direction_id=DIRECTION)
    assert exc.value.status_code == 400
    assert exc.value.code == "NOT_A_DIRECTION"
