"""YClients client unit tests (DRF-837 / Phase 1 / B1).

Network is intercepted via ``unittest.mock.patch`` on
``requests.Session.request``. We assert:

* Each of the 7 endpoints sends the correct method, URL, and WAF-bypass
  headers (``X-Partner-Id: 11958`` + browser-y ``User-Agent``).
* HTTP 4xx + 5xx raise the right error class (5xx →
  ``YClientsUnavailableError`` AND trips the breaker, 4xx →
  ``YClientsAPIError`` and does NOT).
* Circuit breaker opens after 5 failures within the window and blocks
  the 6th call without making a request.
* Circuit cooldown clears after 30s (monkey-patch ``time.monotonic``).
* Singleton factory reads settings and ``reset_yclients_client`` drops it.
* ``create_record`` body shape matches the YClients booking spec.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from apps.integrations.yclients import client as yc


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def api() -> yc.YClientsAPI:
    """A YClientsAPI with stub tokens — no real HTTP touched here."""
    return yc.YClientsAPI(
        partner_token="ptoken",
        user_token="utoken",
        company_id="42",
        base_url="https://yclients.test/api/v1",
    )


def _mock_response(
    *,
    status: int = 200,
    json_body: dict[str, Any] | None = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text or (str(json_body) if json_body else "")
    return resp


# ─── construction guards ───────────────────────────────────────────────────


class TestConstructionFailFast:
    def test_empty_partner_token_raises(self) -> None:
        with pytest.raises(ValueError, match="YCLIENTS_PARTNER_TOKEN"):
            yc.YClientsAPI(partner_token="", user_token="u", company_id="1")

    def test_empty_user_token_raises(self) -> None:
        with pytest.raises(ValueError, match="YCLIENTS_USER_TOKEN"):
            yc.YClientsAPI(partner_token="p", user_token="", company_id="1")

    def test_empty_company_id_raises(self) -> None:
        with pytest.raises(ValueError, match="YCLIENTS_COMPANY_ID"):
            yc.YClientsAPI(partner_token="p", user_token="u", company_id="")


# ─── WAF headers + session config ─────────────────────────────────────────


class TestSessionHeaders:
    def test_waf_bypass_headers_present(self, api: yc.YClientsAPI) -> None:
        """Without User-Agent + X-Partner-Id YClients returns 403."""
        headers = api._session.headers
        assert headers["X-Partner-Id"] == "11958"
        assert "Mozilla/5.0" in headers["User-Agent"]
        assert headers["Accept"] == "application/vnd.yclients.v2+json"
        assert "Bearer ptoken, User utoken" in headers["Authorization"]


# ─── per-endpoint happy-path + URL + headers ──────────────────────────────


class TestGetServices:
    def test_url_method_and_unwrap(self, api: yc.YClientsAPI) -> None:
        body = {
            "success": True,
            "data": [
                {
                    "id": 7,
                    "title": "Massage 60min",
                    "price_min": 1500.0,
                    "price_max": 1500.0,
                    "duration": 3600,
                    "category_id": 3,
                }
            ],
        }
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(status=200, json_body=body),
        ) as mock:
            result = api.get_services()

        call = mock.call_args
        assert call.kwargs["method"] == "GET"
        assert call.kwargs["url"] == "https://yclients.test/api/v1/company/42/services"
        assert len(result) == 1
        assert result[0].id == 7
        assert result[0].title == "Massage 60min"
        assert result[0].duration_s == 3600

    def test_filters_passed_as_params(self, api: yc.YClientsAPI) -> None:
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body={"success": True, "data": []}),
        ) as mock:
            api.get_services(staff_id=5, category_id=2)

        params = mock.call_args.kwargs["params"]
        assert params == {"staff_id": 5, "category_id": 2}


class TestGetStaff:
    def test_endpoint_and_filters_out_inactive(self, api: yc.YClientsAPI) -> None:
        body = {
            "success": True,
            "data": [
                {"id": 1, "name": "Anna", "active": True, "bookable": True},
                {"id": 2, "name": "Fired", "fired": 1},
                {"id": 3, "name": "Hidden", "hidden": 1},
                {"id": 4, "name": "Inactive", "active": False},
            ],
        }
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body=body),
        ) as mock:
            result = api.get_staff()

        assert mock.call_args.kwargs["url"] == ("https://yclients.test/api/v1/company/42/staff")
        assert mock.call_args.kwargs["method"] == "GET"
        assert [s.id for s in result] == [1]


class TestGetAvailableDates:
    def test_envelope_and_sort(self, api: yc.YClientsAPI) -> None:
        body = {
            "success": True,
            "data": {"booking_dates": ["2026-05-20", "2026-05-15", "2026-05-18"]},
        }
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body=body),
        ) as mock:
            result = api.get_available_dates(staff_id=5)

        assert mock.call_args.kwargs["url"] == ("https://yclients.test/api/v1/book_dates/42")
        assert result == ["2026-05-15", "2026-05-18", "2026-05-20"]
        assert mock.call_args.kwargs["params"] == {"staff_id": 5}


class TestGetAvailableTimes:
    def test_url_includes_staff_and_date(self, api: yc.YClientsAPI) -> None:
        body = {
            "success": True,
            "data": [
                {"time": "10:00", "seance_length": 3600},
                {"datetime": "2026-05-20T11:30:00+03:00", "seance_length": 1800},
                "12:00",
            ],
        }
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body=body),
        ) as mock:
            result = api.get_available_times(
                staff_id=5,
                date="2026-05-20",
                service_ids=[7],
            )

        assert mock.call_args.kwargs["url"] == (
            "https://yclients.test/api/v1/book_times/42/5/2026-05-20"
        )
        assert [t.time for t in result] == ["10:00", "11:30", "12:00"]
        assert result[0].seance_length_s == 3600
        assert mock.call_args.kwargs["params"] == {"service_ids": [7]}


class TestCreateRecord:
    def test_body_shape_matches_yclients_spec(self, api: yc.YClientsAPI) -> None:
        body = {
            "success": True,
            "data": [{"record_id": 9001, "record_hash": "abc123"}],
        }
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body=body),
        ) as mock:
            booking = api.create_record(
                staff_id=5,
                services=[7, 8],
                datetime="2026-05-20T10:00:00",
                client_phone="+79991234567",
                client_name="Anna",
                client_email="a@example.com",
                comment="please call ahead",
                notify_by_sms=1,
            )

        call = mock.call_args
        assert call.kwargs["method"] == "POST"
        assert call.kwargs["url"] == ("https://yclients.test/api/v1/book_record/42")
        sent = call.kwargs["json"]
        assert sent["phone"] == "+79991234567"
        assert sent["fullname"] == "Anna"
        assert sent["email"] == "a@example.com"
        assert sent["notify_by_sms"] == 1
        assert sent["notify_by_email"] == 0
        assert sent["comment"] == "please call ahead"
        assert sent["appointments"] == [
            {
                "id": 1,
                "services": [7, 8],
                "staff_id": 5,
                "datetime": "2026-05-20T10:00:00",
            }
        ]
        assert booking.record_id == 9001
        assert booking.record_hash == "abc123"

    def test_success_false_raises(self, api: yc.YClientsAPI) -> None:
        body = {"success": False, "meta": {"message": "slot is taken"}, "data": []}
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body=body),
        ):
            with pytest.raises(yc.YClientsAPIError, match="slot is taken"):
                api.create_record(
                    staff_id=5,
                    services=[7],
                    datetime="2026-05-20T10:00:00",
                    client_phone="+79991234567",
                    client_name="Anna",
                )


class TestCancelRecord:
    def test_delete_method_and_url(self, api: yc.YClientsAPI) -> None:
        resp = _mock_response(status=204, json_body=None)
        with patch.object(api._session, "request", return_value=resp) as mock:
            ok = api.cancel_record(record_id=9001)

        assert ok is True
        assert mock.call_args.args[0] == "DELETE"
        assert mock.call_args.args[1] == ("https://yclients.test/api/v1/records/42/9001")

    def test_404_returns_false(self, api: yc.YClientsAPI) -> None:
        resp = _mock_response(status=404, text="not found")
        with patch.object(api._session, "request", return_value=resp):
            assert api.cancel_record(record_id=9001) is False


class TestGetUserRecords:
    def test_endpoint_and_unwrap(self, api: yc.YClientsAPI) -> None:
        body = {
            "success": True,
            "data": [
                {
                    "id": 555,
                    "services": [{"id": 7, "title": "Massage"}],
                    "company": {"id": 42},
                    "staff": {"id": 5, "name": "Anna"},
                    "date": "2026-05-20 10:00:00",
                    "datetime": "2026-05-20T10:00:00+03:00",
                    "seance_length": 3600,
                }
            ],
        }
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body=body),
        ) as mock:
            result = api.get_user_records()

        assert mock.call_args.kwargs["url"] == ("https://yclients.test/api/v1/user/records")
        assert mock.call_args.kwargs["method"] == "GET"
        assert len(result) == 1
        assert result[0].id == 555
        assert result[0].seance_length == 3600


# ─── error handling ────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_4xx_raises_api_error_without_tripping_breaker(self, api: yc.YClientsAPI) -> None:
        resp = _mock_response(status=400, text="bad request")
        with patch.object(api._session, "request", return_value=resp):
            with pytest.raises(yc.YClientsAPIError) as exc_info:
                api.get_services()
        assert "http_400" in str(exc_info.value)
        # 4xx must NOT count as a breaker failure.
        assert api._circuit.opened_at is None
        assert api._circuit.failures == []

    def test_5xx_raises_unavailable_and_counts_failure(self, api: yc.YClientsAPI) -> None:
        resp = _mock_response(status=500, text="boom")
        with patch.object(api._session, "request", return_value=resp):
            with pytest.raises(yc.YClientsUnavailableError):
                api.get_services()
        assert len(api._circuit.failures) == 1


# ─── circuit breaker ───────────────────────────────────────────────────────


class TestCircuitBreaker:
    def test_opens_after_threshold_failures_and_blocks_next_call(self, api: yc.YClientsAPI) -> None:
        resp = _mock_response(status=500, text="boom")
        with patch.object(api._session, "request", return_value=resp) as mock:
            for _ in range(yc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(yc.YClientsUnavailableError):
                    api.get_services()

            assert api._circuit.opened_at is not None
            calls_before = mock.call_count

            # 6th call should be blocked at the breaker — no HTTP issued.
            with pytest.raises(yc.YClientsUnavailableError, match="circuit_open"):
                api.get_services()
            assert mock.call_count == calls_before

    def test_cooldown_lets_through_after_open_duration(
        self,
        api: yc.YClientsAPI,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Trip the breaker at t=0.
        clock = {"now": 1_000.0}
        monkeypatch.setattr(
            yc.time,
            "monotonic",
            lambda: clock["now"],  # type: ignore[attr-defined]
        )
        resp_500 = _mock_response(status=500, text="boom")
        with patch.object(api._session, "request", return_value=resp_500):
            for _ in range(yc.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(yc.YClientsUnavailableError):
                    api.get_services()
        assert api._circuit.opened_at is not None

        # Advance past the open window — the breaker should half-open and probe.
        clock["now"] += yc.CIRCUIT_OPEN_DURATION_S + 1.0
        ok_body = {"success": True, "data": []}
        with patch.object(
            api._session,
            "request",
            return_value=_mock_response(json_body=ok_body),
        ):
            result = api.get_services()
        assert result == []
        # Probe success → breaker fully closed.
        assert api._circuit.opened_at is None
        assert api._circuit.failures == []


# ─── singleton + settings glue ─────────────────────────────────────────────


class TestSingleton:
    def test_factory_reads_settings_and_reset(
        self,
        settings: Any,  # pytest-django
    ) -> None:
        settings.YCLIENTS_PARTNER_TOKEN = "from-settings-p"
        settings.YCLIENTS_USER_TOKEN = "from-settings-u"
        settings.YCLIENTS_COMPANY_ID = "from-settings-c"
        settings.YCLIENTS_BASE_URL = "https://yclients.test/api/v1"

        yc.reset_yclients_client()
        instance = yc.get_yclients_client()
        assert instance.partner_token == "from-settings-p"
        assert instance.user_token == "from-settings-u"
        assert instance.company_id == "from-settings-c"

        # Same singleton on subsequent calls.
        assert yc.get_yclients_client() is instance

        # Reset drops it; next call rebuilds.
        yc.reset_yclients_client()
        rebuilt = yc.get_yclients_client()
        assert rebuilt is not instance

        # Cleanup so the singleton doesn't leak into other tests.
        yc.reset_yclients_client()

    def test_factory_raises_when_settings_empty(self, settings: Any) -> None:
        settings.YCLIENTS_PARTNER_TOKEN = ""
        settings.YCLIENTS_USER_TOKEN = "u"
        settings.YCLIENTS_COMPANY_ID = "c"
        yc.reset_yclients_client()
        with pytest.raises(ValueError, match="YCLIENTS_PARTNER_TOKEN"):
            yc.get_yclients_client()
        yc.reset_yclients_client()


# ---------------------------------------------------------------------------
# YClients retro hotfix coverage
# ---------------------------------------------------------------------------


class TestRetroB2RetryPolicy:
    """Pre-fix the urllib3 Retry policy allowed retries on POST/PUT/DELETE.
    A successful YClients write followed by a transient 5xx on the
    response leg would re-POST and silently create a duplicate booking
    with no compensate path. Post-fix only idempotent verbs retry.
    """

    def test_retry_allowed_methods_excludes_unsafe_verbs(self, settings: Any) -> None:
        settings.YCLIENTS_PARTNER_TOKEN = "p"
        settings.YCLIENTS_USER_TOKEN = "u"
        settings.YCLIENTS_COMPANY_ID = "c"
        yc.reset_yclients_client()
        instance = yc.get_yclients_client()
        # Pull the mounted HTTPAdapter to inspect its retry policy.
        adapter = instance._session.get_adapter("https://api.yclients.com/")
        retry = adapter.max_retries
        allowed = {m.upper() for m in (retry.allowed_methods or [])}
        # Idempotent verbs only.
        assert "GET" in allowed
        # Unsafe verbs MUST NOT be retried — duplicate-write hazard.
        assert "POST" not in allowed
        assert "PUT" not in allowed
        assert "DELETE" not in allowed
        yc.reset_yclients_client()


class TestRetroY11PiiRedaction:
    """4xx YClients responses echo submitted phone/email back; the
    pre-fix log preview + exception message leaked them. Post-fix
    redacts via _redact_pii before logging / raising.
    """

    def test_phone_redacted(self) -> None:
        from apps.integrations.yclients.client import _redact_pii

        s = "Invalid phone: +79991234567 cannot be used"
        out = _redact_pii(s)
        assert "+79991234567" not in out
        assert "[REDACTED:phone]" in out

    def test_email_redacted(self) -> None:
        from apps.integrations.yclients.client import _redact_pii

        s = "Email already used: anna@example.com"
        out = _redact_pii(s)
        assert "anna@example.com" not in out
        assert "[REDACTED:email]" in out

    def test_non_pii_passthrough(self) -> None:
        from apps.integrations.yclients.client import _redact_pii

        s = "Invalid service_id=22"
        assert _redact_pii(s) == s


class TestRetroY12DtoTypeGuards:
    """create_record pre-fix fabricated ``record_id=0`` on missing/
    malformed response. Post-fix raises YClientsAPIError so Hotfix D's
    compensating ``cancel_record(record_id=0)`` can't fire with garbage
    that succeeds-with-NOT_FOUND and hides the split-brain.
    """

    def test_create_record_raises_on_missing_record_id(
        self, settings: Any, monkeypatch: Any
    ) -> None:
        from apps.integrations.yclients.client import YClientsAPIError

        settings.YCLIENTS_PARTNER_TOKEN = "p"
        settings.YCLIENTS_USER_TOKEN = "u"
        settings.YCLIENTS_COMPANY_ID = "c"
        yc.reset_yclients_client()
        client = yc.get_yclients_client()

        # Stub _request to return a malformed «success but no record_id»
        # response shape — exactly the failure mode the retro flagged.
        def _stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "success": True,
                "data": [{"record_hash": "h", "datetime": "2026-05-22 10:00:00"}],
                "meta": {},
            }

        monkeypatch.setattr(client, "_request", _stub)
        with pytest.raises(YClientsAPIError, match="missing_record_id"):
            client.create_record(
                staff_id=11,
                services=[22],
                datetime="2026-05-22T10:00:00",
                client_phone="79991234567",
                client_name="Anna",
            )
        yc.reset_yclients_client()

    def test_create_record_raises_on_zero_record_id(self, settings: Any, monkeypatch: Any) -> None:
        from apps.integrations.yclients.client import YClientsAPIError

        settings.YCLIENTS_PARTNER_TOKEN = "p"
        settings.YCLIENTS_USER_TOKEN = "u"
        settings.YCLIENTS_COMPANY_ID = "c"
        yc.reset_yclients_client()
        client = yc.get_yclients_client()

        def _stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "success": True,
                "data": [{"record_id": 0, "record_hash": "h"}],
                "meta": {},
            }

        monkeypatch.setattr(client, "_request", _stub)
        with pytest.raises(YClientsAPIError, match="missing_record_id"):
            client.create_record(
                staff_id=11,
                services=[22],
                datetime="2026-05-22T10:00:00",
                client_phone="79991234567",
                client_name="Anna",
            )
        yc.reset_yclients_client()
