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
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture(autouse=True)
def _clear_django_cache() -> None:
    """DRF-997: slot/dates cache must not leak between tests."""
    from django.core.cache import cache

    cache.clear()


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
    def test_get_services_reads_canonical_catalog(self, db) -> None:
        """DRF-1004: the catalog comes from ``catalog/salon-services/`` scoped
        to the active tenant — not from the dead legacy ``services/`` feed."""
        tenant = Tenant.objects.create(slug="svc-cat", name="T")
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": "svc-uuid-1",
                            "tenant": str(tenant.id),
                            "template": None,
                            "category": "cat-uuid",
                            "name": "Массаж",
                            "duration_minutes": 60,
                            "base_price": "1500.00",
                        }
                    ],
                },
            )

        with tenant_scope(tenant):
            out = _client_with(handler).get_services()
        assert captured[0].url.path == "/api/v1/internal/catalog/salon-services/"
        assert captured[0].url.params["tenant"] == str(tenant.id)
        assert captured[0].url.params["is_active"] == "true"
        assert captured[0].headers["Authorization"] == "Bearer secret-tok"
        assert "X-External-User-ID" not in captured[0].headers
        assert len(out) == 1
        svc = out[0]
        assert (svc.id, svc.title, svc.duration_s) == ("svc-uuid-1", "Массаж", 3600)
        assert svc.price_min == 1500.0 and svc.price_max == 1500.0
        assert svc.category_id == "cat-uuid"

    def test_get_services_walks_pagination(self, db) -> None:
        """DRF-1004: 58 services do not fit the default DRF page — the client
        must follow ``next`` until the advertised ``count`` is collected."""
        tenant = Tenant.objects.create(slug="svc-pages", name="T")
        page_size = 25
        requested_pages: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            page = int(req.url.params.get("page", "1"))
            requested_pages.append(str(page))
            start = (page - 1) * page_size
            batch = [
                {
                    "id": f"svc-{i}",
                    "name": f"Service {i}",
                    "base_price": "100.00",
                    "duration_minutes": 30,
                }
                for i in range(start, min(start + page_size, 58))
            ]
            has_next = start + page_size < 58
            return httpx.Response(
                200,
                json={
                    "count": 58,
                    "next": f"https://ayla.test/...?page={page + 1}" if has_next else None,
                    "results": batch,
                },
            )

        with tenant_scope(tenant):
            out = _client_with(handler).get_services()
        assert len(out) == 58
        assert requested_pages == ["1", "2", "3"]

    def test_get_services_incomplete_catalog_raises(self, db) -> None:
        """DRF-1004: silently losing part of the catalog is the exact defect
        class being fixed — a count/rows mismatch must fail loudly."""
        tenant = Tenant.objects.create(slug="svc-short", name="T")

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "count": 58,
                    "next": None,
                    "results": [
                        {"id": "svc-1", "name": "X", "base_price": "10", "duration_minutes": 30}
                    ],
                },
            )

        with tenant_scope(tenant):
            with pytest.raises(bc.BookingUnavailableError, match="catalog_incomplete"):
                _client_with(handler).get_services()

    def test_get_services_base_price_preferred_over_legacy_price(self, db) -> None:
        tenant = Tenant.objects.create(slug="svc-price", name="T")

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": None,
                    "results": [
                        {
                            "id": "svc-new",
                            "name": "New",
                            "base_price": "2800.00",
                            "price": "10",
                            "duration_minutes": 40,
                        },
                        {
                            "id": "svc-old",
                            "name": "Old",
                            "price": "1500.00",
                            "duration_minutes": 60,
                        },
                    ],
                },
            )

        with tenant_scope(tenant):
            out = _client_with(handler).get_services()
        by_id = {s.id: s for s in out}
        assert by_id["svc-new"].price_min == 2800.0
        assert by_id["svc-old"].price_min == 1500.0

    def test_get_services_requires_tenant_scope(self) -> None:
        """DRF-1004: no tenant in scope is a call error, not an empty catalog."""

        def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("no wire call may happen without a tenant")

        with pytest.raises(bc.BookingBadRequestError, match="tenant_scope_required"):
            _client_with(handler).get_services()

    def test_get_services_for_specialist_uses_bookable_edges(self, db) -> None:
        """DRF-1004: the specialist branch reads canonical
        ``catalog/specialist-services/`` edges and joins them with the
        salon-services catalog — the legacy nested path returned count=0."""
        tenant = Tenant.objects.create(slug="svc-spec", name="T")
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            if req.url.path.endswith("catalog/specialist-services/"):
                return httpx.Response(
                    200,
                    json={
                        "count": 1,
                        "next": None,
                        "results": [
                            {
                                "id": "edge-1",
                                "salon_service": "svc-uuid-1",
                                "specialist": "spec-1",
                                "tenant": str(tenant.id),
                            }
                        ],
                    },
                )
            if req.url.path.endswith("catalog/salon-services/"):
                return httpx.Response(
                    200,
                    json={
                        "count": 2,
                        "next": None,
                        "results": [
                            {
                                "id": "svc-uuid-1",
                                "name": "Массаж",
                                "base_price": "100.00",
                                "duration_minutes": 60,
                            },
                            {
                                "id": "svc-uuid-2",
                                "name": "Другое",
                                "base_price": "50.00",
                                "duration_minutes": 30,
                            },
                        ],
                    },
                )
            return httpx.Response(404, json={})

        with tenant_scope(tenant):
            out = _client_with(handler).get_services(specialist_id="spec-1")
        edge_req = next(r for r in captured if r.url.path.endswith("specialist-services/"))
        assert edge_req.url.params["specialist"] == "spec-1"
        assert edge_req.url.params["tenant"] == str(tenant.id)
        assert edge_req.url.params["is_active"] == "true"
        assert [s.id for s in out] == ["svc-uuid-1"]
        assert out[0].title == "Массаж"

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

    def test_me_bookings_reads_the_paginated_items_envelope(self) -> None:
        """DRF-1032: the wire shape is ``{"data": {"items", "next_cursor"}}``.

        This test used to mock ``{"upcoming": [...], "history": [...]}`` — a
        shape Ayla has never returned (``records_api.py:346-349``). It passed
        while the client silently returned an EMPTY list against the real
        backend, which is precisely how the defect survived. The section
        contract itself is covered in ``test_me_bookings_contract.py``.
        """
        captured: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "items": [
                            {
                                "id": "a1",
                                "start_datetime": "2026-06-10T14:00:00+03:00",
                                "end_datetime": "2026-06-10T15:00:00+03:00",
                                "service": {"id": "svc-1"},
                                "specialist": {"id": "spec-1"},
                            }
                        ],
                        "next_cursor": None,
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
    def test_5xx_raises_unavailable_and_trips_breaker(self, db) -> None:
        tenant = Tenant.objects.create(slug="err-5xx", name="T")

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        client = _client_with(handler)
        with tenant_scope(tenant):
            for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(bc.BookingUnavailableError):
                    client.get_services()
        assert client._circuit.is_open(now=time.monotonic()) is True

    def test_4xx_raises_bad_request_no_trip(self, db) -> None:
        tenant = Tenant.objects.create(slug="err-4xx", name="T")

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"code": "SLOT_TAKEN", "message": "x"}})

        client = _client_with(handler)
        with tenant_scope(tenant):
            for _ in range(bc.CIRCUIT_FAILURE_THRESHOLD + 2):
                with pytest.raises(bc.BookingBadRequestError):
                    client.get_services()
        assert client._circuit.is_open(now=time.monotonic()) is False

    def test_network_error_raises_unavailable(self, db) -> None:
        tenant = Tenant.objects.create(slug="err-net", name="T")

        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        with tenant_scope(tenant):
            with pytest.raises(bc.BookingUnavailableError):
                _client_with(handler).get_services()

    def test_circuit_open_short_circuits_without_transport(self, db) -> None:
        tenant = Tenant.objects.create(slug="err-circuit", name="T")
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={})

        client = _client_with(handler)
        with tenant_scope(tenant):
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


