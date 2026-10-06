"""DRF-2785 — the bot's client for the master's own appointment actions.

The wire is the contract ayla-00 sent with catalog PR #648 (05.10):
``POST internal/specialists/{sid}/appointments/{aid}/{action}/``, the master
as ``X-External-User-ID``, ``{"data": {...}}`` back. Driven through
``httpx.MockTransport`` like the rest of this client's round-trip nodes.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc

SID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb2785"
AID = "a8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f62785"
MASTER = "bot:max:700785"


def _answer(**over) -> dict:
    data = {
        "appointment_id": AID,
        "specialist_id": SID,
        "status": "confirmed",
        "version": 2,
        "start_at": "2026-10-06T15:00:00+03:00",
        "end_at": "2026-10-06T16:00:00+03:00",
        "master_acknowledged_at": "2026-10-05T12:00:00+00:00",
        "master_acknowledged_version": 2,
        "acknowledged": True,
    }
    data.update(over)
    return {"data": data}


def _client(handler) -> bc.AylaBookingHTTPClient:
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test",
        api_token="secret-tok",  # pragma: allowlist secret
        transport=httpx.MockTransport(handler),
    )


def _recording(status: int = 200, body: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, json=body if body is not None else _answer(recorded=True))

    return seen, handler


class TestWire:
    def test_s1_acknowledge_posts_the_version_as_the_master(self) -> None:
        seen, handler = _recording()

        out = _client(handler).act_as_specialist(
            external_user_id=MASTER,
            specialist_id=SID,
            appointment_id=AID,
            action="acknowledge",
            expected_version=2,
        )

        (req,) = seen
        assert req.method == "POST"
        assert req.url.path == (
            f"/api/v1/internal/specialists/{SID}/appointments/{AID}/acknowledge/"
        )
        assert req.headers["X-External-User-ID"] == MASTER
        assert json.loads(req.content) == {"expected_version": 2}
        assert out == bc.AylaSpecialistAppointment(
            appointment_id=AID,
            specialist_id=SID,
            status="confirmed",
            version=2,
            start_at="2026-10-06T15:00:00+03:00",
            acknowledged=True,
            recorded=True,
        )

    def test_s2_cancel_may_go_without_a_version_and_carries_the_reason(self) -> None:
        seen, handler = _recording(body=_answer(status="cancelled", acknowledged=False))

        out = _client(handler).act_as_specialist(
            external_user_id=MASTER,
            specialist_id=SID,
            appointment_id=AID,
            action="cancel",
            reason="заболела",
        )

        assert seen[0].url.path.endswith("/cancel/")
        assert json.loads(seen[0].content) == {"reason": "заболела"}
        assert (out.status, out.recorded) == ("cancelled", None)

    @pytest.mark.parametrize("action", ["acknowledge", "complete", "no-show"])
    def test_s3_a_write_that_needs_a_version_never_leaves_without_one(self, action) -> None:
        seen, handler = _recording()

        with pytest.raises(ValueError):
            _client(handler).act_as_specialist(
                external_user_id=MASTER, specialist_id=SID, appointment_id=AID, action=action
            )

        assert seen == []

    def test_s4_stale_version_surfaces_as_a_coded_4xx(self) -> None:
        body = {"error": {"code": "STALE_VERSION", "message": "stale", "details": {}}}
        seen, handler = _recording(status=409, body=body)

        with pytest.raises(bc.BookingBadRequestError) as caught:
            _client(handler).act_as_specialist(
                external_user_id=MASTER,
                specialist_id=SID,
                appointment_id=AID,
                action="acknowledge",
                expected_version=1,
            )

        assert (caught.value.status_code, caught.value.code) == (409, "STALE_VERSION")

    def test_s5_an_unknown_action_is_refused_before_the_network(self) -> None:
        seen, handler = _recording()

        with pytest.raises(ValueError):
            _client(handler).act_as_specialist(
                external_user_id=MASTER,
                specialist_id=SID,
                appointment_id=AID,
                action="delete",
                expected_version=1,
            )

        assert seen == []
