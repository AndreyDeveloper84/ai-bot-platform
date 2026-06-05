"""Ayla user-profile REST client for #446 user.profile.updated consumer.

Per event-contract.md §3.12 — when Ayla emits ``user.profile.updated``,
bot-platform fetches the current profile to sync the PII subset
(``display_name``, ``avatar_url`` ONLY — never phone/email/birthday/
language). This module owns the HTTP call.

### Why a separate client (not :mod:`nutrition_client`)

* Different endpoint surface (``/api/v1/users/{user_id}`` is identity,
  not nutrition).
* Different SLA — profile sync is not critical-path UI rendering, so a
  shorter timeout (5s) and a separate circuit breaker keep the
  identity-side failures from affecting the nutrition stack.
* Different return shape — only 2 fields persisted; the rest of Ayla's
  user record is forbidden by §7 PII rule + the consumer contract.

### Hard rules (PII §7)

* The response is parsed via a closed-shape dataclass — extra fields
  in Ayla's payload are IGNORED, not forwarded. Defence against
  contract drift that would smuggle phone/email through this path.
* Circuit-open + timeout + 5xx all raise :class:`ProfileFetchError`,
  which the consumer treats as «retryable failure» (handler raises →
  dispatcher dead-letters → Ayla retries per §6.3).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Final
from uuid import UUID

import httpx
from django.conf import settings


logger = logging.getLogger(__name__)


# Tighter than nutrition: display_name + avatar_url are not in any
# real-time user-facing path — a 5s upper bound is generous, and a
# faster trip into DLQ is preferable to holding the dispatcher.
TIMEOUT_S: Final[float] = 5.0

# Circuit breaker — same shape as nutrition_client but independent
# state. Profile is far lower QPS than nutrition; thresholds tuned
# accordingly.
CIRCUIT_FAILURE_WINDOW_S: Final[float] = 60.0
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 5
CIRCUIT_OPEN_DURATION_S: Final[float] = 30.0
_BREAKER_NAME: Final[str] = "ayla.profile"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """Lazy-import the Telegram alert path so this module stays free
    of Django coupling at import time."""
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting must NEVER break the breaker
        logger.exception("profile_client.alert_failed transition=%s", transition)


@dataclass
class _Circuit:
    """In-process breaker — mirrors nutrition_client._Circuit shape."""

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
                "profile_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# Module-level breaker — single instance per worker process.
_circuit = _Circuit()


class ProfileFetchError(Exception):
    """Profile fetch failed (timeout, 5xx, circuit open, or auth).

    The #446 consumer maps this to «handler raises → dispatcher
    returns HANDLER_EXCEPTION → DLQ + Ayla retry» per event-contract
    §6.3. NEVER swallow + skip — that would leave the BotUser
    projection stale forever.
    """


@dataclass(frozen=True)
class ProfileFields:
    """The PII subset bot-platform may persist locally per §3.12.

    Closed shape: extra fields in Ayla's response are silently
    dropped here, not propagated. Defence against PII §7
    contract drift.
    """

    display_name: str
    avatar_url: str


def fetch_profile_fields(user_id: UUID) -> ProfileFields:
    """Fetch ``display_name`` + ``avatar_url`` for an Ayla user.

    Raises :class:`ProfileFetchError` on any failure (timeout, 5xx,
    auth, circuit open, malformed response). The consumer treats
    this as «retry via DLQ».

    URL safety: ``user_id`` is a ``UUID`` — formatted via ``str()`` it
    produces a fixed-length hex/dash pattern with no URL-special
    characters. No injection surface.
    """
    base_url = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_SERVICE_TOKEN", "")
    if not base_url or not token:
        raise ProfileFetchError("AYLA_BASE_URL or AYLA_SERVICE_TOKEN not configured")

    now = time.monotonic()
    if _circuit.is_open(now=now):
        raise ProfileFetchError("ayla.profile circuit open")

    url = f"{base_url.rstrip('/')}/api/v1/users/{user_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=TIMEOUT_S) as http:
            resp = http.get(url, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "profile_client.network_failure user_id=%s exc=%s",
            user_id,
            type(exc).__name__,
        )
        raise ProfileFetchError(f"network: {type(exc).__name__}") from exc

    if resp.status_code >= 500:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "profile_client.server_error user_id=%s status=%d",
            user_id,
            resp.status_code,
        )
        raise ProfileFetchError(f"server: HTTP {resp.status_code}")

    if resp.status_code == 401 or resp.status_code == 403:
        # Auth issues do NOT trip the breaker — they're a config
        # problem, not an Ayla outage. Raise so the consumer
        # dead-letters and ops sees the auth gap.
        logger.error(
            "profile_client.auth_failure user_id=%s status=%d",
            user_id,
            resp.status_code,
        )
        raise ProfileFetchError(f"auth: HTTP {resp.status_code}")

    if resp.status_code == 404:
        # User does not exist in Ayla (deleted? wrong ID?). NOT a
        # retryable failure — Ayla will not start returning the
        # user. Raise so it dead-letters once, ops investigates.
        logger.warning("profile_client.user_not_found user_id=%s", user_id)
        raise ProfileFetchError("not_found: HTTP 404")

    if resp.status_code != 200:
        _circuit.record_failure(now=time.monotonic())
        raise ProfileFetchError(f"unexpected: HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        _circuit.record_failure(now=time.monotonic())
        raise ProfileFetchError(f"malformed_json: {exc}") from exc

    if not isinstance(body, dict):
        _circuit.record_failure(now=time.monotonic())
        raise ProfileFetchError("malformed: top-level is not an object")

    # Closed-shape parse — drops every field except the 2 we're
    # contractually allowed to mirror. PII §7 + §3.12 enforcement at
    # the client boundary, NOT the consumer (defence-in-depth).
    #
    # Round-1 review F3: distinguish «key absent» (malformed Ayla
    # response — refuse to write) from «key present but empty string»
    # (Ayla intentionally cleared the field — propagate). Otherwise a
    # response missing display_name silently overwrites the local
    # mirror with "" — data loss vector.
    if "display_name" not in body or "avatar_url" not in body:
        _circuit.record_failure(now=time.monotonic())
        missing = sorted(k for k in ("display_name", "avatar_url") if k not in body)
        raise ProfileFetchError(f"malformed: missing keys {missing}")

    display_name = body["display_name"]
    avatar_url = body["avatar_url"]

    if not isinstance(display_name, str) or not isinstance(avatar_url, str):
        _circuit.record_failure(now=time.monotonic())
        raise ProfileFetchError("malformed: display_name/avatar_url not strings")

    _circuit.record_success()
    return ProfileFields(display_name=display_name, avatar_url=avatar_url)
