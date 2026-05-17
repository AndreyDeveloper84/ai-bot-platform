"""YooKassa client tests (DRF-843 / Phase 1 / B7).

Network is intercepted via ``unittest.mock.patch`` on the session's
``post``/``get``. We assert:

* Test-mode short-circuit returns a stub URL with NO HTTP call.
* Live mode sends Idempotence-Key header + HTTP basic auth.
* 5xx → ``YooKassaUnavailableError`` AND trips the breaker.
* 4xx → ``YooKassaAPIError`` and does NOT trip the breaker.
* Circuit breaker opens after 5 failures.
* Singleton factory + reset.
* Secret key is masked in __repr__.
* Amount formatting always 2 decimals.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from apps.integrations.yookassa import client as yk_module
from apps.integrations.yookassa.client import (
    YooKassaAPIError,
    YooKassaClient,
    YooKassaUnavailableError,
    _format_amount,
    get_yookassa_client,
    reset_yookassa_client,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test gets a fresh module-level singleton."""
    reset_yookassa_client()
    yield
    reset_yookassa_client()


# ─── construction / repr ──────────────────────────────────────────────────


class TestClientConstruction:
    def test_test_mode_with_empty_creds_constructs_ok(self) -> None:
        """In test mode the client must boot even with empty creds —
        tests + non-payments tenants rely on this."""
        client = YooKassaClient(
            shop_id="",
            secret_key="",
            return_url="https://example.com",
            test_mode=True,
        )
        assert client.test_mode is True

    def test_repr_does_not_leak_secret(self) -> None:
        client = YooKassaClient(
            shop_id="shop123",
            secret_key="masked-stub-do-not-log",  # pragma: allowlist secret
            return_url="https://example.com",
            test_mode=True,
        )
        r = repr(client)
        assert "masked-stub-do-not-log" not in r
        assert "shop123" in r


class TestFormatAmount:
    def test_two_decimals_always(self) -> None:
        assert _format_amount(Decimal("1500")) == "1500.00"
        assert _format_amount(Decimal("1500.5")) == "1500.50"
        # ROUND_HALF_EVEN: .005 → .00 (last kept digit is 0, even); .015 → .02.
        assert _format_amount(Decimal("1500.015")) == "1500.02"

    def test_zero(self) -> None:
        assert _format_amount(Decimal("0")) == "0.00"


# ─── test-mode short-circuit ──────────────────────────────────────────────


class TestTestMode:
    def test_create_payment_returns_stub_no_http(self) -> None:
        client = YooKassaClient(
            shop_id="shop",
            secret_key="s",
            return_url="https://example.com/return",
            test_mode=True,
        )
        order_id = uuid4()
        with patch.object(client._session, "post") as mock_post:
            result = client.create_payment(
                amount_rub=Decimal("2000"),
                description="Cert",
                order_id=order_id,
                idempotence_key=uuid4(),
            )
        assert mock_post.call_count == 0
        assert result.test is True
        assert result.status == "pending"
        assert str(order_id) in result.checkout_url
        assert result.payment_id.startswith("test-")

    def test_get_payment_stub_no_http(self) -> None:
        client = YooKassaClient(
            shop_id="",
            secret_key="",
            return_url="https://example.com",
            test_mode=True,
        )
        with patch.object(client._session, "get") as mock_get:
            payload = client.get_payment("any_id")
        assert mock_get.call_count == 0
        assert payload["test"] is True


# ─── live-mode HTTP ───────────────────────────────────────────────────────


def _make_client(test_mode: bool = False) -> YooKassaClient:
    return YooKassaClient(
        shop_id="shop123",
        secret_key="stub-test-key",  # pragma: allowlist secret
        return_url="https://example.com/return",
        test_mode=test_mode,
        base_url="https://yookassa.test/v3",
    )


