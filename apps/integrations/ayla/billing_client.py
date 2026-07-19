"""HTTP client for Ayla's **billing status** (C2) and **payout preview** (C3).

Typed wrapper for two pilot-2026-08-15 internal endpoints (frozen
contracts, ``PILOT_CONTRACTS_2026-08-15.md`` §3/§4):

* ``GET /api/v1/internal/billing/specialists/{specialist_id}/status/`` (C2, owner W2)
* ``GET /api/v1/internal/specialists/{specialist_id}/payout-preview/`` (C3, owner W1)

Both are read-only GETs with Bearer ``AYLA_INTERNAL_API_TOKEN`` auth.
The bot-side consumer is ``apps.master_api`` (W3) proxying to the master
Mini App (W4).

### Pass-through policy

The contracts freeze the response fields; this client returns the
``data`` payload **verbatim** (unknown keys preserved — additive
contract bumps ride through without a bot deploy). No field invention,
no reshaping: the proxy layer in ``apps.master_api.services.billing``
serialises exactly what this returns.

### Errors

* :class:`BillingAuthError` — 401/403 (token mismatch).
* :class:`BillingNotFoundError` — 404 (``SPECIALIST_NOT_FOUND`` per C2/C3;
  note: an empty selection is always 200 with zeroed values, so a 404 is
  a genuine unknown/inaccessible specialist).
* :class:`BillingClientError` — other 4xx.
* :class:`BillingTransportError` — 5xx after retries / network failure.
* :class:`BillingConfigError` — missing token / invalid base URL,
  never retried (a :class:`BillingTransportError` subclass).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)


class BillingProxyError(Exception):
    """Base — anything billing/payout upstream that's not the happy path."""


class BillingAuthError(BillingProxyError):
    """401 / 403 from Ayla. Bearer token mismatch or missing."""


class BillingNotFoundError(BillingProxyError):
    """404 — specialist unknown or outside tenant scope (C2/C3)."""


class BillingClientError(BillingProxyError):
    """Other 4xx. Bug on either side."""


class BillingTransportError(BillingProxyError):
    """5xx after retries / network failure."""


class BillingConfigError(BillingTransportError):
    """Config gap (missing token, invalid base URL) — never retried."""


_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


class AylaBillingClient:
    """Fetch C2 billing status / C3 payout preview with retry + error mapping.

    Construction params are settings overrides for tests; prod code reads
    ``AYLA_BASE_URL`` / ``AYLA_INTERNAL_API_TOKEN`` from Django settings.
    An injected ``http_client`` (e.g. ``httpx.MockTransport``) fakes the
    wire in tests.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
        retries: int | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (
            base_url if base_url is not None else getattr(settings, "AYLA_BASE_URL", "")
        )
        self._token = (
            token if token is not None else getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
        )
        self._timeout = (
            timeout if timeout is not None else getattr(settings, "AYLA_BILLING_HTTP_TIMEOUT", 30)
        )
        self._retries = (
            retries if retries is not None else getattr(settings, "AYLA_BILLING_HTTP_RETRIES", 3)
        )
        self._http: httpx.Client | None = http_client

    def get_billing_status(self, *, specialist_id: str) -> dict[str, Any]:
        """C2: ``GET internal/billing/specialists/{id}/status/`` → ``data`` verbatim."""
        return self._get(f"internal/billing/specialists/{specialist_id}/status/")

    def get_payout_preview(self, *, specialist_id: str) -> dict[str, Any]:
        """C3: ``GET internal/specialists/{id}/payout-preview/`` → ``data`` verbatim."""
        return self._get(f"internal/specialists/{specialist_id}/payout-preview/")

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                return self._send(path)
            except BillingConfigError:
                raise
            except BillingTransportError as exc:
                last_exc = exc
                if attempt == self._retries - 1:
                    break
                pause = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "ayla.billing.http.retry attempt=%s pause=%s path=%s",
                    attempt + 1,
                    pause,
                    path,
                )
                time.sleep(pause)
        raise BillingTransportError(
            f"Ayla billing: exhausted {self._retries} retries on {path}"
        ) from last_exc

    def _send(self, path: str) -> dict[str, Any]:
        try:
            url = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError as exc:
            raise BillingConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise BillingConfigError("AYLA_INTERNAL_API_TOKEN not configured")

        try:
            response = self._client().get(
                url,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise BillingTransportError(f"network: {exc.__class__.__name__} on {path}") from exc

        if response.status_code in (401, 403):
            raise BillingAuthError(f"Ayla billing auth failed: HTTP {response.status_code}")
        if response.status_code == 404:
            raise BillingNotFoundError(f"SPECIALIST_NOT_FOUND: {path}")
        if 400 <= response.status_code < 500:
            raise BillingClientError(
                f"Ayla billing 4xx: HTTP {response.status_code} body={response.text[:200]!r}"
            )
        if response.status_code >= 500:
            raise BillingTransportError(f"http_{response.status_code} on {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise BillingTransportError(f"non-JSON {response.status_code} on {path}") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        return dict(data) if isinstance(data, dict) else {}

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> "AylaBillingClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
