"""MAX REST outbound — `send_message` (DRF-441 / Sprint 2 / D2).

Sends a message to a MAX chat via the public REST API. **No MAX SDK**
in the web process — the SDK is async-only and stays in
`legacy_maxbot/` until Sprint 3+ replaces it with the AI Concierge.

### MAX API wire format

POST `https://botapi.max.ru/messages?chat_id={chat_id}` with
`Authorization: {MAX_BOT_TOKEN}` (NOT `Bearer {token}` — MAX uses the
raw access token; see `legacy_notifications.max_bot.send_max_message`
running in prod since 2026-04). Body is JSON::

    {"text": "...", "attachments": [...]}

`chat_id` is a *query parameter*, not a body field — MAX-specific
quirk that we preserve until Sprint 3+ rewrites this path.

### Error contract

- 2xx → returns parsed JSON response dict.
- non-2xx → raises :class:`MaxAPIError` with status + truncated body.
- Network failure (`httpx.RequestError`) → raises :class:`MaxAPIError`
  with status=0 and the exception message.

The handler (D3) decides what to do on failure — typically log + emit
`channels.max.outbound.failed` event + don't ACK the consumer (so PEL
retains the entry for retry).
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
import httpx

logger = logging.getLogger(__name__)


_DEFAULT_BASE = "https://botapi.max.ru"


class MaxAPIError(Exception):
    """Non-2xx response from MAX REST API, or network failure."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"MAX API status={status_code}: {body[:200]}")


def _api_base() -> str:
    return getattr(settings, "MAX_API_BASE", _DEFAULT_BASE)


def _token() -> str:
    return getattr(settings, "MAX_BOT_TOKEN", "")


def send_message(
    *,
    chat_id: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """POST a message to a MAX chat.

    Args:
      chat_id: stringified `recipient.chat_id` from the inbound event
               (D1 normalises ints to str). MAX accepts ints in the
               query parameter; we send the string and httpx URL-encodes.
      text: message body. Empty allowed (a "typing" or attachment-only
            send), but D3 should always pass non-empty for Sprint 2 echo.
      attachments: pass-through list of dicts in MAX wire format. Full
                   MediaRef DTO contract lands in Sprint 3.
      timeout: request timeout seconds. Default 10s — well under any
               consumer-side budget for a single outbound.

    Returns:
      Parsed JSON response (typically the created message envelope).

    Raises:
      MaxAPIError: non-2xx OR network error.
    """

    token = _token()
    if not token:
        # Empty token — fail loudly. The legacy path returned False and
        # the caller had to inspect; in the new pipeline we'd rather
        # surface this via an exception so the handler emits a clear
        # `channels.max.outbound.no_token` audit.
        raise MaxAPIError(0, "MAX_BOT_TOKEN is not configured")

    body: dict[str, Any] = {"text": text}
    if attachments:
        body["attachments"] = attachments

    url = f"{_api_base()}/messages"
    headers = {
        "Authorization": token,  # MAX uses raw token, not Bearer
        "Content-Type": "application/json",
    }
    params = {"chat_id": chat_id}

    try:
        response = httpx.post(
            url,
            headers=headers,
            params=params,
            json=body,
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        # Connection refused, DNS failure, timeout, etc.
        logger.warning(
            "channels.max.outbound.network_error chat_id=%s exc=%s",
            chat_id,
            exc,
        )
        raise MaxAPIError(0, str(exc)) from exc

    if response.status_code >= 400:
        logger.warning(
            "channels.max.outbound.http_error chat_id=%s status=%s body=%r",
            chat_id,
            response.status_code,
            response.text[:200],
        )
        raise MaxAPIError(response.status_code, response.text)

    # 2xx — parse JSON. MAX returns the created-message envelope.
    try:
        return response.json()
    except ValueError:
        # 2xx with non-JSON body shouldn't happen, but don't crash.
        logger.warning(
            "channels.max.outbound.non_json_2xx chat_id=%s status=%s",
            chat_id,
            response.status_code,
        )
        return {}
