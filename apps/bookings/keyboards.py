"""Channel-agnostic inline keyboards for the reminder system.

Sprint 11 / DRF-844 (Phase 1 / R1). Follows the platform UI contract
defined in :mod:`apps.orchestrator.ui.keyboards`: each keyboard is a
``list[dict[str, str]]`` with ``label`` + ``callback`` keys; the
channel adapter renders these per its native widget (MAX
``InlineKeyboard``, future Telegram ``InlineKeyboardMarkup``, web JSON).

### Callback contract

* ``cb:rem:confirm:{pk}``     — client confirms attendance
* ``cb:rem:reschedule:{pk}``  — client wants to reschedule
* ``cb:rem:cancel:{pk}``      — client cancels

``{pk}`` is the :attr:`BookingReminder.id` UUID stringified — the
callback handler decodes it back to look up the row. Embedding the pk
(not the yclients_record_id) keeps the callback compact and lets the
handler skip the kind/yclients_record_id round-trip; the
``BookingReminder`` row carries everything the handler needs.

### T-2h has no buttons

The T-2h reminder is a soft nudge ("see you in 2 hours"); there's no
action the client can sensibly take 2h out that isn't a free-text reply
to the bot. Keeping it text-only also matches the legacy mysite
behaviour. :func:`two_hours_keyboard` exists as an explicit ``None``
return so callers can branch on ``kind`` without ``hasattr`` games.
"""

from __future__ import annotations


# Callback prefixes — single source of truth for the three actions.
CALLBACK_CONFIRM_PREFIX = "cb:rem:confirm:"
CALLBACK_RESCHEDULE_PREFIX = "cb:rem:reschedule:"
CALLBACK_CANCEL_PREFIX = "cb:rem:cancel:"


# Button labels — Russian per the salon brand voice (see
# ``apps.skills.booking.prompts`` for tone alignment).
LABEL_CONFIRM = "✅ Подтверждаю"
LABEL_RESCHEDULE = "🔄 Перенести"
LABEL_CANCEL = "❌ Отменить"


def day_before_keyboard(reminder_pk: str) -> list[dict[str, str]]:
    """T-24h inline keyboard (3 buttons).

    Args:
      reminder_pk: stringified :attr:`BookingReminder.id` (UUID). Will
                   appear verbatim in the ``cb:rem:{action}:{pk}`` payload
                   so the callback handler can look up the row.

    Returns:
      A list of ``{label, callback}`` dicts in display order:
      Confirm → Reschedule → Cancel.

    Order rationale: the affirmative action is leftmost because the
    happy path is the most-common click — minimises mis-click cost on
    cramped mobile keyboards.
    """
    return [
        {"label": LABEL_CONFIRM, "callback": f"{CALLBACK_CONFIRM_PREFIX}{reminder_pk}"},
        {"label": LABEL_RESCHEDULE, "callback": f"{CALLBACK_RESCHEDULE_PREFIX}{reminder_pk}"},
        {"label": LABEL_CANCEL, "callback": f"{CALLBACK_CANCEL_PREFIX}{reminder_pk}"},
    ]


def two_hours_keyboard(reminder_pk: str) -> None:
    """T-2h has no buttons — text-only soft nudge.

    Returns ``None`` (explicit) so callers can pass the result through
    a uniform ``keyboard = build_keyboard(kind, pk)`` switch without
    branching on ``hasattr`` or empty-list comparisons.

    ``reminder_pk`` is accepted but unused — kept in the signature for
    symmetry with :func:`day_before_keyboard` so the caller can swap
    implementations without changing call-site shape.
    """
    _ = reminder_pk  # silence unused-arg warnings; see docstring
    return None
