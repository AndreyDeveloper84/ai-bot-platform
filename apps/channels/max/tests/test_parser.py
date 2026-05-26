"""MAX webhook parser tests (DRF-440 / Sprint 2 / D1)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.channels.max.parser import CanonicalEvent, ParseError, parse_max_webhook


def _message_created_payload(**overrides) -> dict:
    """Build a minimal MAX `message_created` payload that the parser accepts."""

    base = {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": 12345, "name": "Иван"},
            "recipient": {"chat_id": 67890, "chat_type": "dialog"},
            "body": {"mid": "msg-uuid-1", "seq": 1, "text": "Привет", "attachments": []},
        },
    }
    base.update(overrides)
    return base


class TestParseMaxWebhookHappyPath:
    def test_text_message(self):
        ev = parse_max_webhook(_message_created_payload())

        assert isinstance(ev, CanonicalEvent)
        assert ev.channel == "max"
        assert ev.channel_user_id == "12345"
        assert ev.chat_id == "67890"
        assert ev.channel_message_id == "msg-uuid-1"
        assert ev.text == "Привет"
        assert ev.attachments == []
        assert ev.timestamp == datetime.fromtimestamp(1731320000.0, tz=timezone.utc)
        # Raw preserved for replay (Sprint 5).
        assert ev.raw["update_type"] == "message_created"

    def test_empty_text_tolerated(self):
        payload = _message_created_payload(
            message={
                "sender": {"user_id": 5},
                "recipient": {"chat_id": 9},
                "body": {"mid": "m", "attachments": []},
            }
        )
        ev = parse_max_webhook(payload)
        assert ev.text == ""

    def test_attachments_preserved(self):
        payload = _message_created_payload(
            message={
                "sender": {"user_id": 5},
                "recipient": {"chat_id": 9},
                "body": {
                    "mid": "m",
                    "text": "",
                    "attachments": [
                        {"type": "image", "payload": {"url": "https://x/1.jpg"}},
                    ],
                },
            }
        )
        ev = parse_max_webhook(payload)
        assert len(ev.attachments) == 1
        assert ev.attachments[0]["type"] == "image"

    def test_non_list_attachments_treated_as_empty(self):
        """Defensive: a misbehaving MAX update with attachments=None
        must not blow up the parser — empty list, log via raw."""

        payload = _message_created_payload(
            message={
                "sender": {"user_id": 5},
                "recipient": {"chat_id": 9},
                "body": {"mid": "m", "text": "x", "attachments": None},
            }
        )
        ev = parse_max_webhook(payload)
        assert ev.attachments == []
        # Raw still preserves the original (None).
        assert ev.raw["message"]["body"]["attachments"] is None

    def test_channel_user_id_stringified(self):
        """Even when MAX sends an int user_id, we always emit a string
        for cross-channel uniformity."""

        ev = parse_max_webhook(
            _message_created_payload(
                message={
                    "sender": {"user_id": 999999999999},
                    "recipient": {"chat_id": 1},
                    "body": {"mid": "m"},
                }
            )
        )
        assert isinstance(ev.channel_user_id, str)
        assert ev.channel_user_id == "999999999999"

    def test_missing_timestamp_returns_none(self):
        payload = _message_created_payload()
        payload.pop("timestamp")
        ev = parse_max_webhook(payload)
        assert ev.timestamp is None


class TestParseMaxWebhookErrors:
    def test_unsupported_update_type_raises(self):
        # `message_chat_created` is a real MAX update type we don't handle
        # (group-chat lifecycle event). Stays the canonical "unsupported".
        with pytest.raises(ParseError, match="message_chat_created"):
            parse_max_webhook({"update_type": "message_chat_created", "message": {}})

    def test_missing_message_raises(self):
        with pytest.raises(ParseError, match="message"):
            parse_max_webhook({"update_type": "message_created"})

    def test_missing_sender_user_id_raises(self):
        with pytest.raises(ParseError, match="sender.user_id"):
            parse_max_webhook(
                _message_created_payload(
                    message={
                        "sender": {},
                        "recipient": {"chat_id": 1},
                        "body": {},
                    }
                )
            )

    def test_missing_recipient_chat_id_raises(self):
        with pytest.raises(ParseError, match="recipient.chat_id"):
            parse_max_webhook(
                _message_created_payload(
                    message={
                        "sender": {"user_id": 1},
                        "recipient": {},
                        "body": {},
                    }
                )
            )

    def test_non_dict_message_raises(self):
        with pytest.raises(ParseError):
            parse_max_webhook({"update_type": "message_created", "message": "not a dict"})


def _message_callback_payload(**overrides) -> dict:
    """Build a minimal MAX `message_callback` payload that the parser accepts."""

    base = {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "timestamp": 1731320000500,
            "callback_id": "cb-uuid-1",
            "payload": "cb:welcome:book",
            "user": {"user_id": 12345, "name": "Иван", "lang": "ru"},
        },
        "message": {
            "recipient": {"chat_id": 67890, "chat_type": "dialog"},
            "body": {"mid": "msg-uuid-1", "seq": 1, "text": "Выберите раздел", "attachments": []},
        },
        "user_locale": "ru",
    }
    base.update(overrides)
    return base


class TestParseMaxWebhookCallback:
    """``message_callback`` decoder. The callback payload becomes ``text``
    so skill ``matches()`` predicates that look at ``text.startswith("cb:")``
    light up the same way they do for Telegram callbacks. ``callback_id``
    is stashed in ``raw`` for handler-level idempotency keying.
    """

    def test_callback_payload_becomes_text(self):
        ev = parse_max_webhook(_message_callback_payload())
        assert ev.text == "cb:welcome:book"
        assert ev.channel == "max"
        assert ev.channel_user_id == "12345"
        assert ev.chat_id == "67890"

    def test_callback_id_stashed_in_raw(self):
        ev = parse_max_webhook(_message_callback_payload())
        assert ev.raw["callback_id"] == "cb-uuid-1"

    def test_callback_timestamp_overrides_envelope(self):
        ev = parse_max_webhook(_message_callback_payload())
        # ``callback.timestamp`` (1731320000500ms) wins over the
        # envelope ``timestamp`` (1731320000000ms).
        assert ev.timestamp == datetime.fromtimestamp(1731320000.5, tz=timezone.utc)

    def test_envelope_timestamp_fallback_when_callback_missing_ts(self):
        payload = _message_callback_payload()
        payload["callback"].pop("timestamp")
        ev = parse_max_webhook(payload)
        assert ev.timestamp == datetime.fromtimestamp(1731320000.0, tz=timezone.utc)

    def test_empty_callback_payload_tolerated(self):
        """MAX permits a None ``callback.payload``. We coerce to empty
        string so downstream ``text.startswith("cb:")`` short-circuits
        cleanly without an AttributeError."""

        payload = _message_callback_payload()
        payload["callback"]["payload"] = None
        ev = parse_max_webhook(payload)
        assert ev.text == ""
        # No attachments on callback events.
        assert ev.attachments == []

    def test_missing_callback_raises(self):
        with pytest.raises(ParseError, match="callback"):
            parse_max_webhook({"update_type": "message_callback", "message": {}})

    def test_missing_callback_user_raises(self):
        payload = _message_callback_payload()
        payload["callback"].pop("user")
        with pytest.raises(ParseError, match="callback.user.user_id"):
            parse_max_webhook(payload)

    def test_missing_callback_id_raises(self):
        """callback_id is the idempotency key — refuse a payload that
        can't be deduped on retry."""

        payload = _message_callback_payload()
        payload["callback"].pop("callback_id")
        with pytest.raises(ParseError, match="callback.callback_id"):
            parse_max_webhook(payload)

    def test_missing_chat_id_raises(self):
        """No chat_id means we can't address a reply back — refuse."""

        payload = _message_callback_payload()
        payload["message"].pop("recipient")
        with pytest.raises(ParseError, match="recipient.chat_id"):
            parse_max_webhook(payload)


