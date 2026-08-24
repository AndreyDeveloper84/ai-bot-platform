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
from typing import TYPE_CHECKING, Any

from django.conf import settings
import httpx

from apps.channels.bot_context import current_bot

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.channels.bot_registry import BotEntry

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


def _token(bot: "BotEntry | None" = None) -> str:
    """The API token to send as (DRF-1061).

    Precedence: explicit ``bot`` argument → the surrounding ``bot_scope``
    → ``settings.MAX_BOT_TOKEN``. The last step is what keeps every existing
    call site and deployment behaving exactly as before — a path that never
    opts in still sends as the single configured bot.

    Getting this wrong is not a crash but a wrong-sender message, so the
    fallback is deliberately the *old* behaviour rather than an error: a
    deployment with one bot has one possible answer, and guessing between
    several is never better than the configured default.
    """

    if bot is not None:
        return bot.api_token

    scoped = current_bot()
    if scoped is not None:
        return scoped.api_token

    return getattr(settings, "MAX_BOT_TOKEN", "")


def send_message(
    *,
    chat_id: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    timeout: float = 10.0,
    bot: "BotEntry | None" = None,
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
      bot: send as this bot (DRF-1061). Omit to use the surrounding
           ``bot_scope``, then the single configured bot. Pass it explicitly
           only when the sender is not implied by the context — otherwise
           prefer scoping, so intermediate layers cannot forget to forward it.

    Returns:
      Parsed JSON response (typically the created message envelope).

    Raises:
      MaxAPIError: non-2xx OR network error.
    """

    token = _token(bot)
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


# ─── in-place message edit ────────────────────────────────────────────────


# MAX's edit endpoint mirrors `send_message`'s quirk one field over: the id
# lives in the QUERY STRING, not the body, and the method is PUT.
#
#     PUT https://botapi.max.ru/messages?message_id={mid}
#     Authorization: {raw token}
#     {"text": "...", "attachments": [...]}
#
# The prior estimate for DRF-1362 said MAX "cannot edit a message, so every
# tap spawns a new one". That is not so, and the counter-example is in this
# repo: `legacy_maxbot/handlers/health_screening.py:243-320` has run a
# chronic-illness multi-select on `bot.edit_message(chat_id=…,
# message_id=callback.message.body.mid, attachments=[…])` since Phase 3.2A,
# with `legacy_maxbot/menu_state.py:51` using the same call to strip a
# keyboard off an older menu. The SDK there wraps exactly this REST call.
#
# The verb and path below are not read off the docs alone — MAX's own router
# was asked, unauthenticated, 2026-08-24:
#
#     PUT   /messages          -> 401 {"code": "verify.token"}
#     PATCH /messages          -> 404 {"code": "method.not.found"}
#     PUT   /nonexistent-route -> 404 {"code": "method.not.found"}
#
# The route resolves before the auth gate and resolves per-method, so a 401
# on PUT /messages means this exact endpoint exists and PATCH is not it.
#
# Two facts about failure that the send path does not have to know:
#
# 1. **MAX answers a refused edit with HTTP 200.** The body is MAX's
#    `SimpleQueryResult` — `{"success": false, "message": "..."}` — for the
#    ordinary reasons an edit is refused: the message is too old, it was
#    deleted, another bot owns it, or the per-chat edit budget (two per
#    second, per dev.max.ru/docs-api/methods/PUT/messages) is spent. A
#    `status_code >= 400` check alone reports those as successes and the
#    user sees a keyboard that never changed. We therefore treat
#    `success is False` as an error, same as a 4xx.
# 2. **A refusal must not cost the turn.** The legacy handler wraps the edit
#    in try/except and re-sends on failure, which is what keeps a tap from
#    disappearing. :func:`edit_message_or_send` is that behaviour, hoisted
#    out of the handler so it is a property of the channel rather than of
#    whichever caller remembered to write the fallback.
_EDIT_RATE_HINT = "MAX allows ~2 edits/sec per chat; over that the edit is refused."


def edit_message(
    *,
    message_id: str,
    text: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    timeout: float = 10.0,
    bot: "BotEntry | None" = None,
) -> dict[str, Any]:
    """PUT an edit to an already-sent MAX message.

    Args:
      message_id: the `mid` of the message to rewrite. On a callback event
                  this is ``CanonicalEvent.channel_message_id`` — the parser
                  fills it from ``message.body.mid`` for exactly this reason
                  (`apps/channels/max/parser.py:204`), i.e. the id of the
                  message the tapped keyboard was hanging under.
      text: new body. ``None`` omits the field entirely, which the API
            schema allows (`text` is optional on PUT /messages) — but note
            `legacy_maxbot/menu_state.py:17` records the opposite from the
            SDK's side ("edit_message требует text") and always passes the
            previous text back. Callers that only want to swap a keyboard
            are safer passing the original text than relying on omission.
      attachments: new attachment list in MAX wire format. An EMPTY list is
                   meaningful and IS sent: it strips the keyboard
                   (`legacy_maxbot/menu_state.py:51`). ``None`` omits the
                   field and leaves the existing attachments alone.
      timeout: request timeout seconds; same default as :func:`send_message`.
      bot: send as this bot (DRF-1061), same precedence as :func:`_token`.

    Returns:
      Parsed JSON response.

    Raises:
      MaxAPIError: non-2xx, network error, OR a 200 whose body says
        ``{"success": false}`` — see the note above; MAX reports a refused
        edit inside a successful HTTP response.
    """

    token = _token(bot)
    if not token:
        raise MaxAPIError(0, "MAX_BOT_TOKEN is not configured")

    body: dict[str, Any] = {}
    if text is not None:
        body["text"] = text
    if attachments is not None:
        # NOT `if attachments:` — [] strips the keyboard and must survive.
        body["attachments"] = attachments

    url = f"{_api_base()}/messages"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    params = {"message_id": message_id}

    try:
        response = httpx.put(
            url,
            headers=headers,
            params=params,
            json=body,
            timeout=timeout,
        )
    except httpx.RequestError as exc:
        logger.warning(
            "channels.max.outbound.edit_network_error message_id=%s exc=%s",
            message_id,
            exc,
        )
        raise MaxAPIError(0, str(exc)) from exc

    if response.status_code >= 400:
        logger.warning(
            "channels.max.outbound.edit_http_error message_id=%s status=%s body=%r",
            message_id,
            response.status_code,
            response.text[:200],
        )
        raise MaxAPIError(response.status_code, response.text)

    try:
        parsed = response.json()
    except ValueError:
        logger.warning(
            "channels.max.outbound.edit_non_json_2xx message_id=%s status=%s",
            message_id,
            response.status_code,
        )
        return {}

    if isinstance(parsed, dict) and parsed.get("success") is False:
        # The 200-shaped refusal. `success` is absent from the ordinary
        # success envelope, so only an explicit False counts — a missing key
        # must never be read as a failure.
        detail = str(parsed.get("message") or "")
        logger.warning(
            "channels.max.outbound.edit_refused message_id=%s detail=%r hint=%s",
            message_id,
            detail[:200],
            _EDIT_RATE_HINT,
        )
        raise MaxAPIError(response.status_code, detail or "edit refused (success=false)")

    return parsed if isinstance(parsed, dict) else {}


def edit_message_or_send(
    *,
    chat_id: str,
    message_id: str | None,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    timeout: float = 10.0,
    bot: "BotEntry | None" = None,
) -> bool:
    """Rewrite ``message_id`` in place; on ANY refusal send a new message.

    This is the whole point of the pair: the in-place edit is what makes a
    multi-select feel like one screen instead of a stack of near-identical
    messages, and the fallback is what stops a refused edit from eating the
    user's tap. MAX refuses edits routinely — an old message, a deleted one,
    or simply the second edit inside the same half-second — so a caller that
    only edits will silently drop turns.

    ``message_id=None`` (a path with no message to edit, e.g. the very first
    render) sends directly and is not an error.

    Returns:
      True if the existing message was edited, False if a new message was
      sent instead. Callers that track the "current" mid must re-read it
      from the send response when this returns False — the old mid is stale.

    Raises:
      MaxAPIError: only if the FALLBACK send also fails. An edit failure
        alone never propagates; it is logged and absorbed.
    """

    if message_id:
        try:
            edit_message(
                message_id=message_id,
                text=text,
                attachments=attachments,
                timeout=timeout,
                bot=bot,
            )
            return True
        except MaxAPIError as exc:
            logger.info(
                "channels.max.outbound.edit_fallback_send message_id=%s status=%s body=%r",
                message_id,
                exc.status_code,
                exc.body[:200],
            )

    send_message(
        chat_id=chat_id,
        text=text,
        attachments=attachments,
        timeout=timeout,
        bot=bot,
    )
    return False


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


def send_chat_action(
    *,
    chat_id: str,
    action: str,
    timeout: float = 5.0,
    bot: "BotEntry | None" = None,
) -> None:
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

    token = _token(bot)
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
