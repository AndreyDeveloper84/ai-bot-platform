"""HTTP client for Ayla djangoproject's ``POST /api/v1/payments/create``.

Phase 0 / #427. Per ADR-0009 §Domain ownership matrix + §Hard rule #5,
bot-platform skills that touch payment lifecycle MUST call Ayla
djangoproject's REST API rather than YooKassa directly. Ayla owns the
canonical Payment row, the YooKassa lifecycle (hold→capture, refund,
webhook), and the receipt. bot-platform renders the returned checkout
URL via an inline button.

### Test mode

``settings.AYLA_PAYMENTS_TEST_MODE = True`` (the default) makes
:meth:`AylaPaymentsClient.create_payment` return a stubbed checkout
URL WITHOUT any HTTP call. Production deployments set this to False
explicitly. Tests rely on test-mode plus mocked HTTP for
network-touching paths.

### Why a new module instead of yookassa_client

This is the OUTBOUND side of the YooKassa removal (the INBOUND
webhook + apps/orders/tasks.py survive until #428, Sync 2). A new
module keeps the YooKassa code untouched while bot-platform stops
calling it for new payments — the YooKassa receiver can keep
processing legacy in-flight payments during the safety window with
zero risk of being accidentally re-invoked from skills.

### Auth

Bearer token via ``settings.AYLA_INTERNAL_API_TOKEN``. The token
shape will be unified with Beta's ``jwt-contract.md`` (Sync 3) — the
bearer header form stays stable; the token issuer may swap (today:
a long-lived service-to-service secret; later: a short-lived JWT
minted by Ayla).

### Idempotence-Key

Same contract as YooKassa: ``Idempotence-Key`` header REQUIRED on POST.
Same key on a retry returns the SAME Payment row, not a duplicate.
Tests assert this header is set.
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

# Match the yclients_client / yookassa_client policy.
CIRCUIT_FAILURE_WINDOW_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_DURATION_S = 30.0
_BREAKER_NAME = "ayla_payments.api"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """Forensic-only Telegram alert on breaker state transitions.

    Wraps all exceptions so a slow/broken Telegram channel cannot
    block the breaker. Same pattern as yclients_client / yookassa_client.
    """
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting MUST NEVER break the breaker
        logger.exception("ayla_payments_client.alert_failed transition=%s", transition)


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
                "ayla_payments_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# ─── exceptions ────────────────────────────────────────────────────────────


class AylaPaymentsAPIError(Exception):
    """Base for Ayla-payments client-visible failures (4xx, malformed JSON, etc.)."""


class AylaPaymentsUnavailableError(AylaPaymentsAPIError):
    """Ayla payments unreachable or breaker open — caller surfaces a handoff."""


# ─── DTOs ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CreatePaymentResult:
    """Return value of :meth:`AylaPaymentsClient.create_payment`.

    Fields:

    * ``payment_id``   — Ayla's canonical Payment id. Shown to the user
                         (when needed) and used for forensics / support
                         lookups across Ayla djangoproject.
    * ``checkout_url`` — YooKassa-hosted checkout URL Ayla obtained on
                         our behalf. bot-platform renders this as an
                         inline ``💳 Оплатить`` button.
    * ``status``       — Ayla's payment status at creation time
                         (typically ``pending``).
    * ``test``         — True when the result came from the local
                         test-mode short-circuit (no HTTP made). Tests
                         assert on this.
    """

    payment_id: str
    checkout_url: str
    status: str
    test: bool = False


# ─── client ────────────────────────────────────────────────────────────────


class AylaPaymentsClient:
    """Ayla payments HTTP client. One instance shared per process; breaker local.

    Construct via :func:`get_ayla_payments_client` — module-level
    singleton that reads ``settings.AYLA_*``. Tests reset via
    :func:`reset_ayla_payments_client`.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        test_mode: bool,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        # ``base_url`` and ``api_token`` may be empty for the test-mode
        # path — the singleton boots dormant for non-payments tenants.
        # The live path raises at call time when either is empty.
        self.base_url = str(base_url).rstrip("/")
        self._api_token = str(api_token)
        self.test_mode = bool(test_mode)
        self._timeout_s = timeout_s
        self._circuit = _Circuit()

        self._session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[502, 503, 504],
            # POST included — the Idempotence-Key contract makes POST
            # safely retryable. Same key on a retry returns the same
            # Payment row, not a duplicate.
            allowed_methods=frozenset({"GET", "POST"}),
            raise_on_status=False,
        )
        self._session.mount("https://", HTTPAdapter(max_retries=retry))
        self._session.mount("http://", HTTPAdapter(max_retries=retry))

    def __repr__(self) -> str:
        # NEVER include the token. base_url + test_mode only.
        return f"<AylaPaymentsClient base_url={self.base_url!r} test_mode={self.test_mode}>"

    # ─── create_payment ───────────────────────────────────────────────────

    def create_payment(
        self,
        *,
        amount_rub: Decimal,
        description: str,
        idempotence_key: UUID,
        recipient_name: str = "",
        buyer_email: str = "",
        kind: str = "certificate",
    ) -> CreatePaymentResult:
        """POST ``/api/v1/payments/create`` — exchange an intent for a checkout URL.

        Args:
          amount_rub: Payment amount in roubles. Formatted to ``"X.XX"``
                      for the wire (string-decimal with 2 dp).
          description: Short Russian description shown on the
                      YooKassa-hosted checkout page that Ayla returns.
          idempotence_key: REQUIRED. Same key on a retry returns the
                      SAME Ayla Payment row, preventing duplicates.
          recipient_name: Whom the certificate is for. Optional.
          buyer_email: Buyer's email for 54-FZ receipt. Optional.
          kind: Payment kind in Ayla's vocabulary. Today only
                ``certificate`` is exercised; later flows (deposit,
                package) extend this enum.

        Returns:
          :class:`CreatePaymentResult` with ``payment_id``,
          ``checkout_url``, ``status``. ``test=True`` when the
          test-mode short-circuit fired.

        Raises:
          :class:`AylaPaymentsUnavailableError`: network failure / 5xx /
              breaker open.
          :class:`AylaPaymentsAPIError`: 4xx / malformed JSON / missing
              required fields in the response.
        """
        # ── Test-mode short-circuit ────────────────────────────────────
        if self.test_mode:
            # Stable, recognisable URL — operators reading logs can tell
            # at a glance that NO real money has moved. Idempotence key
            # embedded so each test call has a distinct, traceable URL.
            stub_url = f"https://yoomoney.test/checkout/{idempotence_key}"
            stub_id = f"test-{idempotence_key}"
            logger.info(
                "ayla_payments_client.create_payment.test_mode idem=%s base_url=%s",
                idempotence_key,
                self.base_url or "<unset>",
            )
            return CreatePaymentResult(
                payment_id=stub_id,
                checkout_url=stub_url,
                status="pending",
                test=True,
            )

        # ── Live-mode preconditions ────────────────────────────────────
        if not self.base_url:
            raise AylaPaymentsAPIError("AYLA_BASE_URL is empty — live mode requires it")
        if not self._api_token:
            raise AylaPaymentsAPIError("AYLA_INTERNAL_API_TOKEN is empty — live mode requires it")

        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise AylaPaymentsUnavailableError("circuit_open")

        body: dict[str, Any] = {
            "amount_rub": _format_amount(amount_rub),
            "description": description,
            "kind": kind,
            "recipient_name": recipient_name,
            "buyer_email": buyer_email,
        }
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Idempotence-Key": str(idempotence_key),
        }
        url = f"{self.base_url}/api/v1/payments/create"

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
                "ayla_payments_client.timeout idem=%s base_url=%s",
                idempotence_key,
                self.base_url,
            )
            raise AylaPaymentsUnavailableError("timeout") from exc
        except requests.exceptions.ConnectionError as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "ayla_payments_client.connection_error idem=%s base_url=%s",
                idempotence_key,
                self.base_url,
            )
            raise AylaPaymentsUnavailableError("connection_error") from exc
        except requests.exceptions.RequestException as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "ayla_payments_client.request_error idem=%s err=%s",
                idempotence_key,
                type(exc).__name__,
            )
            raise AylaPaymentsUnavailableError(f"request_error: {type(exc).__name__}") from exc

        return self._parse_create_response(response, idempotence_key=idempotence_key)

    def _parse_create_response(
        self,
        response: requests.Response,
        *,
        idempotence_key: UUID,
    ) -> CreatePaymentResult:
        now = time.monotonic()
        status_code = response.status_code

        if status_code >= 500:
            self._circuit.record_failure(now=now)
            logger.warning(
                "ayla_payments_client.5xx status=%d idem=%s",
                status_code,
                idempotence_key,
            )
            raise AylaPaymentsUnavailableError(f"http_{status_code}")

        if status_code >= 400:
            body_preview = (response.text or "")[:300]
            logger.info(
                "ayla_payments_client.4xx status=%d idem=%s body=%s",
                status_code,
                idempotence_key,
                body_preview,
            )
            raise AylaPaymentsAPIError(f"http_{status_code}: {body_preview}")

        try:
            payload = response.json()
        except ValueError as exc:
            self._circuit.record_failure(now=now)
            raise AylaPaymentsAPIError(f"invalid_json: {exc}") from exc

        if not isinstance(payload, dict):
            raise AylaPaymentsAPIError(f"unexpected_payload_type: {type(payload).__name__}")

        payment_id = str(payload.get("payment_id") or "")
        if not payment_id:
            raise AylaPaymentsAPIError("missing_payment_id")
        checkout_url = str(payload.get("checkout_url") or "")
        if not checkout_url:
            raise AylaPaymentsAPIError("missing_checkout_url")
        status = str(payload.get("status") or "pending")

        self._circuit.record_success()
        return CreatePaymentResult(
            payment_id=payment_id,
            checkout_url=checkout_url,
            status=status,
            test=False,
        )


