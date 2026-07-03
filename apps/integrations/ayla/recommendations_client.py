"""Ayla catalog-recommendations REST client.

Thin sync proxy from bot-platform to Ayla's
``POST /api/v1/internal/me/catalog/recommendations/`` endpoint (shipped by
Alpha in PR #165). The Mini App calls bot-platform's
``/api/v1/customer/recommendations`` — this client is what translates
the call onto the Ayla side per the identity-bridging contract:

* Service-to-service auth via ``Authorization: Bearer
  {AYLA_INTERNAL_API_TOKEN}`` (#1048/#1050 — the single s2s Bearer Ayla
  validates; the deprecated ``AYLA_SERVICE_TOKEN`` never existed on Ayla's
  side). bot-platform NEVER forwards a client JWT.
* User context is conveyed via the ``X-External-User-ID`` header
  (``bot:{channel}:{channel_user_id}``). Ayla resolves it server-side
  via the proxy-user table.

The URL is built through
:class:`apps.integrations.ayla.url_builder.AylaUrlBuilder` (#1049) — it owns
host-only validation of ``AYLA_BASE_URL`` and inserts the ``api/v1`` version
prefix, so this module never hand-builds an ``f"{base}/..."`` string (the old
path was missing the ``/api/v1`` prefix entirely).

Resilience (#1048): an inline circuit breaker (5 failures in 60s → 30s
cooldown) matches the booking/nutrition clients so a hard Ayla outage stops
hammering the request thread — an open breaker short-circuits to
:class:`RecommendationsUnavailable`. 4xx does NOT trip the breaker (we sent
garbage, Ayla is healthy).

Failure surface:

* :class:`RecommendationsConfigError` — service token / base URL not
  configured (or malformed base). Maps to 503 in the view (config gap,
  not Ayla outage).
* :class:`RecommendationsBadRequest` — Ayla returned 4xx (we forwarded
  garbage). Maps to 400 with Ayla's body passed through.
* :class:`RecommendationsUnavailable` — timeout / network error / 5xx /
  malformed JSON / circuit open. Maps to 502 in the view (Ayla outage).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)


# Recommendation calls happen on Mini App tap — the user is staring at a
# spinner. Keep the bound short so a slow Ayla doesn't pin the request
# thread; the view returns 502 and the client retries.
TIMEOUT_S: Final[float] = 5.0

# Inline circuit breaker (#1048) — same policy as the booking/nutrition/profile
# clients: 5 failures in 60s → 30s cooldown. Module-level (this client is a
# free function, not a class) so the state is per-worker.
CIRCUIT_FAILURE_WINDOW_S: Final[float] = 60.0
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 5
CIRCUIT_OPEN_DURATION_S: Final[float] = 30.0
_BREAKER_NAME: Final[str] = "ayla.recommendations"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """Borrow the CR-3 Telegram alert path on a breaker state transition.

    Lazy-imports the alert helper and swallows every exception — alerting is
    forensic and must NEVER break the breaker (same contract as the booking /
    nutrition / profile clients).
    """
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting must NEVER break the breaker
        logger.exception("recommendations_client.alert_failed transition=%s", transition)


@dataclass
class _Circuit:
    """In-process breaker — mirrors profile_client._Circuit shape."""

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
                "recommendations_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# Module-level breaker — single instance per worker process.
_circuit = _Circuit()


def reset_recommendations_circuit() -> None:
    """Reset the module breaker — used by tests to isolate cases."""
    _circuit.record_success()


class RecommendationsConfigError(Exception):
    """``AYLA_BASE_URL`` / ``AYLA_INTERNAL_API_TOKEN`` not configured (or base malformed)."""


class RecommendationsBadRequest(Exception):
    """Ayla returned 4xx — body forwarded so the caller sees Ayla's reason."""

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"ayla recommendations HTTP {status_code}")


class RecommendationsUnavailable(Exception):
    """Network/timeout/5xx/malformed JSON — caller maps to 502."""


def fetch_recommendations(
    *,
    external_user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST ``/internal/me/catalog/recommendations/`` and return Ayla's body.

    Args:
      external_user_id: ``bot:{channel}:{channel_user_id}`` — produced
                        by :func:`apps.integrations.ayla.external_user_id_for`.
      payload: Request body forwarded as-is (``lat``/``lon``/``goal``/
               ``tenant_history``). Caller is responsible for shape
               validation — this layer is a translation hop, not a
               schema gate.

    Returns:
      The parsed JSON object Ayla returned. Pass-through; no shape
      enforcement here so the contract can evolve on Alpha's side
      without lockstep bot-platform releases.

    Raises:
      :class:`RecommendationsConfigError`
      :class:`RecommendationsBadRequest`
      :class:`RecommendationsUnavailable`
    """
    base_url = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
    if not base_url or not token:
        raise RecommendationsConfigError("AYLA_BASE_URL or AYLA_INTERNAL_API_TOKEN not configured")

    # Single URL seam (#1049): the builder validates ``AYLA_BASE_URL`` is
    # host-only and inserts the ``api/v1`` prefix (the old hand-built path was
    # missing it). A malformed base is a config gap, not an Ayla outage — build
    # BEFORE the circuit check so a bad base always surfaces as a 503 config
    # error, matching how the missing-token check already precedes the breaker.
    try:
        url = AylaUrlBuilder(base_url).build("internal/me/catalog/recommendations/")
    except AylaUrlError as exc:
        raise RecommendationsConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc

    now = time.monotonic()
    if _circuit.is_open(now=now):
        raise RecommendationsUnavailable("circuit_open")

    headers = {
        "Authorization": f"Bearer {token}",
        "X-External-User-ID": external_user_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=TIMEOUT_S) as http:
            resp = http.post(url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "recommendations_client.network_failure ext_user=%s exc=%s",
            external_user_id,
            type(exc).__name__,
        )
        raise RecommendationsUnavailable(f"network: {type(exc).__name__}") from exc

    if resp.status_code >= 500:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "recommendations_client.server_error ext_user=%s status=%d",
            external_user_id,
            resp.status_code,
        )
        raise RecommendationsUnavailable(f"server: HTTP {resp.status_code}")

    if 400 <= resp.status_code < 500:
        # 4xx surfaces Ayla's body so the caller (view → frontend) can
        # see Ayla's «detail» field. Don't trip on auth (401/403) as a
        # special case — same handling: forward the body, log loudly.
        # A 4xx means WE sent garbage, not that Ayla is down — do NOT trip
        # the breaker.
        body: Any
        try:
            body = resp.json()
        except ValueError:
            body = {"detail": resp.text[:500]}
        logger.warning(
            "recommendations_client.client_error ext_user=%s status=%d",
            external_user_id,
            resp.status_code,
        )
        raise RecommendationsBadRequest(resp.status_code, body)

    if resp.status_code != 200:
        # 1xx/2xx-non-200/3xx — Ayla shouldn't return these; treat as
        # outage so the frontend retries.
        _circuit.record_failure(now=time.monotonic())
        raise RecommendationsUnavailable(f"unexpected: HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        _circuit.record_failure(now=time.monotonic())
        raise RecommendationsUnavailable(f"malformed_json: {exc}") from exc

    if not isinstance(body, dict):
        _circuit.record_failure(now=time.monotonic())
        raise RecommendationsUnavailable("malformed: top-level is not an object")

    _circuit.record_success()
    return body
