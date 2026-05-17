"""YooKassa webhook receiver tests (DRF-843 / Phase 1 / B7).

Security pipeline coverage:

* Valid IP + valid signature + payment.succeeded → 200, Order flipped
  to paid, fulfilment task enqueued.
* Bad IP → 403, NO audit row (silent rejection by design).
* Bad signature → 200, audit row 'yookassa.bad_signature', no state
  change.
* Empty secret → audit + 200 (fail-closed).
* Duplicate event_id → 200, no double-process, audit 'duplicate'.
* payment.canceled → 200, Order flipped to cancelled.
* Unknown event_type → 200, no state change, audit row.
* Malformed JSON → 200, audit row.
* Order not found → 200, processed=True, audit row.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Generator
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.test import Client
from django.test.utils import override_settings

from apps.audit.models import AuditLog
from apps.identity.models import BotUser
from apps.orders.models import Order, PaymentEvent
from apps.tenancy.models import Tenant


_URL = "/api/v1/yookassa/webhook/"
_SECRET = "test_secret_AB12"  # pragma: allowlist secret
# An IP that matches the YooKassa allowlist for tests. 185.71.76.5 is
# inside 185.71.76.0/27.
_GOOD_IP = "185.71.76.5"
_BAD_IP = "8.8.8.8"


pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _yookassa_secret_setting() -> Generator[None, None, None]:
    with override_settings(YOOKASSA_SECRET_KEY=_SECRET):
        yield


@pytest.fixture(autouse=True)
def _scope_off(settings) -> Generator[None, None, None]:
    """Webhook is an external entry point — STRICT mode would block
    pre-arrange ORM writes that happen outside any tenant_scope."""
    settings.STRICT_TENANT_SCOPE = "audit"
    yield


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="salon-b7", name="Salon B7")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="user-b7",
        chat_id="chat-b7",
        phone="79991234567",
        client_name="Buyer",
    )


@pytest.fixture
def order(tenant: Tenant, bot_user: BotUser) -> Order:
    return Order.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        kind=Order.Kind.CERTIFICATE,
        amount_rub=Decimal("2500"),
        external_payment_id="payment-xyz-123",
        checkout_url="https://yoomoney.test/checkout/abc",
        status=Order.Status.AWAITING_PAYMENT,
    )


@pytest.fixture
def client() -> Client:
    return Client(REMOTE_ADDR=_GOOD_IP)


def _build_payload(
    *,
    event_type: str = "payment.succeeded",
    payment_id: str = "payment-xyz-123",
    event_id: str | None = None,
    amount: str = "2500.00",
) -> dict[str, object]:
    return {
        "event_id": event_id or f"evt-{payment_id}-{event_type}",
        "event": event_type,
        "object": {
            "id": payment_id,
            "status": event_type.split(".")[1],
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": {"order_id": "ignored-by-handler"},
        },
    }


def _sign(body: bytes) -> str:
    digest = hmac.new(_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return "sha256=" + digest


def _post(
    client: Client,
    payload: dict[str, object],
    *,
    sig: str | None = None,
    remote: str = _GOOD_IP,
):
    body = json.dumps(payload).encode("utf-8")
    signature = sig if sig is not None else _sign(body)
    return client.post(
        _URL,
        data=body,
        content_type="application/json",
        HTTP_CONTENT_HMAC=signature,
        REMOTE_ADDR=remote,
    )


def _audit_actions() -> list[str]:
    return list(AuditLog.all_tenants.values_list("action", flat=True))


# ─── IP gate ──────────────────────────────────────────────────────────────


class TestIpGate:
    def test_bad_ip_returns_403_no_audit(self, client: Client, order: Order) -> None:
        before = AuditLog.all_tenants.count()
        resp = _post(client, _build_payload(), remote=_BAD_IP)
        assert resp.status_code == 403
        # Order untouched.
        order.refresh_from_db()
        assert order.status == Order.Status.AWAITING_PAYMENT
        # No audit row written for IP rejection — per spec.
        assert AuditLog.all_tenants.count() == before


# ─── signature gate ───────────────────────────────────────────────────────


class TestSignatureGate:
    def test_bad_signature_returns_200_with_audit(
        self,
        client: Client,
        order: Order,
    ) -> None:
        resp = _post(client, _build_payload(), sig="sha256=" + "0" * 64)
        assert resp.status_code == 200
        actions = _audit_actions()
        assert "yookassa.bad_signature" in actions
        order.refresh_from_db()
        assert order.status == Order.Status.AWAITING_PAYMENT

    def test_missing_signature_returns_200_with_audit(
        self,
        client: Client,
        order: Order,
    ) -> None:
        # Send body but no Content-HMAC header.
        body = json.dumps(_build_payload()).encode("utf-8")
        resp = client.post(
            _URL,
            data=body,
            content_type="application/json",
            REMOTE_ADDR=_GOOD_IP,
        )
        assert resp.status_code == 200
        assert "yookassa.bad_signature" in _audit_actions()

    def test_empty_secret_fails_closed(
        self,
        client: Client,
        order: Order,
        settings,
    ) -> None:
        settings.YOOKASSA_SECRET_KEY = ""
        resp = _post(client, _build_payload())
        assert resp.status_code == 200
        # The audit slug is 'bad_signature' because we treat empty secret
        # as a failed verification (fail-closed posture).
        assert "yookassa.bad_signature" in _audit_actions()
        order.refresh_from_db()
        assert order.status == Order.Status.AWAITING_PAYMENT

    def test_audit_payload_does_not_contain_secret(
        self,
        client: Client,
        order: Order,
    ) -> None:
        _post(client, _build_payload(), sig="sha256=" + "f" * 64)
        for row in AuditLog.all_tenants.all():
            assert _SECRET not in str(row.payload)


# ─── happy paths ──────────────────────────────────────────────────────────


class TestPaymentSucceeded:
    def test_flips_order_to_paid_and_enqueues(
        self,
        client: Client,
        order: Order,
    ) -> None:
        with patch("apps.orders.tasks.fulfill_paid_certificate") as task_mock:
            resp = _post(client, _build_payload())

        assert resp.status_code == 200
        order.refresh_from_db()
        assert order.status == Order.Status.PAID
        assert order.paid_at is not None
        task_mock.delay.assert_called_once_with(str(order.id))
        assert "yookassa.webhook.processed" in _audit_actions()

    def test_paymentevent_row_is_persisted(
        self,
        client: Client,
        order: Order,
    ) -> None:
        payload = _build_payload()
        with patch("apps.orders.tasks.fulfill_paid_certificate"):
            _post(client, payload)
        rows = list(PaymentEvent.objects.all())
        assert len(rows) == 1
        assert rows[0].external_event_id == payload["event_id"]
        assert rows[0].event_type == "payment.succeeded"
        assert rows[0].order_id == order.id
        assert rows[0].processed is True

    def test_unknown_order_records_event_but_no_state_change(
        self,
        client: Client,
    ) -> None:
        payload = _build_payload(payment_id="not-in-db")
        with patch("apps.orders.tasks.fulfill_paid_certificate") as task_mock:
            resp = _post(client, payload)
        assert resp.status_code == 200
        task_mock.delay.assert_not_called()
        assert PaymentEvent.objects.filter(
            external_event_id=payload["event_id"],
        ).exists()
        assert "yookassa.webhook.no_order" in _audit_actions()


class TestPaymentCanceled:
    def test_flips_order_to_cancelled(
        self,
        client: Client,
        order: Order,
    ) -> None:
        with patch("apps.orders.tasks.notify_cancelled_certificate") as task_mock:
            resp = _post(client, _build_payload(event_type="payment.canceled"))
        assert resp.status_code == 200
        order.refresh_from_db()
        assert order.status == Order.Status.CANCELLED
        task_mock.delay.assert_called_once_with(str(order.id))


# ─── idempotency ──────────────────────────────────────────────────────────


class TestIdempotency:
    def test_duplicate_event_id_short_circuits(
        self,
        client: Client,
        order: Order,
    ) -> None:
        payload = _build_payload()
        with patch("apps.orders.tasks.fulfill_paid_certificate") as task_mock:
            r1 = _post(client, payload)
            r2 = _post(client, payload)
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Only one task dispatch, one PaymentEvent row.
        assert task_mock.delay.call_count == 1
        assert PaymentEvent.objects.filter(external_event_id=payload["event_id"]).count() == 1
        assert "yookassa.webhook.duplicate" in _audit_actions()


# ─── unknown event types ──────────────────────────────────────────────────


class TestUnknownEvent:
    def test_unknown_event_records_but_no_side_effect(
        self,
        client: Client,
        order: Order,
    ) -> None:
        resp = _post(client, _build_payload(event_type="refund.succeeded"))
        assert resp.status_code == 200
        order.refresh_from_db()
        assert order.status == Order.Status.AWAITING_PAYMENT
        assert "yookassa.webhook.unknown_event" in _audit_actions()


# ─── malformed body ───────────────────────────────────────────────────────


class TestMalformedBody:
    def test_invalid_json_returns_200_with_audit(
        self,
        client: Client,
    ) -> None:
        body = b"{not-json"
        sig = _sign(body)
        resp = client.post(
            _URL,
            data=body,
            content_type="application/json",
            HTTP_CONTENT_HMAC=sig,
            REMOTE_ADDR=_GOOD_IP,
        )
        assert resp.status_code == 200
        assert "yookassa.webhook.malformed" in _audit_actions()

    def test_non_object_body_returns_200(self, client: Client) -> None:
        body = b'["list", "not", "object"]'
        sig = _sign(body)
        resp = client.post(
            _URL,
            data=body,
            content_type="application/json",
            HTTP_CONTENT_HMAC=sig,
            REMOTE_ADDR=_GOOD_IP,
        )
        assert resp.status_code == 200
        assert "yookassa.webhook.malformed" in _audit_actions()

    def test_missing_event_id_returns_200(self, client: Client) -> None:
        payload: dict[str, object] = {"event": "payment.succeeded", "object": {"id": "x"}}
        resp = _post(client, payload)
        assert resp.status_code == 200
        assert "yookassa.webhook.malformed" in _audit_actions()