# ─── helpers ───────────────────────────────────────────────────────────────


def _format_amount(amount_rub: Decimal) -> str:
    """Format a Decimal as ``"X.XX"`` for the wire amount field.

    Matches YooKassa's spec (string-decimal with 2 fractional digits).
    Quantise here so an upstream caller doesn't have to think about
    fractional-digit normalisation.
    """
    quantised = amount_rub.quantize(Decimal("0.01"))
    return f"{quantised:.2f}"


def generate_idempotence_key() -> UUID:
    """Generate a UUID4 idempotence key. Tiny helper for callers that
    don't want to import :mod:`uuid` directly."""
    return uuid.uuid4()


# ─── singleton ─────────────────────────────────────────────────────────────


_SINGLETON: AylaPaymentsClient | None = None


def get_ayla_payments_client() -> AylaPaymentsClient:
    """Module-level singleton. Lazy — fails loudly at call time when
    credentials are unset AND test mode is off.

    The lazy construction lets a non-payments tenant boot clean: the
    singleton is built on first use; in test mode it builds with empty
    credentials and returns stubs from :meth:`create_payment` without
    raising.
    """
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = AylaPaymentsClient(
            base_url=getattr(settings, "AYLA_BASE_URL", ""),
            api_token=getattr(settings, "AYLA_INTERNAL_API_TOKEN", ""),
            test_mode=bool(getattr(settings, "AYLA_PAYMENTS_TEST_MODE", True)),
        )
    return _SINGLETON


def reset_ayla_payments_client() -> None:
    """Drop the singleton — used by tests + by settings overrides."""
    global _SINGLETON
    _SINGLETON = None
