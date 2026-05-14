"""Channel-agnostic UI button builders.

Sprint 9 / D2 (DRF-832). The orchestrator composer (Sprint 6 / O5)
defined the platform UI contract: each turn's ``action_data["buttons"]``
is a list of ``{label, callback}`` dicts; channel adapters render those
per their native widget.

The mysite source (``legacy_maxbot/keyboards.py``, 735 LOC) was a thick
wrapper over the MAX SDK's ``InlineKeyboardBuilder`` — tightly coupled
to one channel. This port keeps the **payload contracts** (so callback
parsing stays consistent with mysite-era PR #145 / DRF-358) but emits
plain dicts the composer hands to whichever channel adapter is wired.

## Callback payload format

``cb:{domain}:{action}[:{ref}]``. Examples:

* ``cb:food:to_diary:{scan_id}``       (P1 food_scanner ✅)
* ``cb:food:clarify:{scan_id}``        (P1 food_scanner ✏️)
* ``cb:food:reject:{scan_id}``         (P1 food_scanner ❌)
* ``cb:food:diary``                    (P4 food_clarify "В дневник")
* ``cb:food:typo``                     (P4 food_clarify "Опечатка")
* ``cb:food:correct:{field}:{scan_id}`` (P5 food_correction)
* ``cb:anketa:choice:{step}:{value}``  (P3 nutrition_anketa step pick)

Use :func:`parse_callback` to decode; never split manually at call
sites — keep the contract here.

## Why dicts instead of dataclasses

Channel adapter outbound paths already accept dicts (see
``apps/channels/max/outbound.py``). Wrapping each call in a Button
dataclass that then gets ``asdict()``ed adds a layer for nothing.
Dataclasses live here for type-clarity at the producer side;
``Button.as_dict()`` exists for explicitness when the producer wants
a typed handle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ─── shared constants ──────────────────────────────────────────────────────

BUTTON_CONFIRM = "✅ Да"
BUTTON_CANCEL = "❌ Нет"
BUTTON_BACK = "← Назад"

# Special callback for the channel's RequestContactButton equivalent —
# the channel adapter recognises this sentinel and emits the platform's
# phone-collect widget (MAX/Telegram both support it natively).
CALLBACK_REQUEST_CONTACT = "cb:request_contact"


@dataclass(frozen=True)
class Button:
    """Channel-agnostic button. Composer passes ``as_dict()`` to channels."""

    label: str
    callback: str

    def as_dict(self) -> dict[str, str]:
        return {"label": self.label, "callback": self.callback}


def _to_keyboard(*buttons: Button) -> list[dict[str, str]]:
    """Convenience: flatten Button instances into the contract list."""
    return [b.as_dict() for b in buttons]


# ─── builders ──────────────────────────────────────────────────────────────


def food_drink_clarify_keyboard() -> list[dict[str, str]]:
    """P4 food_clarify (DRF-358 fallback card).

    Two buttons: «В дневник» / «Опечатка». No scan_id — entered when the
    parser missed; the next step (food_scanner) generates a scan_id.
    """
    return _to_keyboard(
        Button(label="📔 В дневник", callback="cb:food:diary"),
        Button(label="❌ Опечатка", callback="cb:food:typo"),
    )


def food_recognition_keyboard(scan_id: str) -> list[dict[str, str]]:
    """P1 food_scanner confirmation card.

    Three buttons: ✅ В дневник / ✏️ Уточнить / ❌ Не то. ``scan_id`` is
    the Ayla recognition id — embedded in the callback so the handler
    knows which scan to log/correct/reject.
    """
    return _to_keyboard(
        Button(label="✅ В дневник", callback=f"cb:food:to_diary:{scan_id}"),
        Button(label="✏️ Уточнить", callback=f"cb:food:clarify:{scan_id}"),
        Button(label="❌ Не то", callback=f"cb:food:reject:{scan_id}"),
    )


def correction_choice_keyboard(scan_id: str) -> list[dict[str, str]]:
    """P5 food_correction — what field to correct.

    Three buttons matching the source flow: грамм / название / макросы.
    Each callback carries ``scan_id`` for the follow-up text input step.
    """
    return _to_keyboard(
        Button(label="⚖️ Грамм", callback=f"cb:food:correct:grams:{scan_id}"),
        Button(label="🏷 Название", callback=f"cb:food:correct:name:{scan_id}"),
        Button(label="🥩 Макросы", callback=f"cb:food:correct:macros:{scan_id}"),
    )


def anketa_choice_keyboard(step: str, options: list[tuple[str, str]]) -> list[dict[str, str]]:
    """P3 nutrition_anketa per-step choice keyboard.

    ``step`` is the anketa state name (``gender``, ``activity_level``,
    ``goal``, etc.). ``options`` is a list of ``(label, value)`` pairs;
    the value is what gets logged in skill state.

    Raises:
        ValueError: empty options. A choice keyboard with no choices is
        a programming error — fail loudly rather than render an empty
        widget the user would see as a soft-lock.
    """
    if not options:
        raise ValueError(f"anketa_choice_keyboard: step={step!r} has no options")
    return [
        {"label": label, "callback": f"cb:anketa:choice:{step}:{value}"} for label, value in options
    ]


def request_contact_keyboard() -> list[dict[str, str]]:
    """P3 anketa phone collect — channel-native phone widget.

    Channel adapters recognise the ``cb:request_contact`` sentinel and
    swap it for their native widget (MAX ``RequestContactButton``,
    Telegram ``KeyboardButton(request_contact=True)``).
    """
    return _to_keyboard(
        Button(label="📱 Поделиться номером", callback=CALLBACK_REQUEST_CONTACT),
    )


# ─── parser ────────────────────────────────────────────────────────────────


def parse_callback(callback: str) -> dict[str, Any] | None:
    """Decode a callback string into its components.

    Returns ``None`` for malformed or non-``cb:`` callbacks rather than
    raising — callers route unknown callbacks to the fallback handler.

    Examples:
        >>> parse_callback("cb:food:to_diary:abc123")
        {'domain': 'food', 'action': 'to_diary', 'ref': 'abc123'}
        >>> parse_callback("cb:food:diary")
        {'domain': 'food', 'action': 'diary', 'ref': None}
        >>> parse_callback("cb:food:correct:grams:abc123")
        {'domain': 'food', 'action': 'correct', 'ref': 'grams:abc123'}
        >>> parse_callback("not-a-callback")  # → None
    """
    if not callback.startswith("cb:"):
        return None
    parts = callback.split(":", 3)
    if len(parts) < 3:
        # Just "cb:" or "cb:x" — malformed.
        return None
    _, domain, action = parts[0], parts[1], parts[2]
    if not domain or not action:
        return None
    ref = parts[3] if len(parts) == 4 else None
    return {"domain": domain, "action": action, "ref": ref}
