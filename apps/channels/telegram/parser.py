"""Telegram webhook payload parser (Phase 1 / CH1, DRF-848).

Translates a raw Telegram ``Update`` JSON object into the same channel-
agnostic :class:`CanonicalEvent` shape the MAX parser emits. The
handler doesn't read Telegram-specific fields — it asks "who sent
what" — so the canonical DTO keeps the skill layer channel-blind.

### Telegram Update shape

Telegram's Bot API is documented at https://core.telegram.org/bots/api ;
the relevant update sub-objects for this adapter are:

- ``message`` — a regular text or media message in a private chat.
- ``callback_query`` — payload from an InlineKeyboard button tap.

Update types the bot DOES NOT handle and the parser returns ``None`` for
(no error, no log spam):

- ``channel_post`` / ``edited_channel_post`` — group/channel broadcasts.
- ``edited_message`` — Telegram fires a separate update when the user
  edits their own past message; we'd reply twice.
- ``poll`` / ``poll_answer`` — out of scope for the salon use case.
- ``chat_member`` / ``my_chat_member`` — membership bookkeeping.
- ``inline_query`` / ``chosen_inline_result`` — the bot is not registered
  for inline mode and Telegram won't send these unless we are.

### Canonical event mapping

================================  ===================================
Telegram field                    CanonicalEvent attribute
================================  ===================================
``message.from.id``               ``channel_user_id``
``message.chat.id``               ``chat_id``  (same as user_id in DM)
``message.message_id``            ``channel_message_id``
``message.text`` / ``.caption``   ``text``
``message.photo`` (list)          ``attachments`` (list of ``file_id``)
``callback_query.from.id``        ``channel_user_id``
``callback_query.message.chat.id``  ``chat_id``
``callback_query.data``           ``text`` (callback payload)
``callback_query.id``             stashed in ``raw["callback_query_id"]``
================================  ===================================

The handler needs ``callback_query.id`` to call
``answerCallbackQuery`` (Telegram requires this to clear the spinner
on the button). Rather than add a separate field, we stash it in the
``raw`` envelope under a known key so the handler can fish it out
without changing the cross-channel DTO.

### Why ``return None`` instead of raise ParseError

MAX's parser raises ``ParseError`` for unsupported update types and the
ingress view records an audit row. Telegram's update mix is dirtier —
``my_chat_member`` and ``chat_member`` fire on every bot block/unblock,
``edited_message`` fires on any edit, and the webhook view returns 200
always (per Telegram retry semantics). Returning ``None`` for ignored
types lets the webhook view skip with no log noise; only true malformed
payloads (e.g. no recognised sub-object at all) silently fall through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class CanonicalEvent:
    """Channel-agnostic message event — Telegram parser flavour.

    Shape is intentionally identical to ``apps.channels.max.parser``'s
    DTO so downstream handlers and skills don't have to branch on
    channel. Only difference: Telegram callback taps come through with
    ``text`` set to the button's ``callback_data`` payload — the handler
    pattern-matches on the ``cb:`` prefix the same way it does for MAX.
    """

    channel: str  # always "telegram" for this parser
    channel_user_id: str
    channel_message_id: str
    chat_id: str
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    timestamp: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_inbound(payload: dict[str, Any]) -> CanonicalEvent | None:
    """Translate a Telegram ``Update`` payload to a CanonicalEvent.

    Args:
      payload: parsed JSON body of the Telegram webhook POST. Telegram's
               envelope is ``{"update_id": ..., "<update_type>": {...}}``;
               we discriminate by which top-level key is present.

    Returns:
      A :class:`CanonicalEvent` for ``message`` (text / photo) and
      ``callback_query`` updates, or ``None`` for every other update
      type (the webhook view treats ``None`` as "ignore and 200").

      Also returns ``None`` for malformed payloads where the recognised
      sub-object lacks required nested fields (e.g. ``message`` without
      a ``from.id``) — Telegram doesn't retry on 4xx so we don't want
      to crash the view on a spec drift.
    """
    if not isinstance(payload, dict):
        return None

    if "callback_query" in payload:
        return _parse_callback_query(payload)
    if "message" in payload:
        return _parse_message(payload)

    # Every other update type — channel_post, edited_message, poll,
    # chat_member, my_chat_member, inline_query, etc. — is intentionally
    # ignored. Returning None lets the webhook view skip with 200.
    return None


def _parse_message(payload: dict[str, Any]) -> CanonicalEvent | None:
    """Translate a ``Update.message`` (or photo) update."""
    message = payload.get("message")
    if not isinstance(message, dict):
        return None

    sender = message.get("from")
    chat = message.get("chat")
    if not isinstance(sender, dict) or "id" not in sender:
        return None
    if not isinstance(chat, dict) or "id" not in chat:
        return None

    # Photo messages put the prompt in ``caption``; text messages in
    # ``text``. Either may be empty (photo without caption is valid).
    text = message.get("text") or message.get("caption") or ""

    photos = message.get("photo") or []
    # Telegram sends a list of ``PhotoSize`` objects in ascending size;
    # we preserve them all so the skill layer can pick the largest
    # ``file_id`` and call ``getFile`` itself.
    attachments: list[dict[str, Any]] = []
    if isinstance(photos, list) and photos:
        attachments = [
            {"type": "photo", "file_id": p.get("file_id"), "raw": p}
            for p in photos
            if isinstance(p, dict) and p.get("file_id")
        ]

    timestamp = _parse_unix_ts(message.get("date"))

    return CanonicalEvent(
        channel="telegram",
        channel_user_id=str(sender["id"]),
        channel_message_id=str(message.get("message_id", "")),
        chat_id=str(chat["id"]),
        text=text,
        attachments=attachments,
        timestamp=timestamp,
        raw=payload,
    )


def _parse_callback_query(payload: dict[str, Any]) -> CanonicalEvent | None:
    """Translate a ``Update.callback_query`` (inline button tap)."""
    cb = payload.get("callback_query")
    if not isinstance(cb, dict):
        return None

    sender = cb.get("from")
    if not isinstance(sender, dict) or "id" not in sender:
        return None

    # ``callback_query.message`` is the message the button was attached
    # to — we pull the chat_id from there. Telegram guarantees this
    # field is present for callback_data taps (it's absent only for
    # game-mode callbacks, which we don't enable).
    cb_message = cb.get("message") or {}
    chat = cb_message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        # Defensive: refuse the tap if we can't address a reply back.
        # The webhook view still returns 200; nothing to do.
        return None

    data = cb.get("data") or ""

    # Stash the callback_query.id in raw so the handler can
    # ``answerCallbackQuery`` to clear the spinner. Keeping it inside
    # ``raw`` avoids changing the cross-channel DTO shape.
    raw = dict(payload)
    raw["callback_query_id"] = cb.get("id", "")

    return CanonicalEvent(
        channel="telegram",
        channel_user_id=str(sender["id"]),
        channel_message_id=str(cb_message.get("message_id", "")),
        chat_id=str(chat_id),
        text=str(data),
        attachments=[],
        timestamp=_parse_unix_ts(cb_message.get("date")),
        raw=raw,
    )


def _parse_unix_ts(value: Any) -> datetime | None:
    """Convert Telegram's UNIX-seconds timestamp to a UTC datetime."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return None
