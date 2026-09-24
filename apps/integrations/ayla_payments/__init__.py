"""Ayla djangoproject payments client (Phase 0 / #427).

Replaces direct YooKassa calls from bot-platform skills. Per ADR-0009
§Hard rule #5 (transactional tools = REST wrappers), bot-platform
never writes payment state directly — it calls Ayla djangoproject's
``POST /api/v1/payments/create`` and renders the returned checkout URL.

Ayla owns the YooKassa lifecycle, the Payment row, the receipt, and
the webhook.
"""

from apps.integrations.ayla_payments.client import (
    AylaPaymentsNotAuthorized,
    AylaPaymentsRetryRefused,
    RetryPaymentResult,
    AylaPaymentsAPIError,
    AylaPaymentsClient,
    AylaPaymentsUnavailableError,
    CreatePaymentResult,
    get_ayla_payments_client,
    reset_ayla_payments_client,
)

__all__ = [
    "AylaPaymentsAPIError",
    "AylaPaymentsClient",
    "AylaPaymentsNotAuthorized",
    "AylaPaymentsRetryRefused",
    "AylaPaymentsUnavailableError",
    "CreatePaymentResult",
    "RetryPaymentResult",
    "get_ayla_payments_client",
    "reset_ayla_payments_client",
]
