"""Keyboard conversion tests (Phase 1 / CH1, DRF-848).

Behaviours 10, 11:
  10. MAX text-button dict → Telegram inline-keyboard, callback_data preserved
  11. MAX url-button → Telegram inline-keyboard, url preserved
"""

from __future__ import annotations

import pytest

from apps.channels.telegram.keyboards import to_inline_keyboard


class TestCallbackButtons:
    def test_single_callback_button(self):
        rows = [{"label": "✅ Подтверждаю", "callback": "cb:rem:confirm:abc"}]
        result = to_inline_keyboard(rows)
        assert result == {
            "inline_keyboard": [[{"text": "✅ Подтверждаю", "callback_data": "cb:rem:confirm:abc"}]]
        }

    def test_multiple_callback_buttons_each_own_row(self):
        rows = [
            {"label": "✅ Подтверждаю", "callback": "cb:rem:confirm:abc"},
            {"label": "🔄 Перенести", "callback": "cb:rem:reschedule:abc"},
            {"label": "❌ Отменить", "callback": "cb:rem:cancel:abc"},
        ]
        result = to_inline_keyboard(rows)
        assert result is not None
        # Three rows, each with one button.
        assert len(result["inline_keyboard"]) == 3
        assert result["inline_keyboard"][0][0]["callback_data"] == "cb:rem:confirm:abc"
        assert result["inline_keyboard"][1][0]["callback_data"] == "cb:rem:reschedule:abc"
        assert result["inline_keyboard"][2][0]["callback_data"] == "cb:rem:cancel:abc"

    def test_callback_data_preserved_verbatim(self):
        """Callback strings must round-trip with no mangling."""
        rows = [{"label": "x", "callback": "cb:book:confirm:550e8400-e29b-41d4-a716-446655440000"}]
        result = to_inline_keyboard(rows)
        assert (
            result["inline_keyboard"][0][0]["callback_data"]
            == "cb:book:confirm:550e8400-e29b-41d4-a716-446655440000"
        )


class TestUrlButtons:
    def test_url_button_preserves_url(self):
        """Behaviour 11: URL-button gets url field, not callback_data."""
        rows = [{"label": "💳 Оплатить", "url": "https://yoomoney.ru/checkout/abc"}]
        result = to_inline_keyboard(rows)
        assert result == {
            "inline_keyboard": [
                [{"text": "💳 Оплатить", "url": "https://yoomoney.ru/checkout/abc"}]
            ]
        }

    def test_url_button_no_callback_data_key(self):
        """URL buttons must NOT carry a callback_data field — Telegram rejects mixed buttons."""
        rows = [{"label": "Open", "url": "https://example.com"}]
        result = to_inline_keyboard(rows)
        btn = result["inline_keyboard"][0][0]
        assert "callback_data" not in btn
        assert btn["url"] == "https://example.com"


class TestNoneAndEmpty:
    def test_none_returns_none(self):
        assert to_inline_keyboard(None) is None

    def test_empty_list_returns_none(self):
        assert to_inline_keyboard([]) is None


class TestNestedRows:
    def test_pre_grouped_rows(self):
        """Caller can pre-group two buttons on one row by nesting."""
        rows = [
            [
                {"label": "✅", "callback": "cb:1"},
                {"label": "❌", "callback": "cb:2"},
            ]
        ]
        result = to_inline_keyboard(rows)
        assert len(result["inline_keyboard"]) == 1
        assert len(result["inline_keyboard"][0]) == 2
        assert result["inline_keyboard"][0][0]["callback_data"] == "cb:1"
        assert result["inline_keyboard"][0][1]["callback_data"] == "cb:2"


class TestErrorPaths:
    def test_missing_label_raises(self):
        with pytest.raises(ValueError, match="label"):
            to_inline_keyboard([{"callback": "cb:x"}])

    def test_button_without_callback_or_url_raises(self):
        with pytest.raises(ValueError, match="callback.*url|url.*callback"):
            to_inline_keyboard([{"label": "x"}])

    def test_callback_over_64_bytes_raises(self):
        """Telegram rejects callback_data > 64 bytes — fail fast at convert time."""
        long_cb = "cb:" + ("x" * 100)
        with pytest.raises(ValueError, match="64-byte"):
            to_inline_keyboard([{"label": "x", "callback": long_cb}])

    def test_non_dict_button_raises(self):
        with pytest.raises(ValueError, match="dict"):
            to_inline_keyboard(["not a dict"])  # type: ignore[list-item]
