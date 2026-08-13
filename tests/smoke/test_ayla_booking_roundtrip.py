"""Flag-ON Ayla booking round-trip smoke (#1016 / PR-3).

Drives the *real* :class:`AylaBookingHTTPClient` through a full booking
lifecycle — create → show (me/bookings) → reschedule → cancel — over an
``httpx.MockTransport`` that mimics the locked Ayla wire contract (#1027).

No DB, no skill/LLM stack: this is the wire-level guarantee that the
canonical ``appointment_id`` (UUID) round-trips intact across every
write/read before the production flag flip. The behavioural skill-layer
coverage lives in ``apps/skills/booking/tests/``.
"""

from __future__ import annotations

import json

import httpx

from apps.integrations.ayla import booking_client as bc


_APPT = "3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34"
_SVC = "1a2b3c4d-0000-0000-0000-000000000010"
_SPEC = "7c9e0000-0000-0000-0000-000000000011"
_CLIENT = "9d8c0000-0000-0000-0000-0000000000aa"
_USER = "bot:telegram:42"


def _handler(seen: list[tuple[str, str]]):
    """Route by (method, path) like the merged Ayla internal endpoints."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        seen.append((req.method, path))
        if req.method == "POST" and path == "/api/v1/internal/appointments/":
            return httpx.Response(
                201,
                json={"data": {"id": _APPT, "start_datetime": "2026-07-01T16:00:00+03:00"}},
            )
        if req.method == "GET" and path == "/api/v1/internal/me/bookings/":
            # DRF-1032: the canonical envelope is ``{items, next_cursor}``
            # (``records_api.py:346-349``). The previous ``{upcoming, history}``
            # mock described a shape Ayla never returned, so this smoke test
            # passed while the real round trip would have shown no booking.
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "id": _APPT,
                                "start_datetime": "2026-07-01T16:00:00+03:00",
                                "end_datetime": "2026-07-01T17:00:00+03:00",
                                "service": {"id": _SVC},
                                "specialist": {"id": _SPEC},
                            }
                        ],
                        "next_cursor": None,
                    }
                },
            )
        if path == f"/api/v1/internal/appointments/{_APPT}/reschedule/":
            return httpx.Response(
                200,
                json={"data": {"id": _APPT, "start_datetime": "2026-07-02T18:00:00+03:00"}},
            )
        if path == f"/api/v1/internal/appointments/{_APPT}/cancel/":
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": path}})

    return handler


def _client(seen: list[tuple[str, str]]) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test",
        api_token="smoke-tok",
        transport=httpx.MockTransport(_handler(seen)),
    )


def test_full_booking_lifecycle_roundtrips_uuid() -> None:
    seen: list[tuple[str, str]] = []
    client = _client(seen)

    # 1. create
    created = client.create_appointment(
        external_user_id=_USER,
        client_id=_CLIENT,
        specialist_id=_SPEC,
        service_id=_SVC,
        start_datetime="2026-07-01T16:00:00+03:00",
        idempotency_key="k-create",
    )
    assert created.appointment_id == _APPT

    # 2. show — the booking appears in the user's upcoming list
    bookings = client.get_user_appointments(external_user_id=_USER)
    assert [b.appointment_id for b in bookings] == [_APPT]

    # 3. reschedule — same canonical id is preserved
    moved = client.reschedule_appointment(
        external_user_id=_USER,
        appointment_id=_APPT,
        new_start_datetime="2026-07-02T18:00:00+03:00",
        idempotency_key="k-resched",
    )
    assert moved.appointment_id == _APPT

    # 4. cancel — 204 maps to True
    assert (
        client.cancel_appointment(
            external_user_id=_USER, appointment_id=_APPT, idempotency_key="k-cancel"
        )
        is True
    )

    # All four wire hops happened, in order, against the contract paths.
    assert seen == [
        ("POST", "/api/v1/internal/appointments/"),
        ("GET", "/api/v1/internal/me/bookings/"),
        ("POST", f"/api/v1/internal/appointments/{_APPT}/reschedule/"),
        ("POST", f"/api/v1/internal/appointments/{_APPT}/cancel/"),
    ]

    # Writes carry the verified-client header + idempotency key.
    create_req_method, _ = seen[0]
    assert create_req_method == "POST"


def test_lifecycle_writes_carry_idempotency_and_user_headers() -> None:
    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(201, json={"data": {"id": _APPT}})

    client = bc.AylaBookingHTTPClient(
        base_url="https://ayla.test",
        api_token="smoke-tok",
        transport=httpx.MockTransport(handler),
    )
    client.create_appointment(
        external_user_id=_USER,
        client_id=_CLIENT,
        specialist_id=_SPEC,
        service_id=_SVC,
        start_datetime="2026-07-01T16:00:00+03:00",
        idempotency_key="k-create",
    )
    req = captured[0]
    assert req.headers["Authorization"] == "Bearer smoke-tok"
    assert req.headers["X-External-User-ID"] == _USER
    assert req.headers["X-Idempotency-Key"] == "k-create"
    assert json.loads(req.content)["service_id"] == _SVC