class TestRateLimitRetry:
    """DRF-997: 429 must be retried and, if it persists, surfaced as
    transient unavailability without opening the circuit breaker."""

    def test_429_retries_then_raises_unavailable(self, monkeypatch) -> None:
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"code": "RATE_LIMITED"}},
            )

        # Speed the test up — the retry logic still exercises the loop.
        monkeypatch.setattr(bc, "RATE_LIMIT_BACKOFF_BASE_S", 0.0)
        client = _client_with(handler)
        with pytest.raises(bc.BookingUnavailableError, match="rate_limited"):
            client.get_available_times(
                specialist_id="spec-1", date="2026-06-10", service_id="svc-1"
            )
        assert len(calls) == bc.RATE_LIMIT_MAX_RETRIES + 1
        # 429 is a rate-limit, not a server failure — breaker must stay closed.
        assert client._circuit.is_open(now=time.monotonic()) is False

    def test_429_eventual_success_returns_payload(self, monkeypatch) -> None:
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0"},
                    json={"error": {"code": "RATE_LIMITED"}},
                )
            return httpx.Response(
                200,
                json={"date": "2026-06-10", "slots": ["2026-06-10T14:00:00+03:00"]},
            )

        monkeypatch.setattr(bc, "RATE_LIMIT_BACKOFF_BASE_S", 0.0)
        out = _client_with(handler).get_available_times(
            specialist_id="spec-1", date="2026-06-10", service_id="svc-1"
        )
        assert len(calls) == 2
        assert [s.time for s in out] == ["14:00"]

    def test_429_retry_after_is_capped(self, monkeypatch) -> None:
        """A huge Retry-After header must be clipped to 1.5 s (RATE_LIMIT_MAX_WAIT_S)."""
        calls: list[httpx.Request] = []
        sleeps: list[float] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            if len(calls) == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "300"},
                    json={"error": {"code": "RATE_LIMITED"}},
                )
            return httpx.Response(
                200,
                json={"date": "2026-06-10", "slots": ["2026-06-10T14:00:00+03:00"]},
            )

        monkeypatch.setattr(bc, "RATE_LIMIT_BACKOFF_BASE_S", 0.0)
        monkeypatch.setattr(bc.time, "sleep", sleeps.append)
        out = _client_with(handler).get_available_times(
            specialist_id="spec-1", date="2026-06-10", service_id="svc-1"
        )
        assert len(calls) == 2
        assert [s.time for s in out] == ["14:00"]
        assert len(sleeps) == 1
        # Jitter clips the capped Retry-After to 75-100 % of the max wait.
        assert bc.RATE_LIMIT_MAX_WAIT_S * 0.75 <= sleeps[0] <= bc.RATE_LIMIT_MAX_WAIT_S
        assert sleeps[0] <= 1.5

    def test_429_total_sleep_budget_is_capped_at_three_seconds(self, monkeypatch) -> None:
        """The worst-case per-call synchronous sleep is ≤ 3 s."""
        calls: list[httpx.Request] = []
        sleeps: list[float] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(
                429,
                headers={"Retry-After": "300"},
                json={"error": {"code": "RATE_LIMITED"}},
            )

        monkeypatch.setattr(bc, "RATE_LIMIT_BACKOFF_BASE_S", 0.0)
        monkeypatch.setattr(bc.time, "sleep", sleeps.append)
        with pytest.raises(bc.BookingUnavailableError, match="rate_limited"):
            _client_with(handler).get_available_times(
                specialist_id="spec-1", date="2026-06-10", service_id="svc-1"
            )
        assert len(calls) == bc.RATE_LIMIT_MAX_RETRIES + 1
        assert sum(sleeps) <= 3.0
        for s in sleeps:
            assert s <= bc.RATE_LIMIT_MAX_WAIT_S


