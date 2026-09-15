"""``create_review`` — a client's review written under their own subject (DRF-1855).

What is locked:

* the wire: ``POST /api/v1/internal/users/{ayla_user_id}/reviews/``, the actor
  in ``X-External-User-ID``, the body the catalog's serializer reads;
* refusals keep their wire code (``REVIEW_EXISTS`` is how a caller knows a
  repeat is already done) and do not trip the breaker;
* a 201 without an id is an outage, not «saved»;
* a rating outside 1..5 — or ``True`` — never reaches the wire.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

USER = "11111111-2222-3333-4444-555555555555"
ACTOR = "bot:max:1855001"


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


def _review(client, **overrides):
    kwargs = {
        "external_user_id": ACTOR,
        "ayla_user_id": USER,
        "appointment_id": "appt-1",
        "rating": 5,
    }
    kwargs.update(overrides)
    return client.create_review(**kwargs)


class TestWire:
    def test_path_actor_and_body(self) -> None:
        wire = _Wire(201, {"data": {"id": "rev-1", "rating": 4}})

        got = _review(_client(wire), rating=4, text="Спасибо!", is_anonymous=True)

        (req,) = wire.seen
        assert req.method == "POST"
        assert req.url.path == f"/api/v1/internal/users/{USER}/reviews/"
        assert req.headers["x-external-user-id"] == ACTOR
        assert json.loads(req.content) == {
            "appointment_id": "appt-1",
            "rating": 4,
            "text": "Спасибо!",
            "is_anonymous": True,
        }
        assert got.id == "rev-1"
        assert got.rating == 4
        assert got.appointment_id == "appt-1"


class TestRefusals:
    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (409, "REVIEW_EXISTS"),
            (400, "APPOINTMENT_NOT_COMPLETED"),
            (404, "NOT_FOUND"),
            (403, "PERMISSION_DENIED"),
        ],
    )
    def test_keep_their_wire_code_and_do_not_trip_the_breaker(self, status, code) -> None:
        wire = _Wire(status, {"error": {"code": code, "message": "x"}})
        client = _client(wire)

        with pytest.raises(bc.BookingBadRequestError) as exc:
            _review(client)

        assert exc.value.status_code == status
        assert exc.value.code == code
        assert client._circuit.failures == []

    def test_5xx_is_an_outage(self) -> None:
        with pytest.raises(bc.BookingUnavailableError):
            _review(_client(_Wire(502, {})))

    def test_a_201_without_an_id_is_not_saved(self) -> None:
        with pytest.raises(bc.BookingUnavailableError):
            _review(_client(_Wire(201, {"data": {"rating": 5}})))


class TestCallerBugsStayOffTheWire:
    @pytest.mark.parametrize("rating", [0, 6, True, "5", 4.5])
    def test_rating_outside_one_to_five(self, rating) -> None:
        wire = _Wire(201, {"data": {"id": "rev-1"}})

        with pytest.raises(ValueError):
            _review(_client(wire), rating=rating)

        assert wire.seen == []

    @pytest.mark.parametrize("rating", [1, 5])
    def test_the_bounds_are_accepted(self, rating) -> None:
        wire = _Wire(201, {"data": {"id": "rev-1"}})
        assert _review(_client(wire), rating=rating).rating == rating
