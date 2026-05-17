"""Telegram webhook parser tests (Phase 1 / CH1, DRF-848).

Pin the parser contract documented in :mod:`apps.channels.telegram.parser`:

* text message → CanonicalEvent with text + chat_id, has_attachments=False
* photo message → CanonicalEvent with attachments populated (file_ids preserved)
* callback_query → CanonicalEvent with text=callback_data, raw[callback_query_id] set
* unsupported update types → None
"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.channels.telegram.parser import CanonicalEvent, parse_inbound


def _message_payload(**overrides):
    """Build a minimal valid Telegram text message Update."""
    base = {
        "update_id": 100001,
        "message": {
            "message_id": 42,
            "date": 1731320000,
            "from": {"id": 12345, "is_bot": False, "first_name": "Иван"},
            "chat": {"id": 12345, "type": "private"},
            "text": "Привет",
        },
    }
    base.update(overrides)
    return base


def _photo_payload():
    return {
        "update_id": 200001,
        "message": {
            "message_id": 43,
            "date": 1731320001,
            "from": {"id": 12345, "is_bot": False, "first_name": "Иван"},
            "chat": {"id": 12345, "type": "private"},
            "photo": [
                {"file_id": "AgACAgIAAxk_small", "file_unique_id": "u1", "width": 90, "height": 90},
                {
                    "file_id": "AgACAgIAAxk_large",
                    "file_unique_id": "u2",
                    "width": 1280,
                    "height": 1280,
                },
            ],
            "caption": "проверьте состав",
        },
    }


def _callback_payload():
    return {
        "update_id": 300001,
        "callback_query": {
            "id": "cqid-abc",
            "from": {"id": 12345, "is_bot": False, "first_name": "Иван"},
            "message": {
                "message_id": 99,
                "date": 1731320002,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 555, "is_bot": True, "first_name": "Bot"},
                "text": "(button row)",
            },
            "data": "cb:rem:confirm:abc-uuid",
            "chat_instance": "ci-1",
        },
    }


class TestParseText:
    """Behaviour 1: text message → CanonicalEvent with chat_id + has_attachments=False."""

    def test_text_message_basic_fields(self):
        ev = parse_inbound(_message_payload())
        assert isinstance(ev, CanonicalEvent)
        assert ev.channel == "telegram"
        assert ev.channel_user_id == "12345"
        assert ev.chat_id == "12345"
        assert ev.text == "Привет"
        assert ev.attachments == []
        assert ev.channel_message_id == "42"

    def test_text_timestamp_parsed_as_utc(self):
        ev = parse_inbound(_message_payload())
        assert ev is not None
        assert ev.timestamp == datetime.fromtimestamp(1731320000, tz=timezone.utc)

    def test_text_raw_preserved(self):
        payload = _message_payload()
        ev = parse_inbound(payload)
        assert ev is not None
        assert ev.raw["update_id"] == 100001

    def test_negative_chat_id_stringified(self):
        """Telegram group chat IDs are large negative ints — must stringify cleanly."""
        ev = parse_inbound(
            _message_payload(
                message={
                    "message_id": 5,
                    "date": 1700000000,
                    "from": {"id": 7, "is_bot": False, "first_name": "X"},
                    "chat": {"id": -100123456, "type": "supergroup"},
                    "text": "x",
                }
            )
        )
        assert ev is not None
        assert isinstance(ev.chat_id, str)
        assert ev.chat_id == "-100123456"


class TestParsePhoto:
    """Behaviour 2: photo → has_attachments=True with file_ids preserved."""

    def test_photo_attachments_populated(self):
        ev = parse_inbound(_photo_payload())
        assert ev is not None
        assert len(ev.attachments) == 2
        # Both file_ids preserved in order — handler / skill picks largest.
        assert ev.attachments[0]["file_id"] == "AgACAgIAAxk_small"
        assert ev.attachments[1]["file_id"] == "AgACAgIAAxk_large"

    def test_photo_caption_becomes_text(self):
        """Photo prompts arrive in caption — must surface as event.text."""
        ev = parse_inbound(_photo_payload())
        assert ev is not None
        assert ev.text == "проверьте состав"

    def test_photo_without_caption_has_empty_text(self):
        payload = _photo_payload()
        payload["message"].pop("caption")
        ev = parse_inbound(payload)
        assert ev is not None
        assert ev.text == ""
        assert len(ev.attachments) == 2

    def test_photo_attachment_type_marker(self):
        """Skill layer reads attachment['type'] to pick its branch."""
        ev = parse_inbound(_photo_payload())
        assert ev is not None
        assert all(a["type"] == "photo" for a in ev.attachments)


class TestParseCallback:
    """Behaviour 3: callback_query → text=callback_data, raw carries cqid."""

    def test_callback_data_in_text(self):
        ev = parse_inbound(_callback_payload())
        assert ev is not None
        assert ev.text == "cb:rem:confirm:abc-uuid"

    def test_callback_query_id_stashed_in_raw(self):
        ev = parse_inbound(_callback_payload())
        assert ev is not None
        # Handler reads raw["callback_query_id"] to call answerCallbackQuery.
        assert ev.raw["callback_query_id"] == "cqid-abc"

    def test_callback_chat_id_from_attached_message(self):
        ev = parse_inbound(_callback_payload())
        assert ev is not None
        # chat_id must come from callback_query.message.chat.id, not from .from
        assert ev.chat_id == "12345"

    def test_callback_channel_user_id_from_tapper(self):
        ev = parse_inbound(_callback_payload())
        assert ev is not None
        # The tapper, not the bot author of the original message.
        assert ev.channel_user_id == "12345"


class TestParseIgnoredUpdateTypes:
    """Behaviour 4: channel_post / edited_message / poll → None."""

    def test_channel_post_returns_none(self):
        assert (
            parse_inbound({"update_id": 1, "channel_post": {"message_id": 1, "chat": {"id": 1}}})
            is None
        )

    def test_edited_message_returns_none(self):
        # Telegram fires edited_message when the user edits their own past
        # text; we'd reply twice if we treated it like a fresh message.
        assert (
            parse_inbound({"update_id": 1, "edited_message": {"message_id": 1, "chat": {"id": 1}}})
            is None
        )

    def test_poll_returns_none(self):
        assert parse_inbound({"update_id": 1, "poll": {"id": "x"}}) is None

    def test_chat_member_returns_none(self):
        assert parse_inbound({"update_id": 1, "my_chat_member": {"chat": {"id": 1}}}) is None

    def test_empty_payload_returns_none(self):
        assert parse_inbound({}) is None

    def test_non_dict_payload_returns_none(self):
        assert parse_inbound(None) is None  # type: ignore[arg-type]
        assert parse_inbound("not a dict") is None  # type: ignore[arg-type]


class TestMalformedPayloads:
    """Defensive: bad nested shape → None, never raise."""

    def test_message_missing_from_returns_none(self):
        assert (
            parse_inbound({"update_id": 1, "message": {"message_id": 1, "chat": {"id": 1}}}) is None
        )

    def test_message_missing_chat_returns_none(self):
        assert (
            parse_inbound({"update_id": 1, "message": {"message_id": 1, "from": {"id": 1}}}) is None
        )

    def test_callback_without_chat_returns_none(self):
        """A spec drift where callback_query.message is missing chat → None."""
        assert (
            parse_inbound(
                {
                    "update_id": 1,
                    "callback_query": {
                        "id": "x",
                        "from": {"id": 1},
                        "data": "cb:x",
                        # message absent
                    },
                }
            )
            is None
        )
