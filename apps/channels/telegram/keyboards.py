"""Channel-agnostic → Telegram InlineKeyboardMarkup converter.

Phase 1 / CH1 (DRF-848). The platform's skill layer emits
channel-agnostic keyboards as ``list[dict[str, str]]`` — one row per
list element — where each dict has either:

- ``{"label": "...", "callback": "cb:..."}`` for a callback-data button,
  OR
- ``{"label": "...", "url": "https://..."}`` for a URL button.

See :mod:`apps.bookings.keyboards` for the canonical producers.

This module exposes a single converter, :func:`to_inline_keyboard`,
that turns that shape into Telegram's
``InlineKeyboardMarkup`` wire JSON — a dict with one key,
``inline_keyboard``, whose value is a ``list[list[InlineKeyboardButton]]``.
Buttons themselves use ``callback_data`` for callbacks and ``url`` for
URL buttons.

### Why this is a thin converter, not a duplicate helper

``apps.bookings.keyboards`` already defines ``day_before_keyboard``,
``confirm_2_button``, ``url_button`` — those return the channel-
agnostic dicts. Duplicating them per-channel would mean two parallel
sources of truth for "what does the booking-confirm keyboard look
like". Instead, the producer modules keep emitting the dicts and this
converter renders them once at the channel boundary.

### Row layout

The producer emits a flat list of buttons. We render each button on its
own row by default — vertical layout is the most readable on mobile
Telegram, and it matches the legacy ``mysite`` bot's UX. Callers that
want multi-button rows can pass a pre-grouped ``list[list[dict]]``;
:func:`to_inline_keyboard` accepts both shapes.
"""

from __future__ import annotations

from typing import Any


def to_inline_keyboard(
    rows: list[dict[str, str]] | list[list[dict[str, str]]] | None,
) -> dict[str, Any] | None:
    """Convert the platform's keyboard shape to Telegram's reply_markup.

    Args:
      rows: One of three shapes:

        * ``None`` — caller has no keyboard. Returns ``None`` so the
          caller can pass the result through to
          ``send_message(..., reply_markup=to_inline_keyboard(rows))``
          unconditionally.
        * Flat ``list[dict]`` — single column of buttons. Each dict is
          a ``{"label", "callback"}`` or ``{"label", "url"}`` entry.
        * Nested ``list[list[dict]]`` — pre-grouped rows. Each inner
          list is one keyboard row; same dict shape per button.

    Returns:
      ``{"inline_keyboard": [[InlineKeyboardButton, ...], ...]}`` ready
      to JSON-serialise as Telegram's ``reply_markup`` field, or
      ``None`` when ``rows`` is empty/None.

    Raises:
      ValueError: a button dict is missing both ``callback`` and
                  ``url``, or is missing ``label``. Loud failure here
                  beats silently dropping a button into ``unknown``.
    """
    if not rows:
        return None

    # Normalise to nested rows: a flat ``list[dict]`` becomes
    # ``[[d1], [d2], ...]`` — one button per row.
    nested: list[list[dict[str, str]]]
    if isinstance(rows[0], dict):
        nested = [[btn] for btn in rows]  # type: ignore[list-item]
    else:
        nested = rows  # type: ignore[assignment]

    out_rows: list[list[dict[str, Any]]] = []
    for row in nested:
        out_row: list[dict[str, Any]] = []
        for btn in row:
            if not isinstance(btn, dict):
                raise ValueError(
                    f"Keyboard button must be a dict, got {type(btn).__name__}: {btn!r}"
                )
            label = btn.get("label")
            if not label:
                raise ValueError(f"Keyboard button missing 'label': {btn!r}")
            out_row.append(_button_to_telegram(btn, label))
        out_rows.append(out_row)

    return {"inline_keyboard": out_rows}


def _button_to_telegram(btn: dict[str, str], label: str) -> dict[str, Any]:
    """Convert a single channel-agnostic button to Telegram's wire shape.

    Telegram's ``InlineKeyboardButton`` requires exactly one of
    ``callback_data`` / ``url`` / ``switch_inline_query`` / etc. We
    only support the first two; anything else raises so we don't
    silently send a malformed keyboard.

    Args:
      btn: the channel-agnostic dict.
      label: pre-validated non-empty label string (caller already
             checked, passed in for clean unpacking).

    Returns:
      ``{"text": label, "callback_data": ...}`` or
      ``{"text": label, "url": ...}``.
    """
    if "callback" in btn and btn["callback"]:
        callback = btn["callback"]
        # Telegram caps callback_data at 64 bytes. The platform's
        # ``cb:rem:confirm:<uuid>`` family is well under that (~40
        # bytes). Defensive guard so a future, longer callback prefix
        # blows up at conversion time, not on the Telegram side where
        # the entire keyboard would silently fail.
        if len(callback.encode("utf-8")) > 64:
            raise ValueError(f"callback_data exceeds Telegram's 64-byte limit: {callback!r}")
        return {"text": label, "callback_data": callback}
    if "url" in btn and btn["url"]:
        return {"text": label, "url": btn["url"]}
    raise ValueError(f"Keyboard button needs 'callback' or 'url', got neither: {btn!r}")
