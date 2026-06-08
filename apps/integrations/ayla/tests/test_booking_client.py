"""Ayla booking client unit tests (S1 / #1016).

The HTTP client is now real (contract LOCKED, #193). These tests round-trip
all eight operations against an ``httpx.MockTransport`` and lock down:

* construction fail-fast on empty settings;
* the Bearer auth shape per the contract §2 (``X-External-User-ID`` only on
  writes; ``X-Idempotency-Key`` on writes);
* the envelope / error mapping per §6 (2xx → ``data``; 5xx/timeout/network →
  ``BookingUnavailableError`` trips the breaker; 4xx → ``BookingBadRequestError``
  does NOT trip);
* the UUID ``appointment_id`` threaded through create / reschedule / list;
* the inline circuit breaker lifecycle;
* the DTOs / Protocol the booking skill's provider-selector targets;
* singleton lifecycle.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc


_UUID = "3f1c2e9a-4b7d-4c2a-9e1f-8a2b6c0d1e34"


def _client(handler: Any) -> bc.AylaBookingHTTPClient:
    """Build a client whose HTTP goes through an in-memory MockTransport."""
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test",
        api_token="secret-tok",
        transport=httpx.MockTransport(handler),
    )


def _envelope(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"data": payload})


# ─── construction guards ───────────────────────────────────────────────────


class TestConstructionFailFast:
    def test_empty_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="AYLA_BASE_URL"):
            bc.AylaBookingHTTPClient(base_url="", api_token="t")

    def test_empty_token_raises(self) -> None:
        with pytest.raises(ValueError, match="AYLA_INTERNAL_API_TOKEN"):
            bc.AylaBookingHTTPClient(base_url="https://ayla.test", api_token="")

    def test_base_url_trailing_slash_stripped(self) -> None:
        client = bc.AylaBookingHTTPClient(base_url="https://ayla.test/", api_token="t")
        assert client._base_url == "https://ayla.test"


# ─── auth header shape (#1016) ─────────────────────────────────────────────


class TestAuthHeaders:
    def _client(self) -> bc.AylaBookingHTTPClient:
        return bc.AylaBookingHTTPClient(base_url="https://ayla.test", api_token="secret-tok")

    def test_read_headers_are_bearer_only(self) -> None:
        headers = self._client()._headers()
        assert headers["Authorization"] == "Bearer secret-tok"
        # Reads (catalog/slots) must NOT carry a user binding.
        assert "X-External-User-ID" not in headers

    def test_write_headers_add_external_user_id(self) -> None:
        headers = self._client()._headers(external_user_id="bot:telegram:42")
        assert headers["Authorization"] == "Bearer secret-tok"
        assert headers["X-External-User-ID"] == "bot:telegram:42"

    def test_no_legacy_service_token_header(self) -> None:
        # Guard against regressing to the nutrition client's X-Service-Token.
        headers = self._client()._headers(external_user_id="bot:telegram:42")
        assert "X-Service-Token" not in headers


# ─── circuit breaker ───────────────────────────────────────────────────────


class TestCircuit:
    def test_closed_by_default(self) -> None:
        circuit = bc._Circuit()
        assert circuit.is_open(now=100.0) is False

    def test_opens_after_threshold_failures(self) -> None:
        circuit = bc._Circuit()
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            circuit.record_failure(now=100.0)
        assert circuit.is_open(now=100.0) is True

    def test_below_threshold_stays_closed(self) -> None:
        circuit = bc._Circuit()
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD - 1):
            circuit.record_failure(now=100.0)
        assert circuit.is_open(now=100.0) is False

    def test_reopens_then_half_opens_after_cooldown(self) -> None:
        circuit = bc._Circuit()
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            circuit.record_failure(now=100.0)
        assert circuit.is_open(now=100.0) is True
        # After the cooldown elapses the breaker clears and probes again.
        later = 100.0 + bc.CIRCUIT_OPEN_DURATION_S
        assert circuit.is_open(now=later) is False

    def test_success_resets(self) -> None:
        circuit = bc._Circuit()
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            circuit.record_failure(now=100.0)
        circuit.record_success()
        assert circuit.is_open(now=100.0) is False
        assert circuit.failures == []

    def test_stale_failures_age_out_of_window(self) -> None:
        circuit = bc._Circuit()
        # One failure long ago, then threshold-1 recent ones → still closed.
        circuit.record_failure(now=0.0)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD - 1):
            circuit.record_failure(now=1000.0)
        assert circuit.is_open(now=1000.0) is False


# ─── reads: round-trip + URL/headers ───────────────────────────────────────


class TestReads:
    def test_get_services_global_catalog(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["ext"] = request.headers.get("X-External-User-ID")
            return _envelope(
                [
                    {
                        "id": 10,
                        "title": "Массаж",
                        "price_min": 1500,
                        "price_max": 2500,
                        "duration_s": 3600,
                        "category_id": None,
                    }
                ]
            )

        out = _client(handler).get_services()
        assert seen["url"] == "https://ayla.test/api/v1/internal/services/"
        assert seen["auth"] == "Bearer secret-tok"
        # Reads carry no user binding.
        assert seen["ext"] is None
        assert out[0].id == 10
        assert out[0].title == "Массаж"
        assert out[0].duration_s == 3600

    def test_get_services_master_scoped(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _envelope([])

        _client(handler).get_services(master_id=11)
        assert seen["url"] == "https://ayla.test/api/v1/internal/specialists/11/services/"

    def test_get_masters_list(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://ayla.test/api/v1/internal/specialists/"
            return _envelope(
                [{"id": 11, "name": "Ольга", "specialization": "Массаж", "rating": 4.5}]
            )

        out = _client(handler).get_masters()
        assert out[0].id == 11
        assert out[0].name == "Ольга"

    def test_get_masters_single_wraps(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://ayla.test/api/v1/internal/specialists/11/"
            return _envelope({"id": 11, "name": "Ольга", "specialization": "Массаж"})

        out = _client(handler).get_masters(master_id=11)
        assert len(out) == 1 and out[0].id == 11

    def test_get_available_times_passes_slot_params(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _envelope(
                [{"time": "14:00", "datetime": "2026-06-10T14:00:00+03:00", "duration_s": 3600}]
            )

        out = _client(handler).get_available_times(
            master_id=11, date="2026-06-10", service_ids=[10]
        )
        assert "specialists/11/slots/" in seen["url"]
        assert "service_ids=10" in seen["url"]
        assert "from=2026-06-10" in seen["url"]
        assert out[0].time == "14:00"
        assert out[0].duration_s == 3600

    def test_get_available_dates_derived_from_slots(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _envelope(
                [
                    {"time": "14:00", "datetime": "2026-06-10T14:00:00+03:00"},
                    {"time": "10:00", "datetime": "2026-06-11T10:00:00+03:00"},
                    {"time": "16:00", "datetime": "2026-06-10T16:00:00+03:00"},
                ]
            )

        dates = _client(handler).get_available_dates(master_id=11)
        assert dates == ["2026-06-10", "2026-06-11"]


# ─── writes: headers + UUID round-trip ─────────────────────────────────────


class TestWrites:
    def test_create_appointment_headers_and_uuid(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["auth"] = request.headers.get("Authorization")
            seen["ext"] = request.headers.get("X-External-User-ID")
            seen["idem"] = request.headers.get("X-Idempotency-Key")
            seen["body"] = json.loads(request.content)
            return _envelope({"appointment_id": _UUID, "status": "confirmed"})

        out = _client(handler).create_appointment(
            external_user_id="bot:telegram:42",
            master_id=11,
            service_ids=[10],
            datetime="2026-06-10T14:00:00",
            client_phone="79991234567",
            client_name="Anna",
            idempotency_key="idem-123",
        )
        assert seen["url"] == "https://ayla.test/api/v1/internal/appointments/"
        assert seen["method"] == "POST"
        assert seen["auth"] == "Bearer secret-tok"
        assert seen["ext"] == "bot:telegram:42"
        assert seen["idem"] == "idem-123"
        assert seen["body"]["specialist_id"] == 11
        assert seen["body"]["service_ids"] == [10]
        assert seen["body"]["client"] == {"name": "Anna", "phone": "79991234567"}
        assert out.appointment_id == _UUID

    def test_cancel_appointment_ok(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["idem"] = request.headers.get("X-Idempotency-Key")
            return httpx.Response(200, json={"data": {"status": "cancelled"}})

        ok = _client(handler).cancel_appointment(
            external_user_id="bot:telegram:42",
            appointment_id=_UUID,
            idempotency_key="idem-9",
        )
        assert ok is True
        assert seen["url"] == f"https://ayla.test/api/v1/internal/appointments/{_UUID}/cancel/"
        assert seen["idem"] == "idem-9"

    def test_cancel_appointment_404_returns_false(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

        client = _client(handler)
        assert (
            client.cancel_appointment(external_user_id="bot:telegram:42", appointment_id=_UUID)
            is False
        )
        # 404 is an idempotent no-op, not an outage — breaker stays closed.
        assert client._circuit.failures == []

    def test_reschedule_appointment_preserves_uuid(self) -> None:
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return _envelope({"appointment_id": _UUID, "status": "confirmed"})

        out = _client(handler).reschedule_appointment(
            external_user_id="bot:telegram:42",
            appointment_id=_UUID,
            datetime="2026-06-11T15:00:00",
        )
        assert seen["url"] == f"https://ayla.test/api/v1/internal/appointments/{_UUID}/reschedule/"
        assert seen["body"]["datetime"] == "2026-06-11T15:00:00"
        assert out.appointment_id == _UUID

    def test_get_user_appointments(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url) == "https://ayla.test/api/v1/internal/me/bookings/"
            assert request.headers.get("X-External-User-ID") == "bot:telegram:42"
            return _envelope(
                [
                    {
                        "appointment_id": _UUID,
                        "services": [{"id": "svc-uuid"}],
                        "specialist": {"id": "mst-uuid"},
                        "datetime": "2026-06-10T14:00:00+03:00",
                        "duration_s": 3600,
                    }
                ]
            )

        out = _client(handler).get_user_appointments(external_user_id="bot:telegram:42")
        assert out[0].appointment_id == _UUID
        assert out[0].master == {"id": "mst-uuid"}
        assert out[0].duration_s == 3600


# ─── error mapping + breaker (§6) ──────────────────────────────────────────


class TestErrorMapping:
    def test_5xx_raises_unavailable_and_trips_breaker(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": {"code": "DOWN"}})

        client = _client(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                client.get_masters()
        # Breaker now open → next call short-circuits without HTTP.
        assert client._circuit.is_open(now=__import__("time").monotonic()) is True

    def test_4xx_raises_bad_request_does_not_trip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": {"code": "SLOT_TAKEN"}})

        client = _client(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD + 2):
            with pytest.raises(bc.BookingBadRequestError, match="SLOT_TAKEN"):
                client.create_appointment(
                    external_user_id="bot:telegram:42",
                    master_id=11,
                    service_ids=[10],
                    datetime="2026-06-10T14:00:00",
                    client_phone="79991234567",
                    client_name="Anna",
                )
        # 4xx is user input, not an outage — breaker stays closed.
        assert client._circuit.failures == []

    def test_timeout_raises_unavailable_and_trips_breaker(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("slow")

        client = _client(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                client.get_services()
        assert len(client._circuit.failures) >= bc.CIRCUIT_FAILURE_THRESHOLD

    def test_network_error_raises_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        with pytest.raises(bc.BookingUnavailableError):
            _client(handler).get_services()

    def test_circuit_open_short_circuits(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500)

        client = _client(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                client.get_services()
        calls_after_trip = calls["n"]
        # Breaker open: this call must raise WITHOUT issuing HTTP.
        with pytest.raises(bc.BookingUnavailableError, match="circuit_open"):
            client.get_services()
        assert calls["n"] == calls_after_trip


# ─── DTOs / Protocol ────────────────────────────────────────────────────────


class TestDTOsAndProtocol:
    def test_dtos_are_frozen(self) -> None:
        svc = bc.AylaService(
            id=1,
            title="Массаж",
            price_min=1500.0,
            price_max=2500.0,
            duration_s=3600,
            category_id=None,
        )
        with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError
            svc.title = "x"  # type: ignore[misc]

    def test_http_client_satisfies_protocol(self) -> None:
        client = bc.AylaBookingHTTPClient(base_url="https://ayla.test", api_token="t")
        assert isinstance(client, bc.AylaBookingClient)

    def test_error_hierarchy(self) -> None:
        assert issubclass(bc.BookingUnavailableError, bc.BookingAPIError)
        assert issubclass(bc.BookingBadRequestError, bc.BookingAPIError)


# ─── singleton ──────────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_returns_same_instance(self) -> None:
        from django.test import override_settings

        with override_settings(AYLA_BASE_URL="https://ayla.test", AYLA_INTERNAL_API_TOKEN="t"):
            bc.reset_ayla_booking_client()
            first = bc.get_ayla_booking_client()
            second = bc.get_ayla_booking_client()
            assert first is second
        bc.reset_ayla_booking_client()

    def test_reset_drops_instance(self) -> None:
        from django.test import override_settings

        with override_settings(AYLA_BASE_URL="https://ayla.test", AYLA_INTERNAL_API_TOKEN="t"):
            bc.reset_ayla_booking_client()
            first = bc.get_ayla_booking_client()
            bc.reset_ayla_booking_client()
            second = bc.get_ayla_booking_client()
            assert first is not second
        bc.reset_ayla_booking_client()
