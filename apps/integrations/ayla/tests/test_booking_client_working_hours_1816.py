"""Клиент каталога — часы мастера под субъектом (DRF-1816, M24).

Провод: ``GET/PUT internal/specialists/{id}/working-hours/`` с Bearer и
``X-External-User-ID`` СУБЪЕКТА (не администратора); тело PUT — ``{schedule}``;
ответ разворачивается из конверта ``{"data": …}``; 409 каталога →
``ScheduleBlockConflictError`` (тот же класс, что у time-off), 403 →
``BookingBadRequestError(status_code=403)``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

SPEC = "11111111-1111-1111-1111-111111111111"
ACTOR = "bot:max:777"
WEEK = [{"day_of_week": d, "is_working_day": False} for d in range(7)]


def _client_with(handler) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="secret-tok", transport=httpx.MockTransport(handler)
    )


class TestWorkingHoursWire:
    def test_get_names_the_subject_and_unwraps(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(
                200,
                json={
                    "data": {"specialist_id": SPEC, "timezone": "Europe/Moscow", "schedule": WEEK}
                },
            )

        data = _client_with(handler).get_working_hours(specialist_id=SPEC, external_user_id=ACTOR)
        req = seen[0]
        assert req.method == "GET"
        assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/working-hours/"
        assert req.headers["Authorization"] == "Bearer secret-tok"
        assert req.headers["X-External-User-ID"] == ACTOR
        assert data["timezone"] == "Europe/Moscow"
        assert len(data["schedule"]) == 7

    def test_put_sends_schedule_in_the_body(self) -> None:
        seen: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            seen.append(req)
            return httpx.Response(
                200, json={"data": {"specialist_id": SPEC, "timezone": "UTC", "schedule": WEEK}}
            )

        _client_with(handler).put_working_hours(
            specialist_id=SPEC, external_user_id=ACTOR, schedule=WEEK
        )
        req = seen[0]
        assert req.method == "PUT"
        assert req.url.path == f"/api/v1/internal/specialists/{SPEC}/working-hours/"
        assert req.headers["X-External-User-ID"] == ACTOR
        assert json.loads(req.content) == {"schedule": WEEK}

    def test_409_is_the_shrink_conflict(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409, json={"error": {"code": "HAS_ACTIVE_APPOINTMENTS", "message": "…"}}
            )

        with pytest.raises(bc.ScheduleBlockConflictError):
            _client_with(handler).put_working_hours(
                specialist_id=SPEC, external_user_id=ACTOR, schedule=WEEK
            )

    def test_403_is_a_bad_request_with_its_status(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": "acting subject has no such profile"})

        with pytest.raises(bc.BookingBadRequestError) as exc:
            _client_with(handler).get_working_hours(specialist_id=SPEC, external_user_id=ACTOR)
        assert exc.value.status_code == 403
