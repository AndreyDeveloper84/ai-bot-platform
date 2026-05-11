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
        with pytest.raises(ParseError, match="message_created"):
            parse_max_webhook({"update_type": "message_callback", "message": {}})

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
