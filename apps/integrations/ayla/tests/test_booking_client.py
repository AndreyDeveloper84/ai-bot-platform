"""Ayla booking client unit tests (S1 / #1016).

The HTTP client is a deliberate skeleton — the Ayla booking wire contract is
not locked yet (#1016 pending S2 sign-off), so the public methods raise
``NotImplementedError`` rather than hit an undefined endpoint. These tests
therefore lock down what *is* real and must not regress when the live
implementation lands:

* construction fail-fast on empty settings;
* the Bearer auth shape per #1016 (and ``X-External-User-ID`` only on writes);
* the inline circuit breaker lifecycle (ported from the nutrition client);
* every public method failing loudly until the contract is locked;
* the DTOs / Protocol the booking skill's provider-selector targets;
* singleton lifecycle.
"""

from __future__ import annotations

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


# ─── skeleton: every method fails loudly until contract lock ───────────────


class TestSkeletonNotImplemented:
    def _client(self) -> bc.AylaBookingHTTPClient:
        return bc.AylaBookingHTTPClient(base_url="https://ayla.test", api_token="t")

    def test_get_services_pending(self) -> None:
        with pytest.raises(NotImplementedError, match="#1016"):
            self._client().get_services()

    def test_get_masters_pending(self) -> None:
        with pytest.raises(NotImplementedError, match="get_masters"):
            self._client().get_masters()

    def test_get_available_dates_pending(self) -> None:
        with pytest.raises(NotImplementedError):
            self._client().get_available_dates(master_id=1)

    def test_get_available_times_pending(self) -> None:
        with pytest.raises(NotImplementedError):
            self._client().get_available_times(master_id=1, date="2026-06-10")

    def test_create_appointment_pending(self) -> None:
        with pytest.raises(NotImplementedError, match="create_appointment"):
            self._client().create_appointment(
                external_user_id="bot:telegram:42",
                master_id=1,
                service_ids=[10],
                datetime="2026-06-10T14:00:00",
                client_phone="79991234567",
                client_name="Anna",
            )

    def test_cancel_appointment_pending(self) -> None:
        with pytest.raises(NotImplementedError, match="cancel_appointment"):
            self._client().cancel_appointment(
                external_user_id="bot:telegram:42",
                appointment_id="appt-1",
            )

    def test_reschedule_appointment_pending(self) -> None:
        with pytest.raises(NotImplementedError, match="reschedule_appointment"):
            self._client().reschedule_appointment(
                external_user_id="bot:telegram:42",
                appointment_id="appt-1",
                datetime="2026-06-11T15:00:00",
            )

    def test_get_user_appointments_pending(self) -> None:
        with pytest.raises(NotImplementedError, match="get_user_appointments"):
            self._client().get_user_appointments(external_user_id="bot:telegram:42")


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
