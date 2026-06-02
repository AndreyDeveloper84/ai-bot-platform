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


# MAX bot indicator actions. POST /chats/{chat_id}/actions with
# `{"action": "<value>"}`. Per dev.max.ru/docs-api/methods/POST/chats/-chatId-/actions
# + the legacy maxapi SDK's SenderAction enum.
_CHAT_ACTIONS = {
    "typing_on": "typing_on",
    "mark_seen": "mark_seen",
    "sending_photo": "sending_photo",
    "sending_video": "sending_video",
    "sending_audio": "sending_audio",
    "sending_file": "sending_file",
}


def send_chat_action(*, chat_id: str, action: str, timeout: float = 5.0) -> None:
    """Fire a MAX chat-indicator action (typing_on / mark_seen / …).

    Best-effort: any non-2xx or network failure logs a warning but does
    NOT raise — these indicators are UX polish, never the core path. We
    don't want a typing-indicator hiccup to abort the message reply.

    Args:
      chat_id: stringified MAX chat id.
      action: one of :data:`_CHAT_ACTIONS` keys. Unknown values logged + dropped.
      timeout: request timeout. Short (5s) — indicators are
               fire-and-forget; a hung indicator must not delay reply.
    """
    if action not in _CHAT_ACTIONS:
        logger.warning("channels.max.outbound.unknown_action action=%r", action)
        return

    token = _token()
    if not token:
        return  # send_message would raise; indicators are silent.

    url = f"{_api_base()}/chats/{chat_id}/actions"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(
            url,
            headers=headers,
            json={"action": _CHAT_ACTIONS[action]},
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        logger.info(
            "channels.max.outbound.action_network_error chat_id=%s action=%s exc=%s",
            chat_id,
            action,
            exc,
        )
        return

    if response.status_code >= 400:
        logger.info(
            "channels.max.outbound.action_http_error chat_id=%s action=%s status=%s",
            chat_id,
            action,
            response.status_code,
        )


# ─── inline keyboard wire-format ──────────────────────────────────────────


# MAX-hardening Guard 2 — MAX's inline_keyboard supports a maximum of 29
# rows (per dev.max.ru/docs-api response observed via 400 на 30-row
# payloads). Clamping at producer boundary avoids silent message drops
# для list-heavy keyboards (slot pickers с >29 dates, master rosters,
# etc). Callers that emit more rows than this cap currently lose ALL
# rows past index 28 — the bot sends NO message, MAX returns 400, and
# the operator sees nothing besides an INFO log. Cap + log warns operators
# so they can either compose / paginate or accept silent truncation.
MAX_KEYBOARD_ROWS = 29


def _clamp_keyboard_rows(
    rows: list[list[dict[str, Any]]],
    *,
    context: str,
) -> list[list[dict[str, Any]]]:
    """Truncate keyboard rows к MAX's hard limit, logging when it bites.

    Returns the input unchanged if within cap. Otherwise returns the first
    ``MAX_KEYBOARD_ROWS`` rows + emits a WARNING с the caller-supplied
    ``context`` slug for grep-correlation.
    """
    if len(rows) <= MAX_KEYBOARD_ROWS:
        return rows
    logger.warning(
        "channels.max.keyboard_rows_truncated context=%s rows_in=%d cap=%d",
        context,
        len(rows),
        MAX_KEYBOARD_ROWS,
    )
    return rows[:MAX_KEYBOARD_ROWS]


def make_inline_keyboard_attachment(
    buttons: list[dict[str, str]],
    *,
    columns: int = 1,
) -> dict[str, Any]:
    """Build a MAX ``inline_keyboard`` attachment from the channel-agnostic list.

    The platform's keyboard contract (see
    :mod:`apps.orchestrator.ui.keyboards`) emits ``[{"label": ..., "callback": ...}]``
    — channel-blind. MAX's wire shape nests the buttons in a 2-D matrix
    inside an attachment::

        {
          "type": "inline_keyboard",
          "payload": {
            "buttons": [
              [{"type": "callback", "text": "📅 Записаться", "payload": "cb:welcome:book"}],
              [{"type": "callback", "text": "ℹ️ Услуги",   "payload": "cb:welcome:services"}]
            ]
          }
        }

    ``columns`` flows the flat list into a grid. ``columns=1`` (default)
    stacks vertically; ``columns=2`` pairs them. Callers that need a
    hand-rolled layout use :func:`make_inline_keyboard_attachment_rows`.
    """
    if columns < 1:
        raise ValueError(f"columns must be >= 1, got {columns}")
    rows: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for btn in buttons:
        current.append(_button_to_max(btn))
        if len(current) >= columns:
            rows.append(current)
            current = []
    if current:
        rows.append(current)
    rows = _clamp_keyboard_rows(rows, context="flat")
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def make_inline_keyboard_attachment_rows(
    rows: list[list[dict[str, str]]],
) -> dict[str, Any]:
    """Like :func:`make_inline_keyboard_attachment` but takes pre-shaped rows.

    Use when the caller has already decided which buttons share a row
    (e.g. paired Confirm/Cancel pair followed by a wider Back button).
    """
    wire_rows = [[_button_to_max(b) for b in row] for row in rows]
    wire_rows = _clamp_keyboard_rows(wire_rows, context="rows")
    return {
        "type": "inline_keyboard",
        "payload": {"buttons": wire_rows},
    }


def _button_to_max(btn: dict[str, Any]) -> dict[str, Any]:
    """Convert one channel-agnostic button dict to MAX's wire format.

    Channel-agnostic shape is ``{"label": "📅 Записаться", "callback": "cb:welcome:book"}``.
    MAX wire shape distinguishes by ``type``:

      * ``"callback"`` (default) — bot receives a ``message_callback``
        update with the ``payload`` echoed back.
      * ``"link"`` — opens the URL in the user's external browser.
      * ``"open_app"`` — launches a MAX Mini App; requires either
        ``web_app`` (bot username) or ``contact_id``.
      * ``"request_contact"`` — asks user to share their phone /
        contact card; MAX sends a `message_created` with a
        `contact` attachment back to the bot (MAX-hardening Guard 1).

    The channel-agnostic dict accommodates the variants via optional
    keys: ``url`` selects link, ``web_app``/``contact_id`` selects open_app,
    ``request_contact=True`` selects request_contact. Defaults to callback
    when none are present.
    """
    text = btn.get("label") or ""
    if btn.get("url"):
        return {"type": "link", "text": text, "url": btn["url"]}
    if btn.get("request_contact"):
        # MAX-hardening Guard 1 — request_contact button. Per MAX docs
        # the wire shape is `{"type": "request_contact", "text": ...}`.
        # No payload field — the user's tap returns a contact attachment
        # via a regular `message_created` update, NOT a callback. Parser-
        # side tolerance (Guard 1 inbound half) ensures that downstream
        # `message_created` events с body.attachments=[{"type":"contact"}]
        # don't crash the dispatcher.
        return {"type": "request_contact", "text": text}
    if btn.get("web_app") or btn.get("contact_id"):
        out: dict[str, Any] = {"type": "open_app", "text": text}
        if btn.get("web_app"):
            out["web_app"] = btn["web_app"]
        if btn.get("contact_id"):
            out["contact_id"] = btn["contact_id"]
        if btn.get("callback"):
            # MAX `open_app` button supports a `payload` carried into
            # the Mini App's initData. Reuse the `callback` field so
            # producers don't have to learn two key names.
            #
            # MAX-hardening Guard 3 (memory `max_open_app_payload_format`):
            # MAX requires the payload к be a flat slug — NO `=`, NO
            # `&` (querystring shape gets HTTP 400 proto.payload + poisons
            # the consumer PEL). Validate at producer boundary so a bad
            # caller can't poison the channel.
            payload_str = str(btn["callback"])
            if "=" in payload_str or "&" in payload_str or "?" in payload_str:
                raise ValueError(
                    "MAX open_app payload must be a flat slug — no `=`, `&`, "
                    f"or `?`; got {payload_str!r}. Per memory "
                    "`max_open_app_payload_format`: querystring shape gets "
                    "HTTP 400 + poisons consumer PEL."
                )
            out["payload"] = payload_str
        return out
    return {"type": "callback", "text": text, "payload": btn.get("callback") or ""}
