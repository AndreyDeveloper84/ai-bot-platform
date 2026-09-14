"""Клиент каталога — «Принимаю записи» под субъектом мастера (DRF-1845, K1b).

Провод: ``GET/PATCH internal/specialists/{id}/availability/`` с Bearer и
``X-External-User-ID`` СУБЪЕКТА; тело PATCH — ``{accepting_bookings: bool}``;
ответ разворачивается из конверта; 409 каталога (неопубликованный профиль) →
``BookingBadRequestError(status_code=409, code=PROFILE_NOT_ACTIVE)``; 403 —
не субъект; небулево значение до провода не доходит.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

SPEC = "11111111-1111-1111-1111-111111111845"
ACTOR = "bot:max:1845"


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


class TestWire:
    def test_get_names_the_subject_and_unwraps(self) -> None:
        wire = _Wire(
            200, {"data": {"specialist_id": SPEC, "accepting_bookings": True, "status": "active"}}
        )

        data = _client(wire).get_accepting_bookings(specialist_id=SPEC, external_user_id=ACTOR)

        (req,) = wire.seen
        assert req.method == "GET"
        assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/availability/"
        assert req.headers["X-External-User-ID"] == ACTOR
        assert data["accepting_bookings"] is True

    @pytest.mark.parametrize("accepting", [True, False])
    def test_patch_sends_a_boolean(self, accepting) -> None:
        wire = _Wire(200, {"data": {"accepting_bookings": accepting, "status": "active"}})

        _client(wire).set_accepting_bookings(
            specialist_id=SPEC, external_user_id=ACTOR, accepting=accepting
        )

        (req,) = wire.seen
        assert req.method == "PATCH"
        assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/availability/"
        assert req.headers["X-External-User-ID"] == ACTOR
        assert json.loads(req.content) == {"accepting_bookings": accepting}


class TestRefusals:
    def test_an_unpublished_profile_keeps_its_code(self) -> None:
        wire = _Wire(409, {"error": {"code": "PROFILE_NOT_ACTIVE", "message": "…"}})
        with pytest.raises(bc.BookingBadRequestError) as exc:
            _client(wire).set_accepting_bookings(
                specialist_id=SPEC, external_user_id=ACTOR, accepting=False
            )
        assert exc.value.status_code == 409
        assert exc.value.code == "PROFILE_NOT_ACTIVE"

    def test_not_the_subject_is_403(self) -> None:
        wire = _Wire(403, {"error": {"code": "PERMISSION_DENIED", "message": "…"}})
        with pytest.raises(bc.BookingBadRequestError) as exc:
            _client(wire).get_accepting_bookings(specialist_id=SPEC, external_user_id=ACTOR)
        assert exc.value.status_code == 403

    @pytest.mark.parametrize("value", ["false", 0, None])
    def test_a_non_boolean_never_reaches_the_wire(self, value) -> None:
        wire = _Wire(200, {"data": {"accepting_bookings": False}})
        with pytest.raises(ValueError):
            _client(wire).set_accepting_bookings(
                specialist_id=SPEC, external_user_id=ACTOR, accepting=value
            )
        assert wire.seen == []
