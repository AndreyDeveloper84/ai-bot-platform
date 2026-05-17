"""HTTP client for YooKassa's v3 /payments API.

Phase 1 / B7 (DRF-843). Powers the ``buy_certificate`` LLM tool —
exchanges an order intent for a hosted checkout URL the bot can hand
back to the client.

### Why not the official ``yookassa`` SDK

YooKassa publishes a Python SDK, but:

1. Its breaker / retry / observability story does NOT match the
   platform's (B1 yclients_client + I1 nutrition_client both use the
   ``requests.Session`` + ``urllib3.Retry`` + inline ``_Circuit``
   pattern; adding a third HTTP backend would fragment the alert path
   in :mod:`apps.orchestrator.llm.telegram_alert`).
2. We need ONE endpoint (``POST /v3/payments``) plus a status lookup;
   the SDK weight is not justified.
3. Test mode must short-circuit BEFORE any HTTP call, not after. The
   SDK's mock-server pattern requires running yookassa-test-mock, an
   external dep.

So we mirror the yclients client shape, with payments-specific
adjustments:

* HTTP basic auth (``shop_id:secret_key``) instead of bearer tokens.
* ``Idempotence-Key`` header — REQUIRED by YooKassa for POST
  /payments; the same key on a retry returns the SAME payment row,
  not a duplicate. Tests assert this header is set.
* Test mode short-circuit — when ``settings.YOOKASSA_TEST_MODE`` is
  True, :meth:`create_payment` returns a stubbed
  :class:`CreatePaymentResult` without touching the network.

### Security

* ``YOOKASSA_SECRET_KEY`` is masked in ``__repr__`` and never appears
  in audit / event payloads. ``logger.warning`` lines log shop_id +
  order_id but never the secret.
* No live HTTP in tests: ``settings.YOOKASSA_TEST_MODE = True`` (the
  default) plus ``unittest.mock.patch`` on ``requests.Session.request``
  cover every code path that doesn't short-circuit on test mode.
* Circuit breaker — 5 failures / 60s window / 30s open. YooKassa
  rarely 5xx's but on the rare occasion they do, we must NOT keep
  hammering them: a stuck breaker is cheaper than getting our shop
  rate-limited.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

import requests  # type: ignore[import-untyped]
from django.conf import settings
from requests.adapters import HTTPAdapter  # type: ignore[import-untyped]
from urllib3.util.retry import Retry


logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_BASE_URL = "https://api.yookassa.ru/v3"

# Match the yclients_client / nutrition_client policy (CR-3 aligned).
CIRCUIT_FAILURE_WINDOW_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_DURATION_S = 30.0
_BREAKER_NAME = "yookassa.api"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """Forensic-only Telegram alert on a breaker state transition.

    Wraps all exceptions so a slow/broken Telegram channel can never
    block the breaker. Same pattern as :mod:`apps.integrations.yclients.client`.
    """
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting MUST NEVER break the breaker
        logger.exception("yookassa_client.alert_failed transition=%s", transition)


@dataclass
class _Circuit:
    """Tiny in-process circuit breaker. See yclients_client for shared notes."""

    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def is_open(self, *, now: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= CIRCUIT_OPEN_DURATION_S:
            failures_before = len(self.failures)
            self.opened_at = None
            self.failures = []
            _fire_breaker_alert("open → closed", failures_before)
            return False
        return True

    def record_failure(self, *, now: float) -> None:
        cutoff = now - CIRCUIT_FAILURE_WINDOW_S
        self.failures = [t for t in self.failures if t >= cutoff]
        self.failures.append(now)
        if len(self.failures) >= CIRCUIT_FAILURE_THRESHOLD and self.opened_at is None:
            self.opened_at = now
            logger.warning(
                "yookassa_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# ─── exceptions ────────────────────────────────────────────────────────────


class YooKassaAPIError(Exception):
    """Base for YooKassa client-visible failures (4xx, malformed JSON, etc.)."""


class YooKassaUnavailableError(YooKassaAPIError):
    """YooKassa unreachable or breaker open — caller surfaces a handoff."""


# ─── DTOs ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CreatePaymentResult:
    """Return value of :meth:`YooKassaClient.create_payment`.

    Fields:

    * ``payment_id``   — YooKassa's UUID for the payment row. Stored
                         on :class:`apps.orders.models.Order.external_payment_id`
                         so the webhook can route the eventual
                         ``payment.succeeded`` notification back to
                         this order.
    * ``checkout_url`` — Hosted-checkout URL the bot hands the buyer
                         via an inline ``Оплатить`` button.
    * ``status``       — YooKassa's payment status at creation time
                         (always ``pending`` in practice, but we
                         forward whatever the API returns for replay).
    * ``test``         — True when this result came from
                         :data:`YOOKASSA_TEST_MODE` test-mode
                         short-circuit. Tests assert on this.
    """

    payment_id: str
    checkout_url: str
    status: str
    test: bool = False


# ─── client ────────────────────────────────────────────────────────────────


class YooKassaClient:
    """YooKassa HTTP client. One instance shared per process; breaker local.

    Construct via :func:`get_yookassa_client` — module-level singleton
    that reads ``settings.YOOKASSA_*``. Tests reset via
    :func:`reset_yookassa_client`.
    """

    def __init__(
        self,
        *,
        shop_id: str,
        secret_key: str,
        return_url: str,
        test_mode: bool,
        base_url: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        # ``shop_id`` may be empty in tests that exercise test-mode only
        # paths. ``secret_key`` may also be empty. We DON'T raise here —
        # the live path (test_mode=False) raises at call time if either
        # is empty, so the singleton can boot dormant in non-payments
        # tenants.
        self.shop_id = str(shop_id)
        # We deliberately don't store the secret on a public attribute
        # name. ``_secret_key`` keeps it off ``vars(self)`` ergonomic
        # paths and out of any naive ``repr()``.
        self._secret_key = str(secret_key)
        self.return_url = str(return_url)
        self.test_mode = bool(test_mode)
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._timeout_s = timeout_s
        self._circuit = _Circuit()

        self._session = requests.Session()
        # Default headers; per-request we add Idempotence-Key.
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        if self.shop_id and self._secret_key:
            self._session.auth = (self.shop_id, self._secret_key)
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[502, 503, 504],
            # POST is NOT included in allowed_methods by default; we
            # rely on the Idempotence-Key contract from YooKassa to make
            # POST safely retryable. Same key on a retry returns the
            # same payment row, not a duplicate.
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.mount("http://", HTTPAdapter(max_retries=retry))

    def __repr__(self) -> str:
        # NEVER include the secret. Use shop_id + test_mode only.
        return (
            f"<YooKassaClient shop_id={self.shop_id!r} test_mode={self.test_mode} "
            f"base={self._base_url}>"
        )

    # ─── create_payment ───────────────────────────────────────────────────

    def create_payment(
        self,
        *,
        amount_rub: Decimal,
        description: str,
        order_id: UUID,
        idempotence_key: UUID,
        return_url: str | None = None,
    ) -> CreatePaymentResult:
        """POST ``/v3/payments`` — exchange an intent for a checkout URL.

        Args:
          amount_rub: Order amount in roubles. Formatted to ``"X.XX"``
                      for the YooKassa body (their API requires a
                      string-decimal value with 2 dp).
          description: Short Russian description shown on the
                      YooKassa-hosted checkout page.
          order_id: Our :attr:`apps.orders.models.Order.id` — embedded
                      in YooKassa's ``metadata`` so the webhook can
                      route the eventual notification back.
          idempotence_key: REQUIRED by YooKassa for POST /payments. Same
                      key on a retry returns the SAME payment row,
                      preventing duplicates. Generate one UUID per
                      logical operation; retries reuse it.
          return_url: Override the configured return URL (where YooKassa
                      sends the user after checkout). Defaults to
                      ``settings.YOOKASSA_RETURN_URL``.

        Returns:
          :class:`CreatePaymentResult` with ``payment_id``,
          ``checkout_url``, and ``status``. ``test=True`` when the
          test-mode short-circuit fired.

        Raises:
          :class:`YooKassaUnavailableError`: network failure / 5xx /
              breaker open.
          :class:`YooKassaAPIError`: 4xx / malformed JSON.
        """
        # ── Test-mode short-circuit ────────────────────────────────────
        if self.test_mode:
            # Stable, recognisable URL — operators reading logs can tell
            # at a glance that NO real money has moved. The order_id is
            # embedded so each test order has a distinct URL.
            stub_url = f"https://yoomoney.test/checkout/{order_id}"
            stub_id = f"test-{order_id}"
            logger.info(
                "yookassa_client.create_payment.test_mode order_id=%s shop_id=%s",
                order_id,
                self.shop_id or "<unset>",
            )
            return CreatePaymentResult(
                payment_id=stub_id,
                checkout_url=stub_url,
                status="pending",
                test=True,
            )

        # ── Live-mode preconditions ────────────────────────────────────
        if not self.shop_id:
            raise YooKassaAPIError("YOOKASSA_SHOP_ID is empty — live mode requires it")
        if not self._secret_key:
            raise YooKassaAPIError("YOOKASSA_SECRET_KEY is empty — live mode requires it")

        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise YooKassaUnavailableError("circuit_open")

        body = {
            "amount": {
                "value": _format_amount(amount_rub),
                "currency": "RUB",
            },
            "capture": True,  # one-step capture — YooKassa charges immediately
            "confirmation": {
                "type": "redirect",
                "return_url": return_url or self.return_url,
            },
            "description": description,
            "metadata": {
                "order_id": str(order_id),
            },
        }
        headers = {"Idempotence-Key": str(idempotence_key)}
        url = f"{self._base_url}/payments"

        try:
            response = self._session.post(
                url,
                json=body,
                headers=headers,
                timeout=self._timeout_s,
            )
        except requests.exceptions.Timeout as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "yookassa_client.timeout order_id=%s shop_id=%s",
                order_id,
                self.shop_id,
            )
            raise YooKassaUnavailableError("timeout") from exc
        except requests.exceptions.ConnectionError as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "yookassa_client.connection_error order_id=%s shop_id=%s",
                order_id,
                self.shop_id,
            )
            raise YooKassaUnavailableError("connection_error") from exc
        except requests.exceptions.RequestException as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "yookassa_client.request_error order_id=%s err=%s",
                order_id,
                type(exc).__name__,
            )
            raise YooKassaUnavailableError(f"request_error: {type(exc).__name__}") from exc

        return self._parse_create_response(response, order_id=order_id)

    def _parse_create_response(
        self,
        response: requests.Response,
        *,
        order_id: UUID,
    ) -> CreatePaymentResult:
        now = time.monotonic()
        status_code = response.status_code

        if status_code >= 500:
            self._circuit.record_failure(now=now)
            logger.warning(
                "yookassa_client.5xx status=%d order_id=%s",
                status_code,
                order_id,
            )
            raise YooKassaUnavailableError(f"http_{status_code}")

        if status_code >= 400:
            body_preview = (response.text or "")[:300]
            logger.info(
                "yookassa_client.4xx status=%d order_id=%s body=%s",
                status_code,
                order_id,
                body_preview,
            )
            raise YooKassaAPIError(f"http_{status_code}: {body_preview}")

        try:
            payload = response.json()
        except ValueError as exc:
            self._circuit.record_failure(now=now)
            raise YooKassaAPIError(f"invalid_json: {exc}") from exc

        if not isinstance(payload, dict):
            raise YooKassaAPIError(f"unexpected_payload_type: {type(payload).__name__}")

        payment_id = str(payload.get("id") or "")
        if not payment_id:
            raise YooKassaAPIError("missing_payment_id")
        confirmation = payload.get("confirmation") or {}
        checkout_url = ""
        if isinstance(confirmation, dict):
            checkout_url = str(confirmation.get("confirmation_url") or "")
        if not checkout_url:
            raise YooKassaAPIError("missing_confirmation_url")
        status = str(payload.get("status") or "pending")

        self._circuit.record_success()
        return CreatePaymentResult(
            payment_id=payment_id,
            checkout_url=checkout_url,
            status=status,
            test=False,
        )

    # ─── get_payment (reconciliation / debug) ────────────────────────────

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        """GET ``/v3/payments/{id}`` — fetch current status.

        Used for manual reconciliation when a webhook is suspected lost.
        Not called on the happy path. Returns the raw JSON envelope.

        In test mode returns a stub dict with ``status=pending`` — no
        HTTP call is made.
        """
        if self.test_mode:
            return {"id": payment_id, "status": "pending", "test": True}

        if not self.shop_id or not self._secret_key:
            raise YooKassaAPIError("YOOKASSA credentials missing — live mode requires them")

        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise YooKassaUnavailableError("circuit_open")

        url = f"{self._base_url}/payments/{payment_id}"
        try:
            response = self._session.get(url, timeout=self._timeout_s)
        except requests.exceptions.Timeout as exc:
            self._circuit.record_failure(now=now)
            raise YooKassaUnavailableError("timeout") from exc
        except requests.exceptions.ConnectionError as exc:
            self._circuit.record_failure(now=now)
            raise YooKassaUnavailableError("connection_error") from exc

        if response.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise YooKassaUnavailableError(f"http_{response.status_code}")
        if response.status_code >= 400:
            raise YooKassaAPIError(f"http_{response.status_code}: {(response.text or '')[:200]}")

        try:
            payload = response.json()
        except ValueError as exc:
            self._circuit.record_failure(now=now)
            raise YooKassaAPIError(f"invalid_json: {exc}") from exc
        if not isinstance(payload, dict):
            raise YooKassaAPIError(f"unexpected_payload_type: {type(payload).__name__}")

        self._circuit.record_success()
        return payload


# ─── helpers ───────────────────────────────────────────────────────────────


def _format_amount(amount_rub: Decimal) -> str:
    """Format a Decimal as ``"X.XX"`` for YooKassa's amount.value field.

    YooKassa's spec requires a string-decimal with 2 fractional digits.
    ``"1500"`` is rejected; ``"1500.00"`` is accepted. We quantise to
    two decimals at this boundary so the upstream value is always
    well-formed regardless of how many fractional digits the caller's
    Decimal carries.
    """
    quantised = amount_rub.quantize(Decimal("0.01"))
    return f"{quantised:.2f}"


def generate_idempotence_key() -> UUID:
    """Generate a UUID4 idempotence key. Tiny helper for callers to use
    if they don't want to import :mod:`uuid` directly."""
    return uuid.uuid4()


# ─── singleton ─────────────────────────────────────────────────────────────


_SINGLETON: YooKassaClient | None = None


def get_yookassa_client() -> YooKassaClient:
    """Module-level singleton. Lazy — fails loudly when credentials
    are unset AND test mode is off.

    The lazy construction lets a non-payments tenant boot clean: the
    singleton is built on first use; in test mode it builds with empty
    credentials and returns stubs from :meth:`create_payment` without
    raising.
    """
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = YooKassaClient(
            shop_id=getattr(settings, "YOOKASSA_SHOP_ID", ""),
            secret_key=getattr(settings, "YOOKASSA_SECRET_KEY", ""),
            return_url=getattr(settings, "YOOKASSA_RETURN_URL", ""),
            test_mode=bool(getattr(settings, "YOOKASSA_TEST_MODE", True)),
        )
    return _SINGLETON


def reset_yookassa_client() -> None:
    """Drop the singleton — used by tests + by settings overrides."""
    global _SINGLETON
    _SINGLETON = None
