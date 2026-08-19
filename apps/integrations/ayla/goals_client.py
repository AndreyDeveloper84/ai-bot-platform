"""Ayla goal-layer REST client (DRF-1190).

Thin sync proxy from bot-platform to Ayla's goal-layer endpoints
(shipped in beautygo_backend feat/goal-layer):

* ``GET  /api/v1/internal/me/decision-context/`` — эфемерный документ
  состояния (known / missing / suggestions / intents). Mini App — тупой
  отрисовщик: документ проксируется как есть, без трансляции.
* ``POST /api/v1/internal/me/goals/select/`` — фиксация выбора
  (``goal_key`` XOR ``goal_text`` XOR ``intent=need_guidance``); ответ —
  обновлённый документ состояния.

Auth per the identity-bridging contract (same as recommendations_client):
``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}`` +
``X-External-User-ID: bot:{channel}:{channel_user_id}``; client initData
never leaves bot-platform. URLs via AylaUrlBuilder (#1049).

Resilience mirrors recommendations_client (#1048): inline circuit
breaker (5 failures in 60s → 30s cooldown); 4xx does NOT trip it.

Failure surface:

* :class:`GoalsConfigError` — token / base URL not configured → 503.
* :class:`GoalsBadRequest` — Ayla 4xx, body forwarded → 400.
* :class:`GoalsUnavailable` — timeout / network / 5xx / malformed JSON /
  circuit open → 502.
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

# Goal calls happen on Mini App tap — the user is staring at a spinner.
TIMEOUT_S: Final[float] = 5.0

CIRCUIT_FAILURE_WINDOW_S: Final[float] = 60.0
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 5
CIRCUIT_OPEN_DURATION_S: Final[float] = 30.0
_BREAKER_NAME: Final[str] = "ayla.goals"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """CR-3 alert path on a breaker transition — forensic only, must
    NEVER break the breaker (same contract as the other Ayla clients)."""
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting must NEVER break the breaker
        logger.exception("goals_client.alert_failed transition=%s", transition)


@dataclass
class _Circuit:
    """In-process breaker — mirrors recommendations_client._Circuit shape."""

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
                "goals_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# Module-level breaker — single instance per worker process.
_circuit = _Circuit()


def reset_goals_circuit() -> None:
    """Reset the module breaker — used by tests to isolate cases."""
    _circuit.record_success()


class GoalsConfigError(Exception):
    """``AYLA_BASE_URL`` / ``AYLA_INTERNAL_API_TOKEN`` not configured (or base malformed)."""


class GoalsBadRequest(Exception):
    """Ayla returned 4xx — body forwarded so the caller sees Ayla's reason."""

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"ayla goals HTTP {status_code}")


class GoalsUnavailable(Exception):
    """Network/timeout/5xx/malformed JSON — caller maps to 502."""


def _request(
    method: str,
    path: str,
    *,
    external_user_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Single request seam for the goal layer — see module docstring for
    the failure contract. ``path`` is relative to ``/api/v1/``."""
    base_url = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
    if not base_url or not token:
        raise GoalsConfigError("AYLA_BASE_URL or AYLA_INTERNAL_API_TOKEN not configured")

    try:
        url = AylaUrlBuilder(base_url).build(path)
    except AylaUrlError as exc:
        raise GoalsConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc

    now = time.monotonic()
    if _circuit.is_open(now=now):
        raise GoalsUnavailable("circuit_open")

    headers = {
        "Authorization": f"Bearer {token}",
        "X-External-User-ID": external_user_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=TIMEOUT_S) as http:
            resp = http.request(method, url, headers=headers, json=payload)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "goals_client.network_failure ext_user=%s exc=%s",
            external_user_id,
            type(exc).__name__,
        )
        raise GoalsUnavailable(f"network: {type(exc).__name__}") from exc

    if resp.status_code >= 500:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "goals_client.server_error ext_user=%s status=%d",
            external_user_id,
            resp.status_code,
        )
        raise GoalsUnavailable(f"server: HTTP {resp.status_code}")

    if 400 <= resp.status_code < 500:
        # 4xx = WE sent garbage, not that Ayla is down — forward the body,
        # do NOT trip the breaker.
        try:
            body: Any = resp.json()
        except ValueError:
            body = {"detail": resp.text[:500]}
        logger.warning(
            "goals_client.client_error ext_user=%s status=%d",
            external_user_id,
            resp.status_code,
        )
        raise GoalsBadRequest(resp.status_code, body)

    if resp.status_code != 200:
        _circuit.record_failure(now=time.monotonic())
        raise GoalsUnavailable(f"unexpected: HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        _circuit.record_failure(now=time.monotonic())
        raise GoalsUnavailable(f"malformed_json: {exc}") from exc

    if not isinstance(body, dict):
        _circuit.record_failure(now=time.monotonic())
        raise GoalsUnavailable("malformed: top-level is not an object")

    _circuit.record_success()
    return body


def fetch_decision_context(*, external_user_id: str) -> dict[str, Any]:
    """GET ``/internal/me/decision-context/`` — документ состояния as-is."""
    return _request(
        "GET",
        "internal/me/decision-context/",
        external_user_id=external_user_id,
    )


def post_goal_select(
    *,
    external_user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST ``/internal/me/goals/select/`` — возвращает обновлённый документ.

    ``payload`` forwarded as-is (``goal_key`` / ``goal_text`` / ``intent``
    + ``source_channel``); shape validation lives on Ayla's side — this
    layer is a translation hop, not a schema gate.
    """
    return _request(
        "POST",
        "internal/me/goals/select/",
        external_user_id=external_user_id,
        payload=payload,
    )