class TestSlotCache:
    """DRF-997: short-lived cache for slot/dates lookups prevents repeated
    backend calls within the TTL window."""

    def test_times_cache_hits_prevent_second_wire_call(self) -> None:
        from django.core.cache import cache

        cache.clear()
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(
                200,
                json={"date": "2026-06-10", "slots": ["2026-06-10T14:00:00+03:00"]},
            )

        client = _client_with(handler)
        out1 = client.get_available_times(
            specialist_id="spec-1", date="2026-06-10", service_id="svc-1"
        )
        out2 = client.get_available_times(
            specialist_id="spec-1", date="2026-06-10", service_id="svc-1"
        )
        assert len(calls) == 1
        assert len(out1) == len(out2) == 1
        cache.clear()

    def test_dates_cache_hits_prevent_second_fanout(self) -> None:
        from django.core.cache import cache

        cache.clear()
        calls: list[httpx.Request] = []

        def handler(req: httpx.Request) -> httpx.Response:
            calls.append(req)
            return httpx.Response(200, json={"date": req.url.params.get("date"), "slots": []})

        client = _client_with(handler)
        client.get_available_dates(specialist_id="spec-1", service_id="svc-1", window_days=3)
        client.get_available_dates(specialist_id="spec-1", service_id="svc-1", window_days=3)
        # First call fans out to 3 days; second call must be served from cache.
        assert len(calls) == 3
        cache.clear()

    def test_cache_keys_are_tenant_isolated(self, db) -> None:
        """Two tenants with the same specialist/service/date must not share a cache key."""
        t1 = Tenant.objects.create(slug="cache-tenant-1", name="T1")
        t2 = Tenant.objects.create(slug="cache-tenant-2", name="T2")

        with tenant_scope(t1):
            key1_times = bc._slots_cache_key("spec-1", "svc-1", "2026-06-10")
            key1_dates = bc._dates_cache_key("spec-1", "svc-1", 14)
        with tenant_scope(t2):
            key2_times = bc._slots_cache_key("spec-1", "svc-1", "2026-06-10")
            key2_dates = bc._dates_cache_key("spec-1", "svc-1", 14)

        assert key1_times != key2_times
        assert key1_dates != key2_dates
        assert str(t1.id) in key1_times
        assert str(t2.id) in key2_times


