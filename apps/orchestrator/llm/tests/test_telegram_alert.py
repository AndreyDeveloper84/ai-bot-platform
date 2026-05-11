"""Telegram alert on breaker transition tests (DRF-455 / Sprint 2 / E2)."""

from __future__ import annotations

from unittest.mock import patch


from apps.orchestrator.llm import telegram_alert


class TestSendBreakerAlertSkipsWhenUnconfigured:
    def test_empty_chat_id_is_noop(self, settings):
        settings.ADMIN_MAX_CHAT_ID = ""
        with patch("legacy_notifications.max_bot.send_max_message") as send:
            telegram_alert.send_breaker_alert(
                provider="openai",
                transition="closed → open",
                details={"failures": 5},
            )
        send.assert_not_called()

    def test_non_numeric_chat_id_is_noop(self, settings):
        settings.ADMIN_MAX_CHAT_ID = "not-a-number"
        with patch("legacy_notifications.max_bot.send_max_message") as send:
            telegram_alert.send_breaker_alert(
                provider="openai",
                transition="closed → open",
                details={},
            )
        send.assert_not_called()


class TestSendBreakerAlertHappyPath:
    def test_calls_legacy_send_max_message_with_formatted_text(self, settings):
        settings.ADMIN_MAX_CHAT_ID = "12345"
        with patch("legacy_notifications.max_bot.send_max_message") as send:
            telegram_alert.send_breaker_alert(
                provider="openai",
                transition="closed → open",
                details={"failures": 5, "cooldown": 30},
            )
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        assert kwargs["chat_id"] == 12345
        body = kwargs["text"]
        assert "Breaker closed → open for openai" in body
        assert "failures=5" in body
        assert "cooldown=30" in body
        assert "🚨" in body


class TestSendBreakerAlertNeverRaises:
    def test_send_max_message_exception_swallowed(self, settings):
        settings.ADMIN_MAX_CHAT_ID = "12345"
        with patch(
            "legacy_notifications.max_bot.send_max_message",
            side_effect=RuntimeError("network down"),
        ):
            # Must NOT raise.
            telegram_alert.send_breaker_alert(
                provider="openai",
                transition="closed → open",
                details={},
            )

    def test_legacy_import_failure_swallowed(self, settings, monkeypatch):
        """If legacy_notifications module is somehow unavailable
        (drained from the repo, broken dependency), the alert path
        must degrade quietly instead of crashing the breaker.
        """

        settings.ADMIN_MAX_CHAT_ID = "12345"
        import sys

        # Pretend the legacy module is unavailable.
        original = sys.modules.get("legacy_notifications.max_bot")
        sys.modules["legacy_notifications.max_bot"] = None  # type: ignore[assignment]
        try:
            telegram_alert.send_breaker_alert(
                provider="openai",
                transition="closed → open",
                details={},
            )
        finally:
            if original is not None:
                sys.modules["legacy_notifications.max_bot"] = original
            else:
                sys.modules.pop("legacy_notifications.max_bot", None)
