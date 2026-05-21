"""buy_certificate tool tests (DRF-843 → #427 Ayla payments refactor).

Phase 0 / #427: the tool now calls Ayla djangoproject's REST API
instead of YooKassa directly. bot-platform no longer writes an
Order row — Ayla is canonical SoR for payment lifecycle.

Behaviour we assert:

1. Happy path in test-mode → Ayla returns stub URL, keyboard rendered,
   no HTTP made.
2. Amount too low → clarification, no Ayla call.
3. Amount too high → clarification, no Ayla call.
4. Non-numeric amount → clarification.
5. Ayla failure → handoff slug emitted, audit row written.
6. Audit row 'booking.certificate_checkout_requested' on success.
7. Audit row 'booking.certificate_checkout_failed' on failure.
8. Regression — bot-platform writes NO Order row (ADR-0009 §Hard rule #1).
9. Recipient_name + buyer_email forwarded to the Ayla request body.
10. Audit payload does not leak the Ayla bearer token.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from apps.audit.models import AuditLog
from apps.identity.models import BotUser
from apps.integrations.ayla_payments import (
    AylaPaymentsUnavailableError,
    CreatePaymentResult,
    reset_ayla_payments_client,
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
def _reset_ayla_singleton(settings):
    settings.STRICT_TENANT_SCOPE = "audit"
    settings.AYLA_PAYMENTS_TEST_MODE = True
    settings.AYLA_BASE_URL = ""
    settings.AYLA_INTERNAL_API_TOKEN = ""
    reset_ayla_payments_client()
    yield
    reset_ayla_payments_client()


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
    ) -> None:
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
        # Stub URL points at the safe yoomoney.test sentinel host.
        assert "yoomoney.test" in result.certificate.checkout_url
        # The DTO's order_id field now carries Ayla's payment_id; in
        # test mode it has the "test-" prefix from the client stub.
        assert result.certificate.order_id.startswith("test-")
        # Keyboard returned with the URL.
        assert len(result.keyboard) == 1
        assert result.keyboard[0]["url"] == result.certificate.checkout_url
        # Audit row written.
        assert "booking.certificate_checkout_requested" in _audit_actions()

    def test_recipient_and_email_forwarded_to_ayla(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        # Patch the singleton's create_payment to inspect what we send.
        captured: dict[str, object] = {}

        def _fake_create(**kwargs):
            captured.update(kwargs)
            return CreatePaymentResult(
                payment_id="pay-stub",
                checkout_url="https://yoomoney.test/checkout/x",
                status="pending",
                test=True,
            )

        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=_fake_create,
        ):
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

        assert result.error == ""
        assert captured["recipient_name"] == "Olya"
        assert captured["buyer_email"] == "buyer@example.com"
        assert captured["kind"] == "certificate"
        # Description carries the recipient suffix for the YooKassa page.
        assert "для Olya" in str(captured["description"])


class TestAmountValidation:
    def test_too_low_clarification_no_ayla_call(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=AssertionError("Ayla MUST NOT be called for invalid amount"),
        ):
            with tenant_scope(tenant):
                result = buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": 100},
                )
        assert result.error == "amount_out_of_range"
        assert result.certificate is not None and result.certificate.ok is False

    def test_too_high_clarification_no_ayla_call(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=AssertionError("Ayla MUST NOT be called for invalid amount"),
        ):
            with tenant_scope(tenant):
                result = buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": 200000},
                )
        assert result.error == "amount_out_of_range"

    def test_non_numeric_amount_clarification(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=AssertionError("Ayla MUST NOT be called for invalid amount"),
        ):
            with tenant_scope(tenant):
                result = buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": "not-a-number"},
                )
        assert result.error == "amount_out_of_range"

    def test_boundary_min_accepted(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
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
    ) -> None:
        with tenant_scope(tenant):
            result = buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": int(CERTIFICATE_AMOUNT_MAX)},
            )
        assert result.error == ""


class TestProviderFailure:
    def test_ayla_unavailable_returns_handoff(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=AylaPaymentsUnavailableError("down"),
        ):
            with tenant_scope(tenant):
                result = buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": 2000},
                )
        assert result.error == "certificate_provider_failure"
        assert result.certificate is not None and result.certificate.ok is False
        assert "booking.certificate_checkout_failed" in _audit_actions()


class TestNoCanonicalStateWrite:
    """Regression — bot-platform must NOT write any Order row.

    Per ADR-0009 §Hard rule #1, Ayla owns the canonical Payment row.
    bot-platform's prior shape wrote an Order row mirroring Ayla's
    state; this PR removes that write. A future test added when a
    consumer is wired in (#443 payment.* consumer) verifies the
    converse — that Ayla → bot-platform events drive any
    bot-platform-side projection.
    """

    def test_happy_path_writes_no_order_row(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        with tenant_scope(tenant):
            buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": 2000},
            )
        assert Order.all_tenants.count() == 0

    def test_provider_failure_writes_no_order_row(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=AylaPaymentsUnavailableError("down"),
        ):
            with tenant_scope(tenant):
                buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": 2000},
                )
        assert Order.all_tenants.count() == 0


class TestNoTokenLeakInAudit:
    def test_audit_payload_contains_no_bearer_token(
        self,
        tenant: Tenant,
        bot_user: BotUser,
        settings,
    ) -> None:
        token = "ayla-bearer-stub-do-not-log"  # pragma: allowlist secret
        settings.AYLA_INTERNAL_API_TOKEN = token
        with tenant_scope(tenant):
            buy_certificate(
                tenant=tenant,
                bot_user=bot_user,
                arguments={"amount_rub": 2000},
            )
        for row in AuditLog.all_tenants.all():
            assert token not in str(row.payload)


class TestIdempotenceKey:
    """Pin that bot-platform generates a fresh idempotence_key per call.

    The key is what Ayla uses to dedupe duplicate POSTs (and what
    Ayla forwards to YooKassa). Tests in
    apps/integrations/ayla_payments/tests/test_client.py pin the
    on-the-wire header shape; this one pins the skill's contract
    of supplying a UUID each call so retries don't reuse keys.
    """

    def test_idempotence_key_passed_as_uuid_and_unique_per_call(
        self,
        tenant: Tenant,
        bot_user: BotUser,
    ) -> None:
        from uuid import UUID

        seen: list[UUID] = []

        def _fake_create(**kwargs):
            seen.append(kwargs["idempotence_key"])
            return CreatePaymentResult(
                payment_id="pay-stub",
                checkout_url="https://yoomoney.test/checkout/x",
                status="pending",
                test=True,
            )

        with patch(
            "apps.integrations.ayla_payments.client.AylaPaymentsClient.create_payment",
            side_effect=_fake_create,
        ):
            with tenant_scope(tenant):
                buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": 2000},
                )
                buy_certificate(
                    tenant=tenant,
                    bot_user=bot_user,
                    arguments={"amount_rub": 2000},
                )
        assert len(seen) == 2
        assert all(isinstance(k, UUID) for k in seen)
        assert seen[0] != seen[1]


# Use uuid4 import locally — keeps the test module self-contained vs
# pulling skills' internals.
_ = uuid4
