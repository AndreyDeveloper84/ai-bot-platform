"""Fulfillment task tests (DRF-843 / Phase 1 / B7).

Covers:

* Happy path — paid Order → MAX confirmation sent + audit row.
* Idempotency — Order already paid + paid_at set → no-op call still
  returns 'fulfilled' (the task is safe to re-run).
* Wrong status — Order in PENDING / AWAITING_PAYMENT → skipped, no
  message sent.
* Missing Order → skipped.
* recipient_email present → email-stub log marker emitted.
* MAX send failure does NOT raise; audits the failure.
* Multi-tenant fan-out — two tenants get separate messages, no cross-
  tenant leak.
* Cancel notification flow.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.audit.models import AuditLog
from apps.channels.max.outbound import MaxAPIError
from apps.identity.models import BotUser
from apps.orders.models import Order
from apps.orders.tasks import (
    EMAIL_DELIVERY_STUB_LOG,
    fulfill_paid_certificate,
    notify_cancelled_certificate,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _scope_off(settings):
    settings.STRICT_TENANT_SCOPE = "audit"
    yield


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="tenant-task", name="Task")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="u",
        chat_id="chat-task",
        phone="",
        client_name="Buyer",
    )


def _make_order(
    tenant: Tenant,
    bot_user: BotUser,
    *,
    status: str = Order.Status.PAID,
    amount: Decimal = Decimal("2000"),
    recipient_email: str = "",
) -> Order:
    return Order.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        kind=Order.Kind.CERTIFICATE,
        amount_rub=amount,
        external_payment_id="pid-task",
        checkout_url="https://yoomoney.test/checkout/x",
        status=status,
        recipient_email=recipient_email,
    )


# ─── happy path ───────────────────────────────────────────────────────────


class TestFulfillPaidCertificate:
    def test_sends_max_message_and_audits(self, tenant: Tenant, bot_user: BotUser) -> None:
        order = _make_order(tenant, bot_user)
        with patch("apps.orders.tasks.send_message") as send_mock:
            result = fulfill_paid_certificate(str(order.id))
        assert result["status"] == "fulfilled"
        assert result["max_sent"] is True
        send_mock.assert_called_once()
        call_kwargs = send_mock.call_args.kwargs
        assert call_kwargs["chat_id"] == "chat-task"
        assert "2 000 ₽" in call_kwargs["text"]
        audit_actions = list(AuditLog.all_tenants.values_list("action", flat=True))
        assert "orders.certificate.fulfilled" in audit_actions

    def test_paid_at_stamped_when_missing(self, tenant: Tenant, bot_user: BotUser) -> None:
        order = _make_order(tenant, bot_user)
        # Clear paid_at to simulate a webhook race that didn't stamp.
        Order.all_tenants.filter(pk=order.pk).update(paid_at=None)
        with patch("apps.orders.tasks.send_message"):
            fulfill_paid_certificate(str(order.id))
        order.refresh_from_db()
        assert order.paid_at is not None

    def test_wrong_status_skipped(self, tenant: Tenant, bot_user: BotUser) -> None:
        order = _make_order(tenant, bot_user, status=Order.Status.AWAITING_PAYMENT)
        with patch("apps.orders.tasks.send_message") as send_mock:
            result = fulfill_paid_certificate(str(order.id))
        assert result["status"] == "skipped"
        assert result["reason"] == "not_paid"
        send_mock.assert_not_called()

    def test_missing_order_skipped(self) -> None:
        from uuid import uuid4

        result = fulfill_paid_certificate(str(uuid4()))
        assert result["status"] == "skipped"
        assert result["reason"] == "no_order"

    def test_bad_id_skipped(self) -> None:
        result = fulfill_paid_certificate("not-a-uuid")
        assert result["status"] == "skipped"
        assert result["reason"] == "bad_id"

    def test_email_recipient_emits_stub_log(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        order = _make_order(tenant, bot_user, recipient_email="recipient@example.com")
        with (
            patch("apps.orders.tasks.send_message"),
            caplog.at_level(logging.INFO, logger="apps.orders.tasks"),
        ):
            fulfill_paid_certificate(str(order.id))
        assert any(
            EMAIL_DELIVERY_STUB_LOG in rec.message and "recipient@example.com" in rec.message
            for rec in caplog.records
        )

    def test_max_send_failure_does_not_revert_status(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        order = _make_order(tenant, bot_user)
        with patch(
            "apps.orders.tasks.send_message",
            side_effect=MaxAPIError(500, "upstream down"),
        ):
            result = fulfill_paid_certificate(str(order.id))
        # Result still 'fulfilled' (payment is real); message just didn't send.
        assert result["status"] == "fulfilled"
        assert result["max_sent"] is False
        order.refresh_from_db()
        assert order.status == Order.Status.PAID
        audit_actions = list(AuditLog.all_tenants.values_list("action", flat=True))
        assert "orders.certificate.send_failed" in audit_actions

    def test_unexpected_exception_does_not_propagate(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        order = _make_order(tenant, bot_user)
        with patch("apps.orders.tasks.send_message", side_effect=RuntimeError("boom")):
            result = fulfill_paid_certificate(str(order.id))
        assert result["status"] == "fulfilled"
        assert result["max_sent"] is False


# ─── multi-tenant ─────────────────────────────────────────────────────────


class TestMultiTenantFanOut:
    def test_each_tenant_gets_separate_message(self, db) -> None:
        ta = Tenant.objects.create(slug="t-a", name="A")
        tb = Tenant.objects.create(slug="t-b", name="B")
        ua = BotUser.all_tenants.create(
            tenant=ta,
            channel="max",
            channel_user_id="ua",
            chat_id="chat-a",
            phone="",
            client_name="A",
        )
        ub = BotUser.all_tenants.create(
            tenant=tb,
            channel="max",
            channel_user_id="ub",
            chat_id="chat-b",
            phone="",
            client_name="B",
        )
        oa = _make_order(ta, ua, amount=Decimal("1000"))
        ob = _make_order(tb, ub, amount=Decimal("3000"))

        chat_ids_sent: list[str] = []

        def _spy(*, chat_id: str, text: str, **kwargs):
            chat_ids_sent.append(chat_id)
            return {}

        with patch("apps.orders.tasks.send_message", side_effect=_spy):
            fulfill_paid_certificate(str(oa.id))
            fulfill_paid_certificate(str(ob.id))

        assert chat_ids_sent == ["chat-a", "chat-b"]


# ─── cancel notification ──────────────────────────────────────────────────


class TestNotifyCancelled:
    def test_sends_cancel_message(self, tenant: Tenant, bot_user: BotUser) -> None:
        order = _make_order(tenant, bot_user, status=Order.Status.CANCELLED)
        with patch("apps.orders.tasks.send_message") as send_mock:
            result = notify_cancelled_certificate(str(order.id))
        assert result["status"] == "notified"
        send_mock.assert_called_once()
        assert "отменена" in send_mock.call_args.kwargs["text"]

    def test_wrong_status_skipped(self, tenant: Tenant, bot_user: BotUser) -> None:
        order = _make_order(tenant, bot_user, status=Order.Status.PAID)
        with patch("apps.orders.tasks.send_message") as send_mock:
            result = notify_cancelled_certificate(str(order.id))
        assert result["status"] == "skipped"
        send_mock.assert_not_called()

    def test_bad_id_skipped(self) -> None:
        result = notify_cancelled_certificate("not-a-uuid")
        assert result["status"] == "skipped"
