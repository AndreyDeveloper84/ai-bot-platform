"""Channel-agnostic UI builders.

Skills produce ``action_data["buttons"]`` as a list of ``{label, callback}``
dicts. The channel adapter renders them per its native widget (MAX
InlineKeyboard, Telegram InlineKeyboardMarkup, web JSON). This subpackage
holds the builders + callback payload constants shared across skills.
"""

from apps.orchestrator.ui.keyboards import (
    BUTTON_CANCEL,
    BUTTON_CONFIRM,
    Button,
    anketa_choice_keyboard,
    correction_choice_keyboard,
    food_drink_clarify_keyboard,
    food_recognition_keyboard,
    parse_callback,
    request_contact_keyboard,
)

__all__ = [
    "BUTTON_CANCEL",
    "BUTTON_CONFIRM",
    "Button",
    "anketa_choice_keyboard",
    "correction_choice_keyboard",
    "food_drink_clarify_keyboard",
    "food_recognition_keyboard",
    "parse_callback",
    "request_contact_keyboard",
]
