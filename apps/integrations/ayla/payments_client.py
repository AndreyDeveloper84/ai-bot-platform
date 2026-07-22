"""HTTP client for Ayla's **client-payments** internal API (C7, REVIEW).

Typed wrapper for the C7 surface (``PILOT_CONTRACTS_2026-08-15`` §7.5):

* ``POST   /api/v1/internal/appointments/{appointment_id}/payment/`` (C7.1)
* ``POST   /api/v1/internal/users/{ayla_user_id}/cards/setup/`` (C7.2)
* ``GET    /api/v1/internal/users/{ayla_user_id}/cards/`` (C7.2)
* ``DELETE /api/v1/internal/users/{ayla_user_id}/cards/{card_id}/`` (C7.2)

### Auth (IsBotServiceWithVerifiedClient — verified against Ayla live views)

Every call carries BOTH:

* ``Authorization: Bearer AYLA_INTERNAL_API_TOKEN`` — service-to-service (C7.6);
* ``X-External-User-ID: <source>:<id>`` — Ayla resolves it to the actor
  User (``request.user``); without it the permission 403s before the view.

Body cross-checks (C7.6, defense-in-depth):

* C7.1 create — body MUST carry ``client_id`` == the resolved actor
  (else 403 CLIENT_MISMATCH) plus ``return_url`` (YooKassa redirect).
* C7.2 cards — no body client_id; the path ``ayla_user_id`` is scope-
  checked against the resolved actor. Setup requires ``consent_version``
  + ``return_url``.

### Amount discipline (C7.1 / C7.6)

The client NEVER sends an amount — Ayla prices the payment from the
authoritative Booking snapshot. There is deliberately no ``amount``
parameter anywhere: an amount from the miniapp would be a contract
violation, not a convenience.

### Idempotency

* C7.1 — one active payment per appointment; repeat POST returns the
  same payment. Retried on 5xx/network (server-side idempotent).
* cards setup/list — read/idempotent-action, retried.
* card delete — idempotent server-side (repeat → 200/204), retried.

### Errors

* :class:`ClientPaymentsAuthError` — 401/403.
* :class:`ClientPaymentsNotFoundError` — 404 (unknown user/appointment/card).
* :class:`ClientPaymentsConflictError` — 409, carries ``code`` (the wire
  ``error.code``, e.g. C1's ``SUBSCRIPTION_PAST_DUE``) for the neutral
  client-slug mapping at the view layer.
* :class:`ClientPaymentsClientError` — other 4xx.
* :class:`ClientPaymentsTransportError` — 5xx after retries / network.
* :class:`ClientPaymentsConfigError` — missing token / bad base URL,
  never retried (a :class:`ClientPaymentsTransportError` subclass).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)


class ClientPaymentsError(Exception):
    """Base — anything C7-side that's not the happy path."""


class ClientPaymentsAuthError(ClientPaymentsError):
    """401 / 403 from Ayla. Bearer token mismatch or missing."""


class ClientPaymentsNotFoundError(ClientPaymentsError):
    """404 — unknown ayla_user_id / appointment / card."""


