"""HTTP client for Ayla's **billing status** (C2) and **payout preview** (C3).

Typed wrapper for two pilot-2026-08-15 internal endpoints (frozen
contracts, ``beautygo_backend/docs/PILOT_CONTRACTS_2026-08-15.md`` §3/§4):

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
from apps.integrations.ayla.request_id import with_request_id

logger = logging.getLogger(__name__)


class BillingProxyError(Exception):
    """Base — anything billing/payout upstream that's not the happy path."""


class BillingAuthError(BillingProxyError):
    """401 / 403 from Ayla. Bearer token mismatch or missing."""


class BillingNotFoundError(BillingProxyError):
    """404 — specialist unknown or outside tenant scope (C2/C3).

    ``code`` — ``error.code`` каталога (``SUBSCRIPTION_NOT_FOUND`` и т.п.).
    """

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class BillingClientError(BillingProxyError):
    """Other 4xx. Bug on either side."""


class BillingConflictError(BillingProxyError):
    """409 — carries the wire ``error.code`` (e.g. ``NO_DEBT``)."""

    def __init__(
        self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        #: ``error.details`` каталога (``PRICE_MISMATCH`` → ``current_price``).
        self.details: dict[str, Any] = details or {}


class BillingDisabledError(BillingNotFoundError):
    """404 ``BILLING_DISABLED`` — биллинг каталога выключен флагом (DRF-2984 C1).

    Наследник :class:`BillingNotFoundError`, чтобы двери мастера (C2/C3),
    которые знают только «не найдено», вели себя как прежде.
    """


class BillingTransportError(BillingProxyError):
    """5xx after retries / network failure."""


class BillingConfigError(BillingTransportError):
    """Config gap (missing token, invalid base URL) — never retried."""


class BillingNotSentError(BillingTransportError):
    """Соединение не установлено — запрос до каталога точно не дошёл.

    Единственный сбой, после которого денежный POST (``pay-debt``) можно
    повторить, не рискуя вторым списанием.
    """


class BillingChargesDisabledError(BillingTransportError):
    """503 ``BILLING_CHARGES_DISABLED`` — списания выключены (до «GO на платный»).

    Не сбой сети: повтор ответа не изменит, поэтому не повторяется.
    """


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

    def card_setup(
        self,
        *,
        specialist_id: str,
        tariff: str,
        return_url: str,
    ) -> dict[str, Any]:
        """Card binding start (D7): ``POST internal/billing/specialists/{id}/card-setup/``.

        Body mirrors the live Ayla contract: ``{"tariff": "solo"|"salon",
        "return_url": "<url>"}`` → ``data`` verbatim (``confirmation_url``).
        Idempotent per day upstream, so transport retries are safe.
        """
        return self._post(
            f"internal/billing/specialists/{specialist_id}/card-setup/",
            json_body={"tariff": tariff, "return_url": return_url},
        )

    def pay_debt(
        self,
        *,
        specialist_id: str,
        return_url: str = "",
    ) -> dict[str, Any]:
        """One-shot debt collection: ``POST internal/billing/specialists/{id}/pay-debt/``.

        ``return_url`` is required upstream ONLY when no payment method
        is saved (redirect re-binding); with a saved method the charge
        is instant. We pass it through verbatim and let upstream enforce
        the conditional 400. 409 ``NO_DEBT`` → :class:`BillingConflictError`.
        Response verbatim: ``{payment_id, invoice_id, confirmation_url,
        amount, status, subscription_status}``.
        """
        return self._post(
            f"internal/billing/specialists/{specialist_id}/pay-debt/",
            json_body={"return_url": return_url},
            charge=True,
        )

    # ------------------------------------------------------------------
    # Подписка тенанта (DRF-2984 C3): internal/billing/tenants/{id}/…
    #
    # ``tenant_id`` — общий id тенанта бота и каталога. Ручки со вторым
    # фактором (card-setup, cancel, convert-to-salon) требуют
    # ``X-External-User-ID`` активного admin этого тенанта в каталоге;
    # чужой субъект каталог отвечает 404 — не 403.
    #
    # Открывает подписку (``POST …/subscription/``) только каталог — на первой
    # публикации соло и салона (решение главного окна 21.10, В-10). Бот её не
    # открывает: он зеркалит статус из событий ``subscription.*``.
    # ------------------------------------------------------------------

    def get_tenant_status(self, *, tenant_id: str) -> dict[str, Any]:
        """``GET tenants/{id}/status/`` → ``{tenant_id, subscription|null}`` verbatim."""
        return self._get(f"internal/billing/tenants/{tenant_id}/status/")

    def tenant_card_setup(
        self,
        *,
        tenant_id: str,
        external_user_id: str,
        return_url: str,
        consent_version: str = "",
    ) -> dict[str, Any]:
        """``POST tenants/{id}/card-setup/`` (второй фактор) → ``{confirmation_url}``."""
        body: dict[str, Any] = {"return_url": return_url}
        if consent_version:
            body["consent_version"] = consent_version
        return self._post(
            f"internal/billing/tenants/{tenant_id}/card-setup/",
            json_body=body,
            external_user_id=external_user_id,
        )

    def tenant_pay_debt(self, *, tenant_id: str, return_url: str = "") -> dict[str, Any]:
        """``POST tenants/{id}/pay-debt/`` — каталог субъекта не проверяет.

        Кто вправе платить, решает вызывающая дверь бота.
        """
        return self._post(
            f"internal/billing/tenants/{tenant_id}/pay-debt/",
            json_body={"return_url": return_url},
            charge=True,
        )

    def tenant_cancel(
        self, *, tenant_id: str, external_user_id: str, idempotency_key: str
    ) -> dict[str, Any]:
        """``POST tenants/{id}/cancel/`` (второй фактор) → ``{status, effective_at}``."""
        return self._post(
            f"internal/billing/tenants/{tenant_id}/cancel/",
            json_body={"idempotency_key": idempotency_key},
            external_user_id=external_user_id,
        )

    def tenant_convert_to_salon(
        self,
        *,
        tenant_id: str,
        external_user_id: str,
        idempotency_key: str,
        confirmed_price: str,
    ) -> dict[str, Any]:
        """``POST tenants/{id}/convert-to-salon/`` (второй фактор).

        ``confirmed_price`` — строка ``"990.00"`` (число каталог отвергает).
        409 ``PRICE_MISMATCH`` / ``ALREADY_SALON``.
        """
        return self._post(
            f"internal/billing/tenants/{tenant_id}/convert-to-salon/",
            json_body={"idempotency_key": idempotency_key, "confirmed_price": confirmed_price},
            external_user_id=external_user_id,
        )

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _get(self, path: str) -> dict[str, Any]:
        return self._with_retry("GET", path)

    def _post(
        self,
        path: str,
        *,
        json_body: dict[str, Any],
        external_user_id: str = "",
        charge: bool = False,
    ) -> dict[str, Any]:
        # Upstream is idempotent per day (billing.charges.start_card_setup),
        # so the retry policy matches the GETs. Ручки тенанта (C3) идемпотентны
        # по состоянию (одна подписка на тенант, select_for_update).
        #
        # ``charge`` — списание (pay-debt): ключа идемпотентности у него нет,
        # и повтор после потерянного ответа создал бы второй платёж. Повторяем
        # только то, что точно не ушло (:class:`BillingNotSentError`).
        return self._with_retry(
            "POST",
            path,
            json_body=json_body,
            external_user_id=external_user_id,
            charge=charge,
        )

    def _with_retry(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        external_user_id: str = "",
        charge: bool = False,
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                return self._send(
                    method, path, json_body=json_body, external_user_id=external_user_id
                )
            except (BillingConfigError, BillingChargesDisabledError):
                raise
            except BillingTransportError as exc:
                if charge and not isinstance(exc, BillingNotSentError):
                    raise
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

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        external_user_id: str = "",
    ) -> dict[str, Any]:
        try:
            url = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError as exc:
            raise BillingConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise BillingConfigError("AYLA_INTERNAL_API_TOKEN not configured")

        headers = with_request_id(
            {
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        if external_user_id:
            headers["X-External-User-ID"] = external_user_id
        try:
            response = self._client().request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BillingNotSentError(f"not sent: {exc.__class__.__name__} on {path}") from exc
        except httpx.HTTPError as exc:
            raise BillingTransportError(f"network: {exc.__class__.__name__} on {path}") from exc

        if response.status_code in (401, 403):
            raise BillingAuthError(f"Ayla billing auth failed: HTTP {response.status_code}")
        if response.status_code == 404:
            if _err_code(response) == "BILLING_DISABLED":
                raise BillingDisabledError(f"BILLING_DISABLED: {path}")
            raise BillingNotFoundError(
                f"NOT_FOUND code={_err_code(response)!r}: {path}", code=_err_code(response)
            )
        if response.status_code == 409:
            raise BillingConflictError(
                f"Ayla billing 409: code={_err_code(response)!r} on {path}",
                code=_err_code(response),
                details=_err_details(response),
            )
        if 400 <= response.status_code < 500:
            raise BillingClientError(
                f"Ayla billing 4xx: HTTP {response.status_code} body={response.text[:200]!r}"
            )
        if response.status_code == 503 and _err_code(response) == "BILLING_CHARGES_DISABLED":
            raise BillingChargesDisabledError(f"BILLING_CHARGES_DISABLED on {path}")
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


def _err_code(resp: httpx.Response) -> str:
    """Pull ``error.code`` from an error body, best-effort."""
    try:
        return (resp.json().get("error") or {}).get("code", "") or "unknown"
    except (ValueError, AttributeError):
        return "unknown"


def _err_details(resp: httpx.Response) -> dict[str, Any]:
    """Pull ``error.details`` from an error body, best-effort."""
    try:
        details = (resp.json().get("error") or {}).get("details")
    except (ValueError, AttributeError):
        return {}
    return dict(details) if isinstance(details, dict) else {}