def _bot_started_payload(**overrides) -> dict:
    """Build a minimal MAX `bot_started` lifecycle payload."""
    base = {
        "update_type": "bot_started",
        "timestamp": 1731320000000,
        "chat_id": 67890,
        "user": {"user_id": 12345, "name": "Иван", "lang": "ru"},
        "user_locale": "ru",
    }
    base.update(overrides)
    return base


class TestParseMaxWebhookBotStarted:
    """``bot_started`` — first contact lifecycle event. Synthesised as
    ``text="/start"`` so welcome skill matches via its existing predicate.
    New-client onboarding works whether the MAX client also auto-sends
    a /start text or not (the welcome skill is idempotent on /start).
    """

    def test_synthesises_slash_start_text(self):
        ev = parse_max_webhook(_bot_started_payload())
        assert ev.text == "/start"
        assert ev.channel == "max"
        assert ev.channel_user_id == "12345"
        assert ev.chat_id == "67890"

    def test_synthetic_message_id_prefixed(self):
        """Synthetic channel_message_id must not collide with a real
        message mid — prefix with 'bot_started:' to keep the idempotency
        namespace clean."""

        ev = parse_max_webhook(_bot_started_payload())
        assert ev.channel_message_id.startswith("bot_started:12345:")

    def test_deeplink_payload_preserved_in_raw(self):
        """``payload`` carries an acquisition signal (e.g. ``ref=campaign``).
        Stash on ``raw`` so a downstream attribution skill can read it."""

        ev = parse_max_webhook(_bot_started_payload(payload="ref=ig-aug-2026"))
        assert ev.raw["payload"] == "ref=ig-aug-2026"

    def test_deeplink_payload_folded_into_synthetic_text(self):
        """Task #85 part 4 (PR 3) — multi-tenant S1 variant detection.

        Bot adapter folds the ``payload`` into the synthetic ``/start``
        text suffix so welcome skill can branch on ``ref_/qr_/ig_post_``
        prefixes без changing SkillContext shape. Backward compat: empty
        payload still yields ``/start`` verbatim (test_synthesises_slash_start_text)."""

        ev = parse_max_webhook(_bot_started_payload(payload="ref_user_42"))
        assert ev.text == "/start ref_user_42"
        # Raw still preserves payload too — attribution skills don't lose access.
        assert ev.raw["payload"] == "ref_user_42"

    def test_empty_deeplink_payload_yields_bare_start(self):
        """Defensive: empty-string payload (e.g. legacy MAX builds, broken
        deeplinks) MUST NOT produce ``/start `` (trailing space) — that'd
        be a different match pattern from baseline. Strip + fall through."""

        ev = parse_max_webhook(_bot_started_payload(payload=""))
        assert ev.text == "/start"

    def test_missing_user_raises(self):
        with pytest.raises(ParseError, match="user.user_id"):
            parse_max_webhook(_bot_started_payload(user={}))

    def test_missing_chat_id_raises(self):
        payload = _bot_started_payload()
        payload.pop("chat_id")
        with pytest.raises(ParseError, match="chat_id"):
            parse_max_webhook(payload)