class TestWriteCacheInvalidation:
    """DRF-997: successful writes drop the affected slot/dates cache keys."""

    def test_create_appointment_invalidates_affected_cache(self) -> None:
        from django.core.cache import cache

        cache.clear()
        slots_key = bc._slots_cache_key("spec-1", "svc-1", "2026-06-10")
        dates_key = bc._dates_cache_key("spec-1", "svc-1", bc.AVAILABLE_DATES_WINDOW_DAYS)
        cache.set(
            slots_key,
            [bc.AylaSlot(time="14:00", datetime="2026-06-10T14:00:00+03:00", duration_s=3600)],
            bc.SLOT_CACHE_TTL_S,
        )
        cache.set(dates_key, ["2026-06-10"], bc.SLOT_CACHE_TTL_S)

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json={"data": {"id": "appt-uuid", "start_datetime": "2026-06-10T14:00:00+03:00"}},
            )

        _client_with(handler).create_appointment(
            external_user_id="bot:max:1",
            client_id="client-uuid",
            specialist_id="spec-1",
            service_id="svc-1",
            start_datetime="2026-06-10T14:00:00+03:00",
        )
        assert cache.get(slots_key) is None
        assert cache.get(dates_key) is None
        cache.clear()

    def test_cancel_appointment_invalidates_affected_cache(self) -> None:
        from django.core.cache import cache

        cache.clear()
        slots_key = bc._slots_cache_key("spec-1", "svc-1", "2026-06-10")
        dates_key = bc._dates_cache_key("spec-1", "svc-1", bc.AVAILABLE_DATES_WINDOW_DAYS)
        cache.set(
            slots_key,
            [bc.AylaSlot(time="14:00", datetime="2026-06-10T14:00:00+03:00", duration_s=3600)],
            bc.SLOT_CACHE_TTL_S,
        )
        cache.set(dates_key, ["2026-06-10"], bc.SLOT_CACHE_TTL_S)

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {}})

        _client_with(handler).cancel_appointment(
            external_user_id="bot:max:1",
            appointment_id="appt-uuid",
            specialist_id="spec-1",
            service_id="svc-1",
            date="2026-06-10",
        )
        assert cache.get(slots_key) is None
        assert cache.get(dates_key) is None
        cache.clear()

    def test_reschedule_appointment_invalidates_old_and_new_cache(self) -> None:
        from django.core.cache import cache

        cache.clear()
        old_slots_key = bc._slots_cache_key("spec-1", "svc-1", "2026-06-10")
        new_slots_key = bc._slots_cache_key("spec-1", "svc-1", "2026-06-11")
        dates_key = bc._dates_cache_key("spec-1", "svc-1", bc.AVAILABLE_DATES_WINDOW_DAYS)
        cache.set(
            old_slots_key,
            [bc.AylaSlot(time="14:00", datetime="2026-06-10T14:00:00+03:00", duration_s=3600)],
            bc.SLOT_CACHE_TTL_S,
        )
        cache.set(
            new_slots_key,
            [bc.AylaSlot(time="15:00", datetime="2026-06-11T15:00:00+03:00", duration_s=3600)],
            bc.SLOT_CACHE_TTL_S,
        )
        cache.set(dates_key, ["2026-06-10", "2026-06-11"], bc.SLOT_CACHE_TTL_S)

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"data": {"id": "appt-uuid", "start_datetime": "2026-06-11T15:00:00+03:00"}},
            )

        _client_with(handler).reschedule_appointment(
            external_user_id="bot:max:1",
            appointment_id="appt-uuid",
            new_start_datetime="2026-06-11T15:00:00+03:00",
            specialist_id="spec-1",
            service_id="svc-1",
            old_date="2026-06-10",
        )
        assert cache.get(old_slots_key) is None
        assert cache.get(new_slots_key) is None
        assert cache.get(dates_key) is None
        cache.clear()


