"""Tests for the Ayla billing/payout client (C2/C3, frozen contracts)."""

from __future__ import annotations

import httpx
import pytest

from apps.integrations.ayla.billing_client import (
    AylaBillingClient,
    BillingAuthError,
    BillingConfigError,
    BillingNotFoundError,
    BillingTransportError,
)

_BASE = "https://ayla.test"
_TOKEN = "TOKEN-SENTINEL"  # noqa: S105  # pragma: allowlist secret
_SID = "7c2d8e1f-0a5c-4c3a-9e1b-4d52f8eb3a17"

_STATUS_PAYLOAD = {
    "data": {
        "specialist_id": _SID,
        "subscription": {
            "status": "active",
            "tariff": "solo",
            "current_period_end": "2026-08-31",
            "next_charge": {
                "subscription_amount": "690.00",
                "fees_amount": "270.00",
                "total_amount": "960.00",
                "date": "2026-08-01",
            },
        },
        "fees": {"pending_total": "270.00", "pending_count": 3},
        "last_invoice": {
            "id": "11111111-2222-3333-4444-555555555555",
            "amount": "960.00",
            "status": "paid",
            "paid_at": "2026-07-01T10:00:00Z",
        },
    }
}

_PAYOUT_PAYLOAD = {
    "data": {
        "pending_amount": "5730.00",
        "currency": "RUB",
        "expected_settlement_hint": "~следующий рабочий день",
        "items": [
            {
                "appointment_id": "b8d3e4f5-1c2d-4e6f-8a9b-c3d4e5f6a7b8",
                "completed_at": "2026-07-18T16:00:00Z",
                "amount": "2000.00",
                "platform_fee": "90.00",
                "specialist_income": "1910.00",
                "capture_state": "scheduled",
            }
        ],
    }
}


def _client(handler, **kwargs) -> AylaBillingClient:
    return AylaBillingClient(
        base_url=_BASE,
        token=_TOKEN,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


class TestBillingStatus:
    def test_happy_path_verbatim(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_STATUS_PAYLOAD)

        out = _client(handler).get_billing_status(specialist_id=_SID)

        assert seen["url"] == f"{_BASE}/api/v1/internal/billing/specialists/{_SID}/status/"
        assert seen["auth"] == f"Bearer {_TOKEN}"
        assert out == _STATUS_PAYLOAD["data"]  # verbatim pass-through

    def test_none_subscription_shape(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "specialist_id": _SID,
                        "subscription": {
                            "status": "none",
                            "tariff": None,
                            "current_period_end": None,
                            "next_charge": None,
                        },
                        "fees": {"pending_total": "0.00", "pending_count": 0},
                        "last_invoice": None,
                    }
                },
            )

        out = _client(handler).get_billing_status(specialist_id=_SID)

        assert out["subscription"]["status"] == "none"
        assert out["last_invoice"] is None


class TestPayoutPreview:
    def test_happy_path_verbatim(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=_PAYOUT_PAYLOAD)

        out = _client(handler).get_payout_preview(specialist_id=_SID)

        assert seen["url"] == f"{_BASE}/api/v1/internal/specialists/{_SID}/payout-preview/"
        assert out == _PAYOUT_PAYLOAD["data"]

    def test_empty_payout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "pending_amount": "0.00",
                        "currency": "RUB",
                        "expected_settlement_hint": None,
                        "items": [],
                    }
                },
            )

        out = _client(handler).get_payout_preview(specialist_id=_SID)

        assert out["pending_amount"] == "0.00"
        assert out["items"] == []


class TestErrors:
    def test_404_maps_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "SPECIALIST_NOT_FOUND"}})

        with pytest.raises(BillingNotFoundError):
            _client(handler).get_billing_status(specialist_id=_SID)

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_error(self, status: int) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={})

        with pytest.raises(BillingAuthError):
            _client(handler).get_payout_preview(specialist_id=_SID)

    def test_5xx_retried_then_raises(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(500, json={})

        with pytest.raises(BillingTransportError):
            _client(handler, retries=2).get_billing_status(specialist_id=_SID)

        assert calls["n"] == 2

    def test_missing_token_never_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={})

        client = AylaBillingClient(
            base_url=_BASE,
            token="",
            retries=3,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(BillingConfigError):
            client.get_billing_status(specialist_id=_SID)
        assert calls["n"] == 0


class TestCardSetup:
    def test_happy_path_body_and_url(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["method"] = request.method
            seen["body"] = request.content.decode()
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(
                200, json={"data": {"confirmation_url": "https://pay.test/bind/1"}}
            )

        out = _client(handler).card_setup(
            specialist_id=_SID, tariff="solo", return_url="https://miniapp.test/return"
        )

        assert seen["method"] == "POST"
        assert seen["url"] == f"{_BASE}/api/v1/internal/billing/specialists/{_SID}/card-setup/"
        assert seen["auth"] == f"Bearer {_TOKEN}"
        assert '"tariff": "solo"' in seen["body"] or '"tariff":"solo"' in seen["body"]
        assert "https://miniapp.test/return" in seen["body"]
        assert out == {"confirmation_url": "https://pay.test/bind/1"}

    def test_404_maps_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "SPECIALIST_NOT_FOUND"}})

        with pytest.raises(BillingNotFoundError):
            _client(handler).card_setup(
                specialist_id=_SID, tariff="solo", return_url="https://x.test/"
            )

    def test_400_maps_client_error(self) -> None:
        from apps.integrations.ayla.billing_client import BillingClientError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"code": "VALIDATION_ERROR"}})

        with pytest.raises(BillingClientError):
            _client(handler).card_setup(
                specialist_id=_SID, tariff="salon", return_url="https://x.test/"
            )

    def test_503_retried_then_raises(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503, json={})

        with pytest.raises(BillingTransportError):
            _client(handler, retries=2).card_setup(
                specialist_id=_SID, tariff="solo", return_url="https://x.test/"
            )
        assert calls["n"] == 2


class TestPayDebt:
    def test_happy_path_verbatim(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(
                200,
                json={
                    "data": {
                        "payment_id": "p-1",
                        "invoice_id": "i-1",
                        "confirmation_url": "https://pay.test/debt/1",
                        "amount": "960.00",
                        "status": "pending",
                        "subscription_status": "past_due",
                    }
                },
            )

        out = _client(handler).pay_debt(
            specialist_id=_SID, return_url="https://miniapp.test/return"
        )

        assert seen["url"] == f"{_BASE}/api/v1/internal/billing/specialists/{_SID}/pay-debt/"
        assert "https://miniapp.test/return" in seen["body"]
        assert out["payment_id"] == "p-1"
        assert out["amount"] == "960.00"
        assert out["subscription_status"] == "past_due"

    def test_return_url_optional_passed_verbatim(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"payment_id": "p"}})

        out = _client(handler).pay_debt(specialist_id=_SID)
        assert out == {"payment_id": "p"}

    def test_409_carries_no_debt_code(self) -> None:
        from apps.integrations.ayla.billing_client import BillingConflictError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": {"code": "NO_DEBT"}})

        with pytest.raises(BillingConflictError) as exc_info:
            _client(handler).pay_debt(specialist_id=_SID)

        assert exc_info.value.code == "NO_DEBT"

    def test_404_maps_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": {"code": "SPECIALIST_NOT_FOUND"}})

        with pytest.raises(BillingNotFoundError):
            _client(handler).pay_debt(specialist_id=_SID)
