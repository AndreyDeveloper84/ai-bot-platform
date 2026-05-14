"""external_user_id mapping tests (DRF-826 / Sprint 9 / I2).

Pure-function tests — no DB, no network. ``BotUser`` is constructed
in-memory (not ``Model.objects.create``) so we don't need ``@pytest.mark.django_db``.
"""

from __future__ import annotations

import pytest

from apps.integrations.ayla.user_proxy import (
    external_user_id_for,
    parse_external_user_id,
)


class _Stub:
    """Minimal BotUser stand-in. The helper only reads two attributes; we
    avoid pulling in DB setup for what is a pure-string transformation.
    """

    def __init__(self, channel: str, channel_user_id: str) -> None:
        self.channel = channel
        self.channel_user_id = channel_user_id


class TestExternalUserIdFor:
    def test_max_channel(self) -> None:
        assert external_user_id_for(_Stub("max", "12345")) == "bot:max:12345"  # type: ignore[arg-type]

    def test_telegram_channel(self) -> None:
        assert (
            external_user_id_for(_Stub("telegram", "987654321"))  # type: ignore[arg-type]
            == "bot:telegram:987654321"
        )

    def test_channel_isolation_same_id(self) -> None:
        """MAX user 12345 must NOT collide with Telegram user 12345.

        This is the multi-channel adjustment from the mysite source — see
        module docstring.
        """
        max_id = external_user_id_for(_Stub("max", "12345"))  # type: ignore[arg-type]
        tg_id = external_user_id_for(_Stub("telegram", "12345"))  # type: ignore[arg-type]
        assert max_id != tg_id

    def test_negative_chat_id_passes_through(self) -> None:
        """Telegram group chats use negative IDs. We don't slugify silently —
        if Ayla rejects it that's a channel-adapter problem.
        """
        assert (
            external_user_id_for(_Stub("telegram", "-100123456"))  # type: ignore[arg-type]
            == "bot:telegram:-100123456"
        )


class TestParseExternalUserId:
    def test_round_trip(self) -> None:
        encoded = external_user_id_for(_Stub("max", "12345"))  # type: ignore[arg-type]
        assert parse_external_user_id(encoded) == ("max", "12345")

    def test_telegram_round_trip(self) -> None:
        encoded = external_user_id_for(_Stub("telegram", "-100123456"))  # type: ignore[arg-type]
        assert parse_external_user_id(encoded) == ("telegram", "-100123456")

    @pytest.mark.parametrize(
        "malformed",
        [
            "",  # empty
            "12345",  # no prefix
            "bot:",  # missing both fields
            "bot:max:",  # empty channel_user_id
            "bot::12345",  # empty channel
            "not-a-bot:max:12345",  # wrong prefix
        ],
    )
    def test_malformed_returns_none(self, malformed: str) -> None:
        assert parse_external_user_id(malformed) is None
