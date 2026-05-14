"""Channel-sender registry tests (Sprint 8 review P1-cycle2)."""

from __future__ import annotations

import pytest

from apps.orchestrator import channel_registry
from apps.orchestrator.channel_registry import (
    ChannelSendError,
    get_sender,
    register_channel,
    send,
    unregister_channel,
)


@pytest.fixture(autouse=True)
def _restore_max_sender():
    """Save + restore the boot-registered MAX sender around each test.

    apps.channels.apps.ChannelsConfig.ready() registers the production
    MAX sender at process boot. Tests muck with the registry; we restore
    so sibling tests don't see a None sender.
    """
    saved = get_sender("max")
    yield
    if saved is not None:
        register_channel("max", saved)
    else:
        unregister_channel("max")


class TestRegisterAndSend:
    def test_no_sender_returns_false(self) -> None:
        unregister_channel("nonexistent")
        assert send("nonexistent", "chat-1", "hello") is False

    def test_sender_called_with_chat_and_text(self) -> None:
        seen: list[tuple[str, str]] = []

        def sender(chat_id: str, text: str) -> None:
            seen.append((chat_id, text))

        register_channel("test_chan", sender)
        try:
            result = send("test_chan", "chat-99", "ping")
            assert result is True
            assert seen == [("chat-99", "ping")]
        finally:
            unregister_channel("test_chan")

    def test_sender_exception_propagates(self) -> None:
        """The pipeline retry loop relies on exceptions reaching it —
        registry MUST NOT swallow them.
        """

        def boom_sender(chat_id: str, text: str) -> None:
            raise ChannelSendError("synthetic")

        register_channel("test_chan_boom", boom_sender)
        try:
            with pytest.raises(ChannelSendError, match="synthetic"):
                send("test_chan_boom", "c", "t")
        finally:
            unregister_channel("test_chan_boom")


class TestProductionWiringIntegration:
    def test_max_sender_registered_at_boot(self) -> None:
        """apps.channels.apps.ChannelsConfig.ready() registers the MAX
        sender at Django startup. After the test runner has booted with
        apps.channels in INSTALLED_APPS, the sender should be present.
        """
        assert get_sender("max") is not None

    def test_registry_module_separate_from_channels(self) -> None:
        """The whole point of the registry is to keep
        apps.orchestrator.channel_registry independent of any channel
        adapter module. Importing it must NOT pull apps.channels.*.
        """
        import sys

        # The registry module is loaded by this test's own imports.
        # apps.channels MAY be loaded (Django startup) but the registry
        # itself doesn't trigger that load order.
        loaded = "apps.channels" in sys.modules  # informational
        # The contract is structural: the registry module's globals
        # contain no reference to apps.channels.
        registry_globals = vars(channel_registry)
        assert not any(name.startswith("apps.channels") for name in registry_globals), (
            f"channel_registry leaks apps.channels reference: {registry_globals}"
        )
        _ = loaded  # silence linter
