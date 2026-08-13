"""Ayla identity read-back client — DRF-1035.

The bot names the acting subject in ``X-External-User-ID`` on every
on-behalf-of-user s2s call (``bot:{channel}:{channel_user_id}``, built by
:func:`apps.integrations.ayla.user_proxy.external_user_id_for`). Ayla's
``IsBotServiceWithVerifiedClient`` resolves that header into a concrete
``User`` — lazily creating an ``is_proxy=True`` row on first sight — and
swaps it into ``request.user``.

Until DRF-1035 there was no way to ask Ayla **who it resolved**. That gap
is why ``BotUser.ayla_user_id`` had no writer in production and why booking
create failed with ``ayla_client_id_missing`` for every user who was not
manually provisioned. This module closes it with one GET.

Endpoint: ``GET /api/v1/internal/me/identity/``

### Why ``me/identity/`` and not ``me/``

``api/v1/internal/me/`` is a **namespace prefix** in Ayla's root urlconf —
``me/bookings/`` and ``me/catalog/recommendations/`` are mounted under it.
Hanging a resource off the bare namespace root would mix two roles; the
``me/<resource>/`` shape mirrors the existing ``me/bookings/`` and reads
consistently in logs and in the contract route table.

### Security note (DRF-1036)

This endpoint maps an enumerable external subject onto an Ayla UUID. It is
NOT the root cause of DRF-1036 — several ``/internal/users/{id}/*``
surfaces authorise on the UUID alone under a plain Bearer — but it does
make that defect cheaper to exploit. Accepted for the closed Controlled
Pilot, tracked as a production-release blocker. Nothing here weakens the
booking ``client_id`` cross-check: this call has no request body, so the
subject can only ever come from the header.

### Why a separate client (not booking_client / profile_client)

* Different surface — identity, not booking catalog and not the §3.12
  profile projection.
* Different SLA — this sits in front of the *first* identity-dependent
  action, so a short timeout matters: a slow Ayla must degrade into the
  existing ``ayla_client_id_missing`` path quickly rather than hold the
  turn.
* Independent breaker — an identity outage must not be masked by, or
  mask, booking/profile breaker state.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final
from uuid import UUID

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError


logger = logging.getLogger(__name__)


# Tight on purpose: this call gates the first identity-dependent action
# (booking create, memory write). A slow Ayla must fall through into the
# existing graceful-degradation path fast, not stall the user's turn.
TIMEOUT_S: Final[float] = 4.0

# Independent breaker state — same shape as profile_client._Circuit.
CIRCUIT_FAILURE_WINDOW_S: Final[float] = 60.0
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 5
CIRCUIT_OPEN_DURATION_S: Final[float] = 30.0
_BREAKER_NAME: Final[str] = "ayla.identity"

_ENDPOINT: Final[str] = "internal/me/identity/"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """Lazy-import the alert path so this module stays free of Django
    coupling at import time."""
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting must NEVER break the breaker
        logger.exception("identity_client.alert_failed transition=%s", transition)


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
                "identity_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# Module-level breaker — single instance per worker process.
_circuit = _Circuit()


class IdentityResolveError(Exception):
    """Identity read-back failed (timeout, 5xx, auth, circuit, malformed).

    Callers MUST degrade rather than propagate: `ensure_ayla_link` turns
    this into ``None`` so the caller falls through to its pre-existing
    unlinked behaviour (booking → ``ayla_client_id_missing`` →
    ``AdminTask``). Identity resolution failing must never abort a turn.
    """


@dataclass(frozen=True)
class ResolvedIdentity:
    """Closed shape — extra fields in Ayla's payload are dropped, not
    forwarded. Same contract-drift defence as :class:`ProfileFields`.

    ``is_proxy`` distinguishes «Ayla resolved the isolated proxy» from
    «Ayla resolved a REAL account this external identity was bound to»
    (``bind_external_identity``). The bot does not branch on it today; it
    is carried for observability and for the future binding lifecycle.
    """

    ayla_user_id: UUID
    is_proxy: bool


def _parse(payload: Any) -> ResolvedIdentity:
    """Parse the response body, tolerating both the bare and the
    ``{"data": {...}}`` envelope Ayla's ``success_response`` emits.

    Ayla wraps some internal responses in ``{"data": ...}`` (see
    ``users/response.py::success_response``) and returns others bare
    (``InternalUserProfileView``). Accepting both keeps this client
    correct regardless of which helper the endpoint ends up using —
    the alternative is a contract break that only shows up in prod.
    """
    if not isinstance(payload, dict):
        raise IdentityResolveError("malformed: response is not an object")
    inner = payload.get("data")
    body: dict[str, Any] = inner if isinstance(inner, dict) else payload

    raw_id = body.get("ayla_user_id")
    if not raw_id:
        raise IdentityResolveError("malformed: ayla_user_id missing")
    try:
        user_id = UUID(str(raw_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise IdentityResolveError("malformed: ayla_user_id is not a UUID") from exc

    # Absent `is_proxy` is not fatal — the id is what we need. Default to
    # True: an unbound proxy is the overwhelmingly common case, and the
    # flag is observability-only.
    return ResolvedIdentity(ayla_user_id=user_id, is_proxy=bool(body.get("is_proxy", True)))


def resolve_identity(external_user_id: str) -> ResolvedIdentity:
    """Ask Ayla which user it resolves ``external_user_id`` to.

    The call is a pure read from the bot's perspective, but it is not
    side-effect-free on Ayla: ``resolve_external_user`` lazily creates the
    proxy row on first sight (exactly as it already does for
    ``GET /internal/me/bookings/`` and every write). That creation is
    idempotent — ``username`` is UNIQUE on ``users_user``, so concurrent
    calls converge on one row and one UUID.

    Raises :class:`IdentityResolveError` on every failure mode.
    """
    if not external_user_id:
        raise IdentityResolveError("external_user_id is empty")

    base_url = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
    if not base_url or not token:
        raise IdentityResolveError("AYLA_BASE_URL or AYLA_INTERNAL_API_TOKEN not configured")

    now = time.monotonic()
    if _circuit.is_open(now=now):
        raise IdentityResolveError("ayla.identity circuit open")

    try:
        url = AylaUrlBuilder(base_url).build(_ENDPOINT)
    except AylaUrlError as exc:
        raise IdentityResolveError(f"invalid AYLA_BASE_URL: {exc}") from exc

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        # The ONLY place the subject is named. No request body exists, so
        # a caller cannot substitute a different subject (DRF-1035 §E.4).
        "X-External-User-ID": external_user_id,
    }

    try:
        with httpx.Client(timeout=TIMEOUT_S) as http:
            resp = http.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        _circuit.record_failure(now=time.monotonic())
        logger.warning("identity_client.network_failure exc=%s", type(exc).__name__)
        raise IdentityResolveError(f"network: {type(exc).__name__}") from exc

    if resp.status_code >= 500:
        _circuit.record_failure(now=time.monotonic())
        logger.warning("identity_client.server_error status=%d", resp.status_code)
        raise IdentityResolveError(f"server: HTTP {resp.status_code}")

    if resp.status_code in (401, 403):
        # Config problem (bad/missing bearer, malformed external id), not
        # an Ayla outage — do NOT trip the breaker, or a token rotation
        # would lock out identity resolution for 30s per worker.
        logger.error("identity_client.auth_failure status=%d", resp.status_code)
        raise IdentityResolveError(f"auth: HTTP {resp.status_code}")

    if resp.status_code != 200:
        logger.warning("identity_client.unexpected_status status=%d", resp.status_code)
        raise IdentityResolveError(f"unexpected: HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise IdentityResolveError("malformed: response is not JSON") from exc

    identity = _parse(payload)
    _circuit.record_success()
    return identity