class TestCanonicalVersionRead:
    """DRF-1233 — the value a concurrency guard is built on.

    Every failure mode here ends in an exception rather than a default,
    and that is the point: a guessed version does not make the guard
    fail loudly, it makes it pass wrongly. The booking would be closed
    or moved against a revision nobody looked at.
    """

    def _handler(self, payload, status=200):
        def handler(request: httpx.Request) -> httpx.Response:
            self.seen = request
            return httpx.Response(status, json=payload)

        return handler

    def test_reads_the_four_canonical_facts(self) -> None:
        client = _client_with(
            self._handler(
                {
                    "data": {
                        "id": "appt-1",
                        "version": 4,
                        "status": "confirmed",
                        "start_datetime": "2026-08-22T15:00:00+03:00",
                    }
                }
            )
        )

        got = client.get_appointment_version(
            external_user_id="bot:max:1",
            booking_id="appt-1",
        )

        assert got.version == 4
        assert got.status == "confirmed"
        assert got.start_datetime == "2026-08-22T15:00:00+03:00"
        assert str(self.seen.url).endswith("/api/v1/internal/appointments/appt-1/")

    def test_the_actor_travels(self) -> None:
        """Upstream hides a booking this actor may not see; it can only do
        that if we say who is asking."""
        client = _client_with(self._handler({"data": {"id": "a", "version": 1}}))

        client.get_appointment_version(
            external_user_id="bot:max:83146139",
            booking_id="a",
        )

        assert self.seen.headers["x-external-user-id"] == "bot:max:83146139"

    def test_a_missing_version_is_an_outage_not_a_default(self) -> None:
        client = _client_with(self._handler({"data": {"id": "a", "status": "confirmed"}}))

        with pytest.raises(bc.BookingUnavailableError):
            client.get_appointment_version(external_user_id="bot:max:1", booking_id="a")

    def test_a_non_numeric_version_is_refused(self) -> None:
        client = _client_with(self._handler({"data": {"id": "a", "version": "later"}}))

        with pytest.raises(bc.BookingUnavailableError):
            client.get_appointment_version(external_user_id="bot:max:1", booking_id="a")

    def test_a_shape_we_cannot_read_is_refused(self) -> None:
        client = _client_with(self._handler({"data": ["not", "a", "dict"]}))

        with pytest.raises(bc.BookingUnavailableError):
            client.get_appointment_version(external_user_id="bot:max:1", booking_id="a")

    def test_a_hidden_booking_surfaces_as_an_error(self) -> None:
        """404 upstream covers both «no such booking» and «not yours»."""
        client = _client_with(self._handler({"error": {"code": "NOT_FOUND"}}, status=404))

        with pytest.raises(bc.BookingAPIError):
            client.get_appointment_version(external_user_id="bot:max:1", booking_id="a")
