"""Ayla booking client unit tests (S1 / #1016).

The HTTP client talks to the merged S2 surface (#193). These tests drive it
through an ``httpx.MockTransport`` and lock down:

* construction fail-fast on empty settings;
* the Bearer auth shape per #1016 (and ``X-External-User-ID`` only on writes /
  ``me`` reads, ``X-Idempotency-Key`` on writes);
* the wire→DTO mapping for catalog/slots/appointments (UUID ids, ``{data}`` vs
  raw envelopes, single-day slots, the ``get_available_dates`` fan-out);
* request bodies for create/reschedule (singular ``service_id`` + ``client_id``);
* the §6 error mapping — 5xx/network → ``BookingUnavailableError`` (trips the
  breaker), 4xx → ``BookingBadRequestError`` (does not trip);
* the inline circuit breaker lifecycle and singleton.
"""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc


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


# ─── HTTP round-trip via MockTransport ─────────────────────────────────────


def _client_with(handler) -> bc.AylaBookingHTTPClient:
    """Build a client whose wire is driven by ``handler`` (httpx.MockTransport)."""
    return bc.AylaBookingHTTPClient(
        base_url="https://ayla.test",
        api_token="secret-tok",
        transport=httpx.MockTransport(handler),
    )


class TestReadRoundTrip:
    def test_get_services_catalog_unwraps_data(self) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "svc-uuid-1",
                            "name": "Массаж",
                            "price": "1500.00",
                            "duration_minutes": 60,
                            "category": "cat-uuid",
                        }
                    ]
                },
            )

        out = _client_with(handler).get_services()
        assert captured[0].url.path == "/api/v1/internal/services/"
        assert captured[0].headers["Authorization"] == "Bearer secret-tok"
        assert "X-External-User-ID" not in captured[0].headers
        assert len(out) == 1
        svc = out[0]
        assert (svc.id, svc.title, svc.duration_s) == ("svc-uuid-1", "Массаж", 3600)
        assert svc.price_min == 1500.0 and svc.category_id == "cat-uuid"

    def test_get_services_for_specialist_hits_nested_path_raw_list(self) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200, json=[{"id": "s1", "name": "X", "price": "10", "duration_minutes": 30}]
            )

        out = _client_with(handler).get_services(specialist_id="spec-1")
        assert captured[0].url.path == "/api/v1/internal/specialists/spec-1/services/"
        assert out[0].id == "s1"

    def test_get_masters_paginated_results(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "results": [{"id": "m1", "display_name": "Ольга", "rating": 4.5}],
                        "count": 1,
                    }
                },
            )

        out = _client_with(handler).get_masters()
        assert (out[0].id, out[0].name, out[0].rating) == ("m1", "Ольга", 4.5)

    def test_get_available_times_parses_iso_slots(self) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "date": "2026-06-10",
                    "slots": ["2026-06-10T14:00:00+03:00", "2026-06-10T15:30:00+03:00"],
                },
            )

        out = _client_with(handler).get_available_times(
            specialist_id="spec-1", date="2026-06-10", service_id="svc-1"
        )
        assert captured[0].url.path == "/api/v1/internal/specialists/spec-1/slots/"
        assert captured[0].url.params.get("service_id") == "svc-1"
        assert captured[0].url.params.get("date") == "2026-06-10"
        assert [s.time for s in out] == ["14:00", "15:30"]
        assert out[0].datetime == "2026-06-10T14:00:00+03:00"

    def test_get_available_dates_fans_out_over_window(self) -> None:
        today = date.today()
        free = {today.isoformat(), (today + timedelta(days=2)).isoformat()}
        calls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            day = req.url.params.get("date")
            calls.append(day)
            return httpx.Response(200, json={"date": day, "slots": ["x"] if day in free else []})

        out = _client_with(handler).get_available_dates(
            specialist_id="spec-1", service_id="svc-1", window_days=3
        )
        assert len(calls) == 3  # one slots call per day in the window
        assert out == [today.isoformat(), (today + timedelta(days=2)).isoformat()]

    def test_times_without_service_id_raises_before_any_http(self) -> None:
        # #1051: a service-less slots call must fail fast — no HTTP, so it can't
        # become a 400 MISSING_PARAM from Ayla.
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(400, json={"error": {"code": "MISSING_PARAM"}})

        with pytest.raises(bc.BookingBadRequestError, match="service_id_required"):
            _client_with(handler).get_available_times(
                specialist_id="spec-1", date="2026-06-10", service_id=""
            )
        assert calls == []  # never hit the wire

    def test_dates_without_service_id_raises_without_fanout(self) -> None:
        # #1051 core: a service-less availability request must NOT fan out into a
        # per-day 400 cascade — one clean BookingBadRequestError, zero HTTP.
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(400, json={"error": {"code": "MISSING_PARAM"}})

        with pytest.raises(bc.BookingBadRequestError, match="service_id_required"):
            _client_with(handler).get_available_dates(
                specialist_id="spec-1", service_id="", window_days=14
            )
        assert calls == []  # no 14× fan-out