class ClientPaymentsConflictError(ClientPaymentsError):
    """409 — carries the wire ``error.code`` for neutral-slug mapping."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class ClientPaymentsClientError(ClientPaymentsError):
    """Other 4xx. Bug on either side."""


class ClientPaymentsTransportError(ClientPaymentsError):
    """5xx after retries / network failure."""


class ClientPaymentsConfigError(ClientPaymentsTransportError):
    """Config gap (missing token, invalid base URL) — never retried."""


_BACKOFF_SECONDS = (0.5, 1.0, 2.0)


class AylaClientPaymentsClient:
    """C7 payment/cards calls with Bearer auth + retry + error mapping.

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
            timeout
            if timeout is not None
            else getattr(settings, "AYLA_CLIENT_PAYMENTS_HTTP_TIMEOUT", 30)
        )
        self._retries = (
            retries
            if retries is not None
            else getattr(settings, "AYLA_CLIENT_PAYMENTS_HTTP_RETRIES", 3)
        )
        self._http: httpx.Client | None = http_client

    # ------------------------------------------------------------------
    # C7.1 — payment create
    # ------------------------------------------------------------------

    def create_payment(
        self,
        *,
        appointment_id: str,
        external_user_id: str,
        client_id: str,
        return_url: str,
    ) -> dict[str, Any]:
        """``POST internal/appointments/{id}/payment/`` → ``data`` verbatim.

        Body is exactly ``{client_id, return_url}``: ``client_id`` is the
        C7.6 cross-check (must equal the actor the X-External-User-ID
        resolves to — else 403 CLIENT_MISMATCH), ``return_url`` is the
        YooKassa redirect. NO amount ever — the price comes from Ayla's
        Booking snapshot (C7.1/C7.6). Repeat POST returns the same
        payment (one active payment per appointment).
        """
        return self._send_with_retry(
            "POST",
            f"internal/appointments/{appointment_id}/payment/",
            json_body={"client_id": client_id, "return_url": return_url},
            external_user_id=external_user_id,
        )

    # ------------------------------------------------------------------
    # C7.2 — cards
    # ------------------------------------------------------------------

    def cards_setup(
        self,
        *,
        ayla_user_id: str,
        external_user_id: str,
        consent_version: str,
        return_url: str,
    ) -> dict[str, Any]:
        """``POST internal/users/{id}/cards/setup/`` → ``{confirmation_url}``.

        Body ``{consent_version, return_url}`` — the consent boundary
        (C7.2): binding is a separate voluntary action with an explicit,
        versioned consent text. Ownership is the path scope check
        (ayla_user_id == resolved actor), no body client_id here.
        """
        return self._send_with_retry(
            "POST",
            f"internal/users/{ayla_user_id}/cards/setup/",
            json_body={"consent_version": consent_version, "return_url": return_url},
            external_user_id=external_user_id,
        )

    def list_cards(self, *, ayla_user_id: str, external_user_id: str) -> Any:
        """``GET internal/users/{id}/cards/`` → payload verbatim (list/envelope)."""
        return self._send_with_retry(
            "GET",
            f"internal/users/{ayla_user_id}/cards/",
            external_user_id=external_user_id,
        )

    def delete_card(self, *, ayla_user_id: str, card_id: str, external_user_id: str) -> None:
        """``DELETE internal/users/{id}/cards/{card_id}/`` — idempotent."""
        self._send_with_retry(
            "DELETE",
            f"internal/users/{ayla_user_id}/cards/{card_id}/",
            external_user_id=external_user_id,
        )

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _send_with_retry(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        external_user_id: str,
    ) -> Any:
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                return self._send(
                    method, path, json_body=json_body, external_user_id=external_user_id
                )
            except ClientPaymentsConfigError:
                raise
            except ClientPaymentsTransportError as exc:
                last_exc = exc
                if attempt == self._retries - 1:
                    break
                pause = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "client_payments.http.retry attempt=%s pause=%s path=%s",
                    attempt + 1,
                    pause,
                    path,
                )
                time.sleep(pause)
        raise ClientPaymentsTransportError(
            f"Ayla client-payments: exhausted {self._retries} retries on {path}"
        ) from last_exc

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        external_user_id: str,
    ) -> Any:
        try:
            url = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError as exc:
            raise ClientPaymentsConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise ClientPaymentsConfigError("AYLA_INTERNAL_API_TOKEN not configured")

        try:
            response = self._client().request(
                method,
                url,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    # IsBotServiceWithVerifiedClient: resolves the actor
                    # User; without it Ayla 403s before the view (C7.6).
                    "X-External-User-ID": external_user_id,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ClientPaymentsTransportError(
                f"network: {exc.__class__.__name__} on {path}"
            ) from exc

        if response.status_code in (401, 403):
            raise ClientPaymentsAuthError(
                f"Ayla client-payments auth failed: HTTP {response.status_code}"
            )
        if response.status_code == 404:
            raise ClientPaymentsNotFoundError(f"not found: {path}")
        if response.status_code == 409:
            raise ClientPaymentsConflictError(
                f"Ayla client-payments 409: code={_err_code(response)!r} on {path}",
                code=_err_code(response),
            )
        if 400 <= response.status_code < 500:
            raise ClientPaymentsClientError(
                f"Ayla client-payments 4xx: HTTP {response.status_code} "
                f"code={_err_code(response)!r} body={response.text[:200]!r}"
            )
        if response.status_code >= 500:
            raise ClientPaymentsTransportError(f"http_{response.status_code} on {path}")
        if response.status_code == 204 or not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise ClientPaymentsTransportError(
                f"non-JSON {response.status_code} on {path}"
            ) from exc
        # Unwrap the {"data": ...} envelope when present; pass through verbatim.
        if isinstance(payload, dict) and isinstance(payload.get("data"), (dict, list)):
            return payload["data"]
        return payload

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> "AylaClientPaymentsClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _err_code(resp: httpx.Response) -> str:
    """Pull ``error.code`` from an error body, best-effort."""
    try:
        return (resp.json().get("error") or {}).get("code", "") or "unknown"
    except (ValueError, AttributeError):
        return "unknown"
