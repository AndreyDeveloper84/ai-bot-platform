"""buy_certificate tool tests (DRF-843 / Phase 1 / B7).

The tool itself is a sync ORM-writing handler — we drive it directly
with a stub YooKassa client. The skill-level orchestration (LLM
provider mocking, dispatcher) is covered separately.

Behaviour we assert:

1. Happy path in test-mode → Order created, status=awaiting_payment,
   keyboard returned with the stub URL, no HTTP made.
2. Amount too low → clarification, no Order created.
3. Amount too high → clarification, no Order created.
4. Non-numeric amount → clarification.
5. YooKassa failure → Order in failed state, handoff slug emitted.
6. Audit row 'booking.certificate_checkout_requested' on success.
7. Audit row 'booking.certificate_checkout_failed' on failure.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from apps.audit.models import AuditLog
from apps.identity.models import BotUser
from apps.integrations.yookassa import reset_yookassa_client
from apps.integrations.yookassa.client import (
    YooKassaUnavailableError,
)
from apps.orders.models import Order
from apps.skills.booking.tools import (
    CERTIFICATE_AMOUNT_MAX,
    CERTIFICATE_AMOUNT_MIN,
    buy_certificate,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _reset_yk_singleton(settings):
    settings.STRICT_TENANT_SCOPE = "audit"
    reset_yookassa_client()
    yield
    reset_yookassa_client()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="t-cert", name="Cert")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="u-cert",
        chat_id="chat-cert",
        phone="",
        client_name="Buyer",
    )


def _audit_actions() -> list[str]:
    return list(AuditLog.all_tenants.values_list("action", flat=True))


class TestHappyPath:
    def test_test_mode_returns_stub_url_no_http(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        settings.YOOKASSA_SHOP_ID = ""
        settings.YOOKASSA_SECRET_KEY = ""

        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": 2000},
            )

        assert result.error == ""
        assert result.certificate is not None
        assert result.certificate.ok is True
        assert result.certificate.amount_rub == Decimal("2000")
        # Stub URL contains the order id.
        assert result.certificate.order_id in result.certificate.checkout_url
        assert "yoomoney.test" in result.certificate.checkout_url
        # Keyboard returned.
        assert len(result.keyboard) == 1
        assert result.keyboard[0]["url"] == result.certificate.checkout_url
        # Order row persisted, status=awaiting_payment.
        order = Order.all_tenants.get(pk=result.certificate.order_id)
        assert order.status == Order.Status.AWAITING_PAYMENT
        assert order.amount_rub == Decimal("2000")
        assert order.bot_user_id == bot_user.id
        # Audit row written.
        assert "booking.certificate_checkout_requested" in _audit_actions()

    def test_recipient_name_persisted(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={
                    "amount_rub": 1500,
                    "recipient_name": "Olya",
                    "buyer_email": "buyer@example.com",
                },
            )
        assert result.certificate is not None
        order = Order.all_tenants.get(pk=result.certificate.order_id)
        assert order.recipient_name == "Olya"
        assert order.buyer_email == "buyer@example.com"
        assert "для Olya" in order.description


class TestAmountValidation:
    def test_too_low_clarification_no_order(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": 100},
            )
        assert result.error == "amount_out_of_range"
        assert result.certificate is not None and result.certificate.ok is False
        # No Order row written.
        assert Order.all_tenants.count() == 0

    def test_too_high_clarification_no_order(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": 200000},
            )
        assert result.error == "amount_out_of_range"
        assert Order.all_tenants.count() == 0

    def test_non_numeric_amount_clarification(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": "not-a-number"},
            )
        assert result.error == "amount_out_of_range"
        assert Order.all_tenants.count() == 0

    def test_boundary_min_accepted(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": int(CERTIFICATE_AMOUNT_MIN)},
            )
        assert result.error == ""

    def test_boundary_max_accepted(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": int(CERTIFICATE_AMOUNT_MAX)},
            )
        assert result.error == ""


class TestProviderFailure:
    def test_yookassa_unavailable_flips_order_to_failed(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        settings.YOOKASSA_TEST_MODE = True
        # Patch the client returned by the singleton so create_payment raises.
        with patch(
            "apps.integrations.yookassa.client.YooKassaClient.create_payment",
            side_effect=YooKassaUnavailableError("down"),
        ):
            with tenant_scope(tenant):
                result = buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": 2000},
                )
        assert result.error == "certificate_provider_failure"
        # One Order row, status=failed.
        orders = list(Order.all_tenants.all())
        assert len(orders) == 1
        assert orders[0].status == Order.Status.FAILED
        assert "booking.certificate_checkout_failed" in _audit_actions()


class TestNoSecretLeakInAudit:
    def test_audit_payload_contains_no_secret(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        secret = "masked-stub-do-not-log"  # pragma: allowlist secret
        settings.YOOKASSA_TEST_MODE = True
        settings.YOOKASSA_SECRET_KEY = secret
        with tenant_scope(tenant):
            buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": 2000},
            )
        for row in AuditLog.all_tenants.all():
            assert secret not in str(row.payload)