def _mock_response(*, status: int = 200, json_body=None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body if json_body is not None else {}
    resp.text = text or (str(json_body) if json_body else "")
    return resp


class TestLiveCreatePayment:
    def test_sends_idempotence_key_header(self) -> None:
        client = _make_client(test_mode=False)
        idem = uuid4()
        order_id = uuid4()
        body = {
            "id": "pay-uuid",
            "status": "pending",
            "confirmation": {"confirmation_url": "https://yoomoney/checkout/xyz"},
        }
        with patch.object(
            client._session,
            "post",
            return_value=_mock_response(status=200, json_body=body),
        ) as mock_post:
            result = client.create_payment(
                amount_rub=Decimal("1500"),
                description="cert",
                order_id=order_id,
                idempotence_key=idem,
            )

        assert result.payment_id == "pay-uuid"
        assert result.checkout_url == "https://yoomoney/checkout/xyz"
        assert result.test is False

        # Confirm the headers + body shape we sent.
        call_args = mock_post.call_args
        sent_headers = call_args.kwargs["headers"]
        assert sent_headers["Idempotence-Key"] == str(idem)
        sent_body = call_args.kwargs["json"]
        assert sent_body["amount"]["value"] == "1500.00"
        assert sent_body["amount"]["currency"] == "RUB"
        assert sent_body["capture"] is True
        assert sent_body["confirmation"]["type"] == "redirect"
        assert sent_body["metadata"]["order_id"] == str(order_id)

    def test_basic_auth_is_set(self) -> None:
        client = _make_client(test_mode=False)
        assert client._session.auth == ("shop123", "stub-test-key")

    def test_5xx_raises_unavailable_and_trips_breaker(self) -> None:
        client = _make_client(test_mode=False)
        with patch.object(
            client._session,
            "post",
            return_value=_mock_response(status=503, text="upstream down"),
        ):
            with pytest.raises(YooKassaUnavailableError):
                client.create_payment(
                    amount_rub=Decimal("1000"),
                    description="x",
                    order_id=uuid4(),
                    idempotence_key=uuid4(),
                )
        assert len(client._circuit.failures) == 1

    def test_4xx_raises_api_error_and_does_not_trip_breaker(self) -> None:
        client = _make_client(test_mode=False)
        with patch.object(
            client._session,
            "post",
            return_value=_mock_response(status=400, text='{"type":"error"}'),
        ):
            with pytest.raises(YooKassaAPIError):
                client.create_payment(
                    amount_rub=Decimal("1000"),
                    description="x",
                    order_id=uuid4(),
                    idempotence_key=uuid4(),
                )
        # 4xx is a caller error — breaker stays cool.
        assert client._circuit.failures == []

    def test_invalid_json_response(self) -> None:
        client = _make_client(test_mode=False)
        resp = _mock_response(status=200, text="not-json")
        resp.json.side_effect = ValueError("decode")
        with patch.object(client._session, "post", return_value=resp):
            with pytest.raises(YooKassaAPIError, match="invalid_json"):
                client.create_payment(
                    amount_rub=Decimal("1000"),
                    description="x",
                    order_id=uuid4(),
                    idempotence_key=uuid4(),
                )

    def test_missing_confirmation_url(self) -> None:
        client = _make_client(test_mode=False)
        body = {"id": "pid", "status": "pending"}  # no confirmation
        with patch.object(
            client._session,
            "post",
            return_value=_mock_response(status=200, json_body=body),
        ):
            with pytest.raises(YooKassaAPIError, match="missing_confirmation_url"):
                client.create_payment(
                    amount_rub=Decimal("1000"),
                    description="x",
                    order_id=uuid4(),
                    idempotence_key=uuid4(),
                )

    def test_live_mode_empty_shop_id_raises(self) -> None:
        client = YooKassaClient(
            shop_id="",
            secret_key="s",
            return_url="https://example.com",
            test_mode=False,
        )
        with pytest.raises(YooKassaAPIError, match="YOOKASSA_SHOP_ID"):
            client.create_payment(
                amount_rub=Decimal("1000"),
                description="x",
                order_id=uuid4(),
                idempotence_key=uuid4(),
            )

    def test_live_mode_empty_secret_raises(self) -> None:
        client = YooKassaClient(
            shop_id="shop",
            secret_key="",
            return_url="https://example.com",
            test_mode=False,
        )
        with pytest.raises(YooKassaAPIError, match="YOOKASSA_SECRET_KEY"):
            client.create_payment(
                amount_rub=Decimal("1000"),
                description="x",
                order_id=uuid4(),
                idempotence_key=uuid4(),
            )


class TestCircuitBreaker:
    def test_breaker_opens_after_threshold(self) -> None:
        client = _make_client(test_mode=False)
        with patch.object(
            client._session,
            "post",
            return_value=_mock_response(status=503, text="x"),
        ):
            for _ in range(yk_module.CIRCUIT_FAILURE_THRESHOLD):
                with pytest.raises(YooKassaUnavailableError):
                    client.create_payment(
                        amount_rub=Decimal("1000"),
                        description="x",
                        order_id=uuid4(),
                        idempotence_key=uuid4(),
                    )
        # 6th call: circuit is open, no HTTP made.
        with patch.object(client._session, "post") as mock_post:
            with pytest.raises(YooKassaUnavailableError, match="circuit_open"):
                client.create_payment(
                    amount_rub=Decimal("1000"),
                    description="x",
                    order_id=uuid4(),
                    idempotence_key=uuid4(),
                )
            assert mock_post.call_count == 0


# ─── singleton ────────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_yookassa_client_returns_same_instance(self, settings) -> None:
        settings.YOOKASSA_SHOP_ID = "s"
        settings.YOOKASSA_SECRET_KEY = "k"
        settings.YOOKASSA_RETURN_URL = "https://example.com"
        settings.YOOKASSA_TEST_MODE = True
        reset_yookassa_client()
        a = get_yookassa_client()
        b = get_yookassa_client()
        assert a is b

    def test_reset_yookassa_client_drops_singleton(self, settings) -> None:
        settings.YOOKASSA_TEST_MODE = True
        a = get_yookassa_client()
        reset_yookassa_client()
        b = get_yookassa_client()
        assert a is not b
