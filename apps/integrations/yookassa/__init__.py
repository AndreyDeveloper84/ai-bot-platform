"""YooKassa hosted-checkout integration (DRF-843 / Phase 1 / B7).

Powers the ``buy_certificate`` LLM tool. The client is a thin HTTP
wrapper around YooKassa's v3 /payments API; the webhook receiver
verifies an HMAC signature + an IP allowlist + an idempotency
ledger before calling the Celery fulfilment task.

### Test mode

``settings.YOOKASSA_TEST_MODE = True`` (the default) makes
:meth:`YooKassaClient.create_payment` return a stubbed checkout URL
WITHOUT making any HTTP call. Production deployments set this to
False explicitly. Tests rely on test-mode plus mocked HTTP for
network-touching paths.

### Single-tenant scope

Credentials come from ``settings.YOOKASSA_*``. Per-tenant credential
storage on :class:`apps.tenancy.models.Tenant` is a Phase 2 follow-up
— mirrors the same trajectory as YClients (B1).
"""

from apps.integrations.yookassa.client import (
    CreatePaymentResult,
    YooKassaAPIError,
    YooKassaClient,
    YooKassaUnavailableError,
    get_yookassa_client,
    reset_yookassa_client,
)

__all__ = [
    "CreatePaymentResult",
    "YooKassaAPIError",
    "YooKassaClient",
    "YooKassaUnavailableError",
    "get_yookassa_client",
    "reset_yookassa_client",
]
