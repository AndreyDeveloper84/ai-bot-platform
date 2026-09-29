"""Клиент каталога — «Мои отзывы» под субъектом мастера (DRF-1857, K14).

Провод: ``GET internal/specialists/{id}/reviews/`` с Bearer и
``X-External-User-ID`` СУБЪЕКТА; ответ разворачивается из конверта; 403
каталога (не субъект) → ``BookingBadRequestError(status_code=403)``.
"""

from __future__ import annotations

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

SPEC = "11111111-1111-1111-1111-111111111857"
ACTOR = "bot:max:1857"


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


def test_get_names_the_subject_and_unwraps() -> None:
    wire = _Wire(
        200,
        {"data": {"specialist_id": SPEC, "review_count": 0, "rating": None, "reviews": []}},
    )

    data = _client(wire).get_specialist_reviews(specialist_id=SPEC, external_user_id=ACTOR)

    (req,) = wire.seen
    assert req.method == "GET"
    assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/reviews/"
    assert req.headers["X-External-User-ID"] == ACTOR
    assert data["review_count"] == 0
    assert data["reviews"] == []


def test_a_foreign_profile_is_a_bad_request_with_its_status() -> None:
    wire = _Wire(403, {"error": {"code": "PERMISSION_DENIED", "message": "…"}})
    with pytest.raises(bc.BookingBadRequestError) as exc:
        _client(wire).get_specialist_reviews(specialist_id=SPEC, external_user_id=ACTOR)
    assert exc.value.status_code == 403
