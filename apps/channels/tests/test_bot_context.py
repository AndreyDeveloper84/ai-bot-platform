"""Outbound bot identity — scope propagation and precedence (DRF-1061).

The failure this guards against is not a crash. It is the salon bot's user
receiving a reply **from the client bot**: a message that arrives, looks
fine in logs, and is wrong only to the person reading it. Tests that stub
the sender never see it, which is exactly why the identity is asserted here
at the token level.

Three properties are pinned:

* **precedence** — explicit argument beats scope beats the legacy setting;
* **no leak between messages** — a worker task handles many updates in
  sequence, and an identity left set would answer the next user as the
  previous user's bot;
* **the legacy path is untouched** — outside any scope, with no argument,
  sending is byte-for-byte what it was before this ticket.
"""

from __future__ import annotations

import httpx
import pytest

from apps.channels.bot_context import bot_scope, current_bot, reset_bot, set_bot
from apps.channels.bot_registry import BotEntry
from apps.channels.max import outbound

CLIENT_BOT = BotEntry(
    slug="client",
    webhook_secret="wh-client",  # pragma: allowlist secret
    api_token="token-client",  # pragma: allowlist secret
)
SALON_BOT = BotEntry(
    slug="salon",
    webhook_secret="wh-salon",  # pragma: allowlist secret
    api_token="token-salon",  # pragma: allowlist secret
    tenant_slug="formula-tela",
)
LEGACY_TOKEN = "token-legacy"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _legacy_token(settings):
    settings.MAX_BOT_TOKEN = LEGACY_TOKEN


@pytest.fixture(autouse=True)
def _clean_scope():
    """Fail loudly if a test leaves an identity set."""
    yield
    assert current_bot() is None, "bot scope leaked out of a test"


class TestTokenPrecedence:
    def test_no_bot_no_scope_uses_legacy_setting(self):
        assert outbound._token() == LEGACY_TOKEN

    def test_scope_overrides_legacy_setting(self):
        with bot_scope(SALON_BOT):
            assert outbound._token() == "token-salon"

    def test_explicit_argument_overrides_scope(self):
        with bot_scope(SALON_BOT):
            assert outbound._token(CLIENT_BOT) == "token-client"

    def test_scope_of_none_falls_back_to_legacy(self):
        with bot_scope(None):
            assert outbound._token() == LEGACY_TOKEN


class TestScopeHygiene:
    def test_scope_is_restored_on_exit(self):
        with bot_scope(SALON_BOT):
            assert current_bot() is SALON_BOT
        assert current_bot() is None

    def test_scope_is_restored_on_exception(self):
        # The finally is the point: a handler that raises must not leave the
        # next message in the same worker speaking as this bot.
        with pytest.raises(RuntimeError):
            with bot_scope(SALON_BOT):
                raise RuntimeError("handler blew up")

        assert current_bot() is None

    def test_scopes_nest_and_unwind(self):
        with bot_scope(CLIENT_BOT):
            assert current_bot() is CLIENT_BOT
            with bot_scope(SALON_BOT):
                assert current_bot() is SALON_BOT
            assert current_bot() is CLIENT_BOT
        assert current_bot() is None

    def test_manual_set_reset_pair(self):
        token = set_bot(SALON_BOT)
        assert current_bot() is SALON_BOT
        reset_bot(token)
        assert current_bot() is None


class TestWireIdentity:
    """What actually goes out on the wire, not just what _token returns."""

    def test_send_message_uses_the_scoped_bot_token(self, httpx_mock):
        httpx_mock.add_response(json={"message": {}})

        with bot_scope(SALON_BOT):
            outbound.send_message(chat_id="555", text="привет")

        request = httpx_mock.get_requests()[0]
        assert request.headers["Authorization"] == "token-salon"

    def test_send_message_explicit_bot_wins(self, httpx_mock):
        httpx_mock.add_response(json={"message": {}})

        with bot_scope(SALON_BOT):
            outbound.send_message(chat_id="555", text="привет", bot=CLIENT_BOT)

        assert httpx_mock.get_requests()[0].headers["Authorization"] == "token-client"

    def test_send_message_outside_any_scope_is_unchanged(self, httpx_mock):
        httpx_mock.add_response(json={"message": {}})

        outbound.send_message(chat_id="555", text="привет")

        assert httpx_mock.get_requests()[0].headers["Authorization"] == LEGACY_TOKEN

    def test_send_chat_action_follows_the_same_identity(self, httpx_mock):
        httpx_mock.add_response(json={})

        with bot_scope(SALON_BOT):
            outbound.send_chat_action(chat_id="555", action="typing_on")

        assert httpx_mock.get_requests()[0].headers["Authorization"] == "token-salon"

    def test_bot_without_token_raises_rather_than_sending_as_someone_else(self):
        # A registry entry with an empty api_token can exist (the legacy
        # fallback allows secret-without-token). Sending must fail loudly
        # instead of quietly falling back to another bot's credentials.
        tokenless = BotEntry(
            slug="tokenless",
            webhook_secret="wh-x",  # pragma: allowlist secret
            api_token="",
        )

        with pytest.raises(outbound.MaxAPIError):
            outbound.send_message(chat_id="555", text="привет", bot=tokenless)


class TestNoCrossTalk:
    def test_sequential_sends_do_not_inherit_the_previous_identity(self, httpx_mock):
        # Simulates a worker draining two updates for two different bots.
        httpx_mock.add_response(json={"message": {}})
        httpx_mock.add_response(json={"message": {}})
        httpx_mock.add_response(json={"message": {}})

        with bot_scope(SALON_BOT):
            outbound.send_message(chat_id="1", text="a")
        with bot_scope(CLIENT_BOT):
            outbound.send_message(chat_id="2", text="b")
        outbound.send_message(chat_id="3", text="c")

        sent = [r.headers["Authorization"] for r in httpx_mock.get_requests()]
        assert sent == ["token-salon", "token-client", LEGACY_TOKEN]


def test_network_failure_inside_a_scope_still_raises(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("boom"))

    with pytest.raises(outbound.MaxAPIError):
        with bot_scope(SALON_BOT):
            outbound.send_message(chat_id="555", text="привет")
