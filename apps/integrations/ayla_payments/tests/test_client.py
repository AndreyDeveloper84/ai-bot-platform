"""Tests for the Ayla payments HTTP client (Phase 0 / #427).

Mirrors the YooKassa client test shape — the Ayla client implements
the same external contract (test-mode short-circuit, retry+circuit,
idempotence header) against a different upstream. Tests rely on
``AYLA_PAYMENTS_TEST_MODE = True`` plus mocked HTTP for the live
paths. No live network in tests.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
import requests  # type: ignore[import-untyped]

from apps.integrations.ayla_payments import (
    AylaPaymentsAPIError,
    AylaPaymentsClient,
    AylaPaymentsUnavailableError,
    get_ayla_payments_client,
    reset_ayla_payments_client,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_ayla_payments_client()
    yield
    reset_ayla_payments_client()


def _make_response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.text = text or ""
    return response


class TestTestModeShortCircuit:
    def test_test_mode_returns_stub_url_without_http(self) -> None:
        """test_mode=True returns a stub CreatePaymentResult, never touches network."""
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="",
            test_mode=True,
        )
        idem = uuid4()
        with patch.object(client._session, "request") as mock_request:
            result = client.create_payment(
                amount_rub=Decimal("1500.00"),
                description="Сертификат",
                idempotence_key=idem,
            )
        mock_request.assert_not_called()
        assert result.test is True
        assert result.checkout_url.startswith("https://yoomoney.test/checkout/")
        assert result.payment_id.startswith("test-")
        # The stub payment id includes the idempotence key so each test
        # call gets a distinct, traceable URL.
        assert str(idem) in result.checkout_url


class TestHappyPath:
    def test_returns_payment_id_and_checkout_url(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="bearer-abc",
            test_mode=False,
        )
        upstream_body = {
            "payment_id": "pay_01HXXXXXXXXXXXXXXX",
            "checkout_url": "https://yoomoney.ru/checkout/123",
            "status": "pending",
        }
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(200, upstream_body),
        ):
            result = client.create_payment(
                amount_rub=Decimal("2000"),
                description="Сертификат",
                idempotence_key=uuid4(),
            )
        assert result.payment_id == "pay_01HXXXXXXXXXXXXXXX"
        assert result.checkout_url == "https://yoomoney.ru/checkout/123"
        assert result.status == "pending"
        assert result.test is False

    def test_sends_bearer_token_in_authorization_header(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="bearer-secret-token",
            test_mode=False,
        )
        upstream_body = {
            "payment_id": "p",
            "checkout_url": "https://x",
            "status": "pending",
        }
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(200, upstream_body),
        ) as mock_post:
            client.create_payment(
                amount_rub=Decimal("1500"),
                description="x",
                idempotence_key=uuid4(),
            )
        _, kwargs = mock_post.call_args
        headers = kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer bearer-secret-token"

    def test_sends_idempotence_key_header(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        idem = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        upstream_body = {
            "payment_id": "p",
            "checkout_url": "https://x",
            "status": "pending",
        }
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(200, upstream_body),
        ) as mock_post:
            client.create_payment(
                amount_rub=Decimal("1500"),
                description="x",
                idempotence_key=idem,
            )
        _, kwargs = mock_post.call_args
        headers = kwargs.get("headers") or {}
        assert headers.get("Idempotence-Key") == str(idem)

    def test_post_body_includes_amount_description_recipient(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        upstream_body = {
            "payment_id": "p",
            "checkout_url": "https://x",
            "status": "pending",
        }
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(200, upstream_body),
        ) as mock_post:
            client.create_payment(
                amount_rub=Decimal("2500.50"),
                description="Сертификат для Ольги",
                idempotence_key=uuid4(),
                recipient_name="Ольга",
                buyer_email="buyer@example.com",
                kind="certificate",
            )
        _, kwargs = mock_post.call_args
        body = kwargs.get("json") or {}
        assert body["amount_rub"] == "2500.50"
        assert body["description"] == "Сертификат для Ольги"
        assert body["recipient_name"] == "Ольга"
        assert body["buyer_email"] == "buyer@example.com"
        assert body["kind"] == "certificate"


class TestErrorMapping:
    def test_5xx_raises_unavailable(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(503, {}, text="upstream down"),
        ):
            with pytest.raises(AylaPaymentsUnavailableError):
                client.create_payment(
                    amount_rub=Decimal("1500"),
                    description="x",
                    idempotence_key=uuid4(),
                )

    def test_4xx_raises_api_error(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(
                400, {"error": "bad_amount"}, text='{"error":"bad_amount"}'
            ),
        ):
            with pytest.raises(AylaPaymentsAPIError) as excinfo:
                client.create_payment(
                    amount_rub=Decimal("1500"),
                    description="x",
                    idempotence_key=uuid4(),
                )
        # AylaPaymentsUnavailableError is a subclass of AylaPaymentsAPIError
        # in the YooKassa lineage; assert we got the non-unavailable variant.
        assert not isinstance(excinfo.value, AylaPaymentsUnavailableError)

    def test_timeout_raises_unavailable(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        with patch.object(
            client._session,
            "post",
            side_effect=requests.exceptions.Timeout("timed out"),
        ):
            with pytest.raises(AylaPaymentsUnavailableError):
                client.create_payment(
                    amount_rub=Decimal("1500"),
                    description="x",
                    idempotence_key=uuid4(),
                )

    def test_connection_error_raises_unavailable(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        with patch.object(
            client._session,
            "post",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(AylaPaymentsUnavailableError):
                client.create_payment(
                    amount_rub=Decimal("1500"),
                    description="x",
                    idempotence_key=uuid4(),
                )

    def test_malformed_json_raises_api_error(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        bad_response = MagicMock(spec=requests.Response)
        bad_response.status_code = 200
        bad_response.json.side_effect = ValueError("no json")
        bad_response.text = "<html>oops</html>"
        with patch.object(client._session, "post", return_value=bad_response):
            with pytest.raises(AylaPaymentsAPIError):
                client.create_payment(
                    amount_rub=Decimal("1500"),
                    description="x",
                    idempotence_key=uuid4(),
                )

    def test_missing_payment_id_raises_api_error(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(200, {"checkout_url": "https://x", "status": "pending"}),
        ):
            with pytest.raises(AylaPaymentsAPIError):
                client.create_payment(
                    amount_rub=Decimal("1500"),
                    description="x",
                    idempotence_key=uuid4(),
                )


class TestCircuitBreaker:
    def test_circuit_opens_after_threshold_failures(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="t",
            test_mode=False,
        )
        with patch.object(
            client._session,
            "post",
            return_value=_make_response(503, {}, text="down"),
        ):
            # Trip the breaker with N consecutive 5xx.
            for _ in range(5):
                with pytest.raises(AylaPaymentsUnavailableError):
                    client.create_payment(
                        amount_rub=Decimal("1500"),
                        description="x",
                        idempotence_key=uuid4(),
                    )

        # Subsequent call short-circuits with breaker_open BEFORE the
        # HTTP call. We assert by patching post to raise — if the breaker
        # is open, post is never called and the raise never fires.
        with patch.object(
            client._session,
            "post",
            side_effect=AssertionError("post should NOT be called when breaker is open"),
        ):
            with pytest.raises(AylaPaymentsUnavailableError) as excinfo:
                client.create_payment(
                    amount_rub=Decimal("1500"),
                    description="x",
                    idempotence_key=uuid4(),
                )
        assert "circuit_open" in str(excinfo.value)


class TestPreconditions:
    def test_live_mode_with_empty_base_url_raises_api_error(self) -> None:
        client = AylaPaymentsClient(
            base_url="",
            api_token="t",
            test_mode=False,
        )
        with pytest.raises(AylaPaymentsAPIError):
            client.create_payment(
                amount_rub=Decimal("1500"),
                description="x",
                idempotence_key=uuid4(),
            )

    def test_live_mode_with_empty_token_raises_api_error(self) -> None:
        client = AylaPaymentsClient(
            base_url="https://ayla.test",
            api_token="",
            test_mode=False,
        )
        with pytest.raises(AylaPaymentsAPIError):
            client.create_payment(
                amount_rub=Decimal("1500"),
                description="x",
                idempotence_key=uuid4(),
            )


class TestSingleton:
    def test_get_returns_same_instance(self, settings) -> None:
        settings.AYLA_PAYMENTS_TEST_MODE = True
        a = get_ayla_payments_client()
        b = get_ayla_payments_client()
        assert a is b

    def test_reset_creates_new_instance(self, settings) -> None:
        settings.AYLA_PAYMENTS_TEST_MODE = True
        a = get_ayla_payments_client()
        reset_ayla_payments_client()
        b = get_ayla_payments_client()
        assert a is not b
