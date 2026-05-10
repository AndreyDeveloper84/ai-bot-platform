"""Phase 3.2B T09: nudge mute UI keyboards."""
from __future__ import annotations

from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def nudge_mute_keyboard(*, kind: str):
    """[🔕 Не показывай такое] [Показывать реже] на kind-specific нудже."""
    builder = InlineKeyboardBuilder()
    builder.row(
        CallbackButton(
            text="🔕 Не показывай такое",
            payload=f"cb:nudge:mute:off:{kind}",
        ),
    )
    builder.row(
        CallbackButton(
            text="Показывать реже",
            payload=f"cb:nudge:mute:less:{kind}",
        ),
    )
    return builder.as_markup()
