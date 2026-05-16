"""Keyboard builder tests (DRF-844 / Phase 1 / R1)."""

from __future__ import annotations

from apps.bookings.keyboards import (
    CALLBACK_CANCEL_PREFIX,
    CALLBACK_CONFIRM_PREFIX,
    CALLBACK_RESCHEDULE_PREFIX,
    day_before_keyboard,
    two_hours_keyboard,
)


class TestDayBeforeKeyboard:
    def test_returns_three_buttons(self) -> None:
        rows = day_before_keyboard("rem-pk-1")
        assert len(rows) == 3

    def test_button_order_is_confirm_reschedule_cancel(self) -> None:
        # Happy-path leftmost (minimise mis-click for the common
        # action — see keyboards.py docstring).
        rows = day_before_keyboard("rem-pk-1")
        callbacks = [r["callback"] for r in rows]
        assert callbacks[0].startswith(CALLBACK_CONFIRM_PREFIX)
        assert callbacks[1].startswith(CALLBACK_RESCHEDULE_PREFIX)
        assert callbacks[2].startswith(CALLBACK_CANCEL_PREFIX)

    def test_callbacks_embed_reminder_pk(self) -> None:
        rows = day_before_keyboard("abc-123")
        for r in rows:
            assert r["callback"].endswith(":abc-123")

    def test_each_button_has_label_and_callback(self) -> None:
        rows = day_before_keyboard("x")
        for r in rows:
            assert "label" in r and r["label"]
            assert "callback" in r and r["callback"]


class TestTwoHoursKeyboard:
    def test_returns_none(self) -> None:
        # T-2h has no buttons — explicit None so callers can branch
        # on kind without hasattr/empty-list games. The signature is
        # ``-> None`` (sentinel function), so call-as-statement is the
        # only correct invocation; we assert the side-effect-free
        # behaviour by capturing the bare call.
        result = two_hours_keyboard("rem-pk-1")  # type: ignore[func-returns-value]
        assert result is None
