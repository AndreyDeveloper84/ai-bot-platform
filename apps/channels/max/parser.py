"""MAX webhook payload parser (DRF-440 / Sprint 2 / D1).

Translates the raw MAX webhook JSON into a channel-agnostic
:class:`CanonicalEvent` that downstream handlers (D3) consume without
knowing or caring about MAX-specific field names.

### Expected MAX webhook shapes

Per dev.max.ru docs and the legacy `mysite/maxbot/handlers/ai_assistant.py`
field reads, a `message_created` update looks like::

    {
      "update_type": "message_created",
      "timestamp": 1731320000000,
      "message": {
        "sender": {"user_id": 12345, "name": "Иван", "lang": "ru"},
        "recipient": {"chat_id": 67890, "chat_type": "dialog"},
        "body": {"mid": "msg-uuid", "seq": 1, "text": "Привет", "attachments": []}
      }
    }

A `message_callback` update (user tapped an inline-keyboard button) looks like::

    {
      "update_type": "message_callback",
      "timestamp": 1731320000000,
      "callback": {
        "timestamp": 1731320000000,
        "callback_id": "cb-uuid",
        "payload": "cb:welcome:book",
        "user": {"user_id": 12345, "name": "Иван", "lang": "ru"}
      },
      "message": {
        "recipient": {"chat_id": 67890, "chat_type": "dialog"},
        "body": {"mid": "msg-uuid", ...}
      },
      "user_locale": "ru"
    }

The callback payload becomes ``CanonicalEvent.text`` (channel-agnostic
convention: skills match on ``text.startswith("cb:")`` exactly the way
they do for Telegram callbacks; see ``apps.channels.telegram.parser``).
``callback_id`` is stashed in ``raw["callback_id"]`` so the handler can
use it for the idempotency key + a future ``/answers`` ack call.

Every other update type maps to ParseError.

### Why a CanonicalEvent DTO instead of using the raw payload directly

The handler (D3) doesn't read MAX-specific fields — it asks "who sent
what". Channel-specific shape would couple D3 to MAX's wire format and
would require rewriting on Sprint 3's first non-MAX channel. The
ChannelAdapter ABC (`apps/channels/base.py`) is intentionally deferred
to Sprint 3 — at one channel today, an ABC is premature abstraction —
but the DTO is the data shape it will standardise around.

### `raw` field preserves the entire payload

For Sprint 5 replay tooling: re-run an old event through a new pipeline
version requires the original JSON, not just our canonical extraction.
Storage cost is small (one TEXT column per Stream entry) and the
replay value is large.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class CanonicalEvent:
    """Channel-agnostic message event.

    All fields are derived from the source channel's webhook shape.
    String-typed for cross-channel uniformity: a `channel_user_id`
    that is an int in MAX, an opaque string in WhatsApp, and a signed
    int in Telegram all stringify cleanly.
    """

    channel: str  # always "max" for this parser; D1 sibling parsers (Sprint 3) set "telegram", etc.
    channel_user_id: str
    channel_message_id: str
    chat_id: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    timestamp: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ParseError(Exception):
    """Malformed or unsupported MAX webhook payload."""


def parse_max_webhook(payload: dict[str, Any]) -> CanonicalEvent:
    """Translate a MAX webhook update into a CanonicalEvent.

    Args:
      payload: parsed JSON body of the POST to `/api/v1/ingress/max/`.

    Returns:
      A frozen :class:`CanonicalEvent` with the canonical fields filled.

    Raises:
      ParseError: ``update_type`` is missing or not one of the supported
                  values, OR required nested fields are missing.
                  Tolerated shape oddities (missing text, empty
                  attachments) fall back to empty defaults.
    """

    update_type = payload.get("update_type")
    if update_type == "message_created":
        return _parse_message_created(payload)
    if update_type == "message_callback":
        return _parse_message_callback(payload)
    if update_type == "bot_started":
        return _parse_bot_started(payload)
    raise ParseError(
        f"Unsupported MAX update_type={update_type!r}. Supported: "
        "'message_created', 'message_callback', 'bot_started'."
    )


def _parse_message_created(payload: dict[str, Any]) -> CanonicalEvent:
    """Translate a `message_created` update."""
    message = payload.get("message")
    if not isinstance(message, dict):
        raise ParseError("MAX payload missing required field: message")

    sender = message.get("sender")
    if not isinstance(sender, dict) or "user_id" not in sender:
        raise ParseError("MAX payload missing required field: message.sender.user_id")

    recipient = message.get("recipient")
    if not isinstance(recipient, dict) or "chat_id" not in recipient:
        raise ParseError("MAX payload missing required field: message.recipient.chat_id")

    body = message.get("body") or {}
    text = body.get("text") or ""
    attachments = body.get("attachments") or []
    if not isinstance(attachments, list):
        # Tolerate non-list attachment field — log via raw, treat as empty.
        attachments = []

    channel_message_id = str(body.get("mid") or body.get("seq") or "")
    timestamp = _parse_ms_ts(payload.get("timestamp"))

    return CanonicalEvent(
        channel="max",
        channel_user_id=str(sender["user_id"]),
        channel_message_id=channel_message_id,
        chat_id=str(recipient["chat_id"]),
        text=text,
        attachments=attachments,
        timestamp=timestamp,
        raw=payload,
    )


def _parse_message_callback(payload: dict[str, Any]) -> CanonicalEvent:
    """Translate a `message_callback` update.

    The callback ``payload`` is folded into ``text`` so skill ``matches()``
    predicates that look at ``text.startswith("cb:")`` light up the same
    way they do for the Telegram channel. The ``callback_id`` is stashed
    in ``raw["callback_id"]`` for handler-level use (idempotency, the
    optional ``/answers`` ack).
    """
    callback = payload.get("callback")
    if not isinstance(callback, dict):
        raise ParseError("MAX callback payload missing required field: callback")

    user = callback.get("user")
    if not isinstance(user, dict) or "user_id" not in user:
        raise ParseError("MAX callback payload missing required field: callback.user.user_id")

    # `chat_id` lives on the ORIGINAL message the button was attached to —
    # this is the bot's reply that carried the inline keyboard. MAX always
    # includes it for callback events fired by inline_keyboard buttons.
    message = payload.get("message") or {}
    recipient = message.get("recipient") or {}
    chat_id = recipient.get("chat_id")
    if chat_id is None:
        raise ParseError("MAX callback payload missing required field: message.recipient.chat_id")

    callback_id = str(callback.get("callback_id") or "")
    if not callback_id:
        raise ParseError("MAX callback payload missing required field: callback.callback_id")

    # `payload` may legitimately be None — defensive empty string.
    cb_payload = callback.get("payload") or ""

    # Surface callback_id via a raw copy so the handler can pull it without
    # mutating the original dict (consumer Stream entry must not be touched).
    raw = dict(payload)
    raw["callback_id"] = callback_id

    # Per-callback timestamp wins over the envelope timestamp; both are
    # epoch ms in the MAX schema.
    timestamp = _parse_ms_ts(callback.get("timestamp") or payload.get("timestamp"))

    body = message.get("body") or {}
    channel_message_id = str(body.get("mid") or body.get("seq") or "")

    return CanonicalEvent(
        channel="max",
        channel_user_id=str(user["user_id"]),
        channel_message_id=channel_message_id,
        chat_id=str(chat_id),
        text=str(cb_payload),
        attachments=[],
        timestamp=timestamp,
        raw=raw,
    )


def _parse_bot_started(payload: dict[str, Any]) -> CanonicalEvent:
    """Translate a ``bot_started`` lifecycle event.

    MAX fires this when a new user activates the bot for the first time
    (taps «Начать» in the bot card). It is the channel-level analog of
    Telegram's ``/start`` text. We synthesise ``text="/start"`` on the
    canonical event so the welcome skill matches via its existing
    predicate — no special-case branch in the skill layer.

    Shape per dev.max.ru/docs-api::

        {
          "update_type": "bot_started",
          "timestamp": 1731320000000,
          "chat_id": 67890,
          "user": {"user_id": 12345, "name": "Иван", "lang": "ru"},
          "payload": "<deeplink param, optional>",
          "user_locale": "ru"
        }
    """
    user = payload.get("user")
    if not isinstance(user, dict) or "user_id" not in user:
        raise ParseError("MAX bot_started payload missing required field: user.user_id")

    chat_id = payload.get("chat_id")
    if chat_id is None:
        raise ParseError("MAX bot_started payload missing required field: chat_id")

    timestamp = _parse_ms_ts(payload.get("timestamp"))

    # The deeplink ``payload`` (e.g. ``ref_user_12345`` / ``qr_42_window``
    # / ``ig_post_99``) carries acquisition + multi-tenant context. Fold
    # it into the synthetic ``/start`` text so the welcome skill can
    # detect S1 multi-tenant variant без changing SkillContext shape
    # (cross-cutting refactor). Empty payload → text="/start" verbatim
    # (backward compat with attribution-only flows и existing tests).
    # Also stash on ``raw`` для downstream attribution skill access.
    #
    # Whitespace: only edge whitespace stripped via .strip(); internal
    # whitespace в crafted deeplinks preserved through suffix, stripped
    # again at extraction time. Prefix-match against ASCII-only Tau
    # patterns (ref_/qr_/ig_post_) means Unicode / emoji payloads fall
    # through to standard WELCOME_TEXT safely (CR #810 nits #6 + #7).
    deeplink_payload = payload.get("payload") or ""
    synthetic_text = f"/start {deeplink_payload}".strip() if deeplink_payload else "/start"
    return CanonicalEvent(
        channel="max",
        channel_user_id=str(user["user_id"]),
        # Synthetic id — MAX doesn't give us a stable update id for
        # lifecycle events. Prefix so idempotency keys can't collide
        # with a real message mid.
        channel_message_id=f"bot_started:{user['user_id']}:{payload.get('timestamp', '')}",
        chat_id=str(chat_id),
        text=synthetic_text,
        attachments=[],
        timestamp=timestamp,
        raw=payload,
    )


def _parse_ms_ts(value: Any) -> datetime | None:
    """Convert MAX's epoch-milliseconds timestamp to a UTC datetime."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    return None
