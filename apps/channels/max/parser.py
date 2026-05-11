"""MAX webhook payload parser (DRF-440 / Sprint 2 / D1).

Translates the raw MAX webhook JSON into a channel-agnostic
:class:`CanonicalEvent` that downstream handlers (D3) consume without
knowing or caring about MAX-specific field names.

### Expected MAX webhook shape

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

Sprint 2 only handles `update_type=message_created`. Callback buttons
(`message_callback`) and other update types map to ParseError until
Sprint 3 adds the AI Concierge with full intent dispatch.

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
    """Translate a `message_created` MAX webhook into a CanonicalEvent.

    Args:
      payload: parsed JSON body of the POST to `/api/v1/ingress/max/`.

    Returns:
      A frozen :class:`CanonicalEvent` with the canonical fields filled.

    Raises:
      ParseError: ``update_type`` is missing or not ``message_created``,
                  OR any of the required nested fields are missing
                  (``message.sender.user_id``, ``message.recipient.chat_id``).
                  All other shape oddities (missing text, empty
                  attachments) are tolerated — empty defaults.
    """

    update_type = payload.get("update_type")
    if update_type != "message_created":
        raise ParseError(
            f"Unsupported MAX update_type={update_type!r}. Sprint 2 "
            "only handles 'message_created' — callback buttons and "
            "other event types land in Sprint 3."
        )

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
    timestamp_ms = payload.get("timestamp")
    timestamp: datetime | None = None
    if isinstance(timestamp_ms, (int, float)):
        # MAX sends epoch milliseconds; normalise to UTC datetime.
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)

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