class TestWriteRoundTrip:
    def test_create_sends_body_and_headers(self) -> None:
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            captured["body"] = json.loads(req.content)
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "appt-uuid",
                        "status": "confirmed",
                        "start_datetime": "2026-06-10T14:00:00+03:00",
                        "end_datetime": "2026-06-10T15:00:00+03:00",
                        "service": {"id": "svc-1"},
                        "specialist": {"id": "spec-1"},
                    }
                },
            )

        rec = _client_with(handler).create_appointment(
            external_user_id="bot:telegram:42",
            client_id="client-uuid",
            specialist_id="spec-1",
            service_id="svc-1",
            start_datetime="2026-06-10T14:00:00+03:00",
            idempotency_key="idem-1",
        )
        req = captured["req"]
        assert (req.method, req.url.path) == ("POST", "/api/v1/internal/appointments/")
        assert req.headers["X-External-User-ID"] == "bot:telegram:42"
        assert req.headers["X-Idempotency-Key"] == "idem-1"
        assert captured["body"] == {
            "client_id": "client-uuid",
            "specialist_id": "spec-1",
            "service_id": "svc-1",
            "start_datetime": "2026-06-10T14:00:00+03:00",
            # AMD-002 — default true (обратная совместимость).
            "payment_required": True,
        }
        assert rec.appointment_id == "appt-uuid"
        assert rec.raw["start_datetime"] == "2026-06-10T14:00:00+03:00"

    def test_cancel_200_true_404_false(self) -> None:
        def ok(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {}})

        def gone(req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": "x"}})

        assert (
            _client_with(ok).cancel_appointment(
                external_user_id="bot:telegram:42", appointment_id="a1"
            )
            is True
        )
        assert (
            _client_with(gone).cancel_appointment(
                external_user_id="bot:telegram:42", appointment_id="a1"
            )
            is False
        )

    def test_reschedule_preserves_appointment_id(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/api/v1/internal/appointments/a1/reschedule/"
            assert json.loads(req.content) == {"new_start_datetime": "2026-06-11T16:00:00+03:00"}
            return httpx.Response(
                200, json={"data": {"id": "a1", "start_datetime": "2026-06-11T16:00:00+03:00"}}
            )

        rec = _client_with(handler).reschedule_appointment(
            external_user_id="bot:telegram:42",
            appointment_id="a1",
            new_start_datetime="2026-06-11T16:00:00+03:00",
        )
        assert rec.appointment_id == "a1"

    def test_reschedule_sends_idempotency_key_header(self) -> None:
        """P1 test gap closed (Wave 1 Simple Reschedule audit): the
        create-appointment path already asserted ``X-Idempotency-Key``
        lands on the wire (see ``test_create_sends_body_and_headers``
        above); reschedule accepts the same ``idempotency_key`` kwarg
        (``booking_client.py`` write helper) but had no equivalent
        assertion."""
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["req"] = req
            return httpx.Response(
                200, json={"data": {"id": "a1", "start_datetime": "2026-06-11T16:00:00+03:00"}}
            )

        _client_with(handler).reschedule_appointment(
            external_user_id="bot:telegram:42",
            appointment_id="a1",
            new_start_datetime="2026-06-11T16:00:00+03:00",
            idempotency_key="idem-reschedule-1",
        )
        assert captured["req"].headers["X-Idempotency-Key"] == "idem-reschedule-1"

    def test_reschedule_falls_back_to_passed_id(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {}})

        rec = _client_with(handler).reschedule_appointment(
            external_user_id="x",
            appointment_id="a1",
            new_start_datetime="2026-06-11T16:00:00+03:00",
        )
        assert rec.appointment_id == "a1"

    def test_me_bookings_groups_upcoming_and_history(self) -> None:
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "upcoming": [
                            {
                                "id": "a1",
                                "start_datetime": "2026-06-10T14:00:00+03:00",
                                "end_datetime": "2026-06-10T15:00:00+03:00",
                                "service": {"id": "svc-1"},
                                "specialist": {"id": "spec-1"},
                            }
                        ],
                        "history": [],
                    }
                },
            )

        out = _client_with(handler).get_user_appointments(external_user_id="bot:telegram:42")
        assert captured[0].url.path == "/api/v1/internal/me/bookings/"
        assert captured[0].headers["X-External-User-ID"] == "bot:telegram:42"
        assert out[0].appointment_id == "a1"
        assert out[0].duration_s == 3600
        assert out[0].master == {"id": "spec-1"}


