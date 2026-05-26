"""Ayla catalog-recommendations REST client.

Thin sync proxy from bot-platform to Ayla's
``POST /internal/me/catalog/recommendations/`` endpoint (shipped by
Alpha in PR #165). The Mini App calls bot-platform's
``/api/v1/customer/recommendations`` — this client is what translates
the call onto the Ayla side per the identity-bridging contract:

* Service-to-service auth via ``Authorization: Bearer
  {AYLA_SERVICE_TOKEN}``. bot-platform NEVER forwards a client JWT.
* User context is conveyed via the ``X-External-User-ID`` header
  (``bot:{channel}:{channel_user_id}``). Ayla resolves it server-side
  via the proxy-user table.

Failure surface:

* :class:`RecommendationsConfigError` — service token / base URL not
  configured. Maps to 503 in the view (config gap, not Ayla outage).
* :class:`RecommendationsBadRequest` — Ayla returned 4xx (we forwarded
  garbage). Maps to 400 with Ayla's body passed through.
* :class:`RecommendationsUnavailable` — timeout / network error / 5xx /
  malformed JSON. Maps to 502 in the view (Ayla outage).
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


# Recommendation calls happen on Mini App tap — the user is staring at a
# spinner. Keep the bound short so a slow Ayla doesn't pin the request
# thread; the view returns 502 and the client retries.
TIMEOUT_S: Final[float] = 5.0


class RecommendationsConfigError(Exception):
    """``AYLA_BASE_URL`` / ``AYLA_SERVICE_TOKEN`` not configured."""


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
    token = getattr(settings, "AYLA_SERVICE_TOKEN", "")
    if not base_url or not token:
        raise RecommendationsConfigError("AYLA_BASE_URL or AYLA_SERVICE_TOKEN not configured")

    url = f"{base_url.rstrip('/')}/internal/me/catalog/recommendations/"
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
        logger.warning(
            "recommendations_client.network_failure ext_user=%s exc=%s",
            external_user_id,
            type(exc).__name__,
        )
        raise RecommendationsUnavailable(f"network: {type(exc).__name__}") from exc

    if resp.status_code >= 500:
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
        raise RecommendationsUnavailable(f"unexpected: HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        raise RecommendationsUnavailable(f"malformed_json: {exc}") from exc

    if not isinstance(body, dict):
        raise RecommendationsUnavailable("malformed: top-level is not an object")

    return body