class TestErrorMapping:
    def test_5xx_raises_unavailable_and_trips_breaker(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        client = _client_with(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                client.get_services()
        assert client._circuit.is_open(now=time.monotonic()) is True

    def test_4xx_raises_bad_request_no_trip(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"code": "SLOT_TAKEN", "message": "x"}})

        client = _client_with(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD + 2):
            with pytest.raises(bc.BookingBadRequestError):
                client.get_services()
        assert client._circuit.is_open(now=time.monotonic()) is False

    def test_network_error_raises_unavailable(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        with pytest.raises(bc.BookingUnavailableError):
            _client_with(handler).get_services()

    def test_circuit_open_short_circuits_without_transport(self) -> None:
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={})

        client = _client_with(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                client.get_services()
        before = calls["n"]
        with pytest.raises(bc.BookingUnavailableError, match="circuit_open"):
            client.get_services()
        assert calls["n"] == before  # short-circuited, no new wire call


# ─── DTOs / Protocol ────────────────────────────────────────────────────────


class TestDTOsAndProtocol:
    def test_dtos_are_frozen(self) -> None:
        svc = bc.AylaService(
            id="svc-dto-1",
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


# ─── hardening (PR-3: CR-SF1 / CR-SF2 / S5-LOW2) ────────────────────────────


class TestClientReuse:
    """CR-SF1: one pooled httpx.Client is reused across the fan-out."""

    def test_single_client_built_for_date_fanout(self, monkeypatch) -> None:
        real_cls = bc.httpx.Client
        built: list[Any] = []

        def spy(*args: Any, **kwargs: Any) -> Any:
            inst = real_cls(*args, **kwargs)
            built.append(inst)
            return inst

        monkeypatch.setattr(bc.httpx, "Client", spy)

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"slots": []})

        client = _client_with(handler)
        client.get_available_dates(specialist_id="spec-1", service_id="svc-1", window_days=5)
        # Previously one client was built per HTTP call (5); now exactly one.
        assert len(built) == 1

    def test_client_accessor_is_idempotent(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"slots": []})

        client = _client_with(handler)
        assert client._client() is client._client()

    def test_close_drops_client(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"slots": []})

        client = _client_with(handler)
        first = client._client()
        client.close()
        assert client._http is None
        assert client._client() is not first


class TestWindowClamp:
    """S5-LOW2: get_available_dates clamps an oversized window."""

    def test_oversized_window_clamped(self) -> None:
        calls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req.url.params.get("date"))
            return httpx.Response(200, json={"slots": []})

        _client_with(handler).get_available_dates(
            specialist_id="spec-1", service_id="svc-1", window_days=1000
        )
        assert len(calls) == bc.MAX_AVAILABLE_DATES_WINDOW_DAYS


class TestCancelStatusMapping:
    """CR-SF2: cancel shares the consolidated status mapping (_fail_status)."""

    def test_cancel_204_true(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        assert (
            _client_with(handler).cancel_appointment(
                external_user_id="bot:telegram:42", appointment_id="a1"
            )
            is True
        )

    def test_cancel_5xx_trips_breaker(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        client = _client_with(handler)
        for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
            with pytest.raises(bc.BookingUnavailableError):
                client.cancel_appointment(external_user_id="x", appointment_id="a1")
        assert client._circuit.is_open(now=time.monotonic()) is True

    def test_cancel_4xx_bad_request_no_trip(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"code": "BAD", "message": "x"}})

        client = _client_with(handler)
        with pytest.raises(bc.BookingBadRequestError):
            client.cancel_appointment(external_user_id="x", appointment_id="a1")
        assert client._circuit.is_open(now=time.monotonic()) is False
