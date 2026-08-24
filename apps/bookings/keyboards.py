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

# B5 / DRF-841 — customer-initiated destructive-action prefixes. Distinct
# namespace from the ``cb:rem:*`` reminder-button family so a misfired
# parser (or a copy-pasted developer payload) can't cross paths. The
# ``{pk}`` segment is a UUID pointing at a
# :class:`apps.booking.models.PendingBookingAction` row.
CALLBACK_BOOK_CONFIRM_PREFIX = "cb:book:confirm:"
CALLBACK_BOOK_CANCEL_PREFIX = "cb:book:cancel:"

# Booking master-pick callback (2026-05-21). When the user taps a master
# card after a show_masters result, the button payload carries the YClients
# staff id verbatim. Booking skill matches this prefix and dispatches
# show_slots(master_id=<id>) directly — no LLM round-trip needed for the
# selection itself. Suffix is the raw integer YClients staff id (NOT a
# UUID like the confirm/cancel preview tokens — those are platform-side
# pending-action rows, this is a stateless deterministic id).
CALLBACK_BOOK_PICK_MASTER_PREFIX = "cb:book:pick_master:"

# Booking slot-pick callback (2026-05-21). When the user taps a slot
# card after a show_slots result, the button payload carries the slot's
# ISO datetime verbatim. Booking skill matches this prefix and
# synthesises a user-text query ("запиши меня на <datetime>") so the
# normal Phase-1 LLM call emits confirm_booking with master_id +
# service_id pulled from short-term conversation memory. Suffix is
# the raw ISO string YClients returned in show_slots — kept verbatim
# so the LLM's confirm_booking arguments match the prior tool output.
CALLBACK_BOOK_PICK_SLOT_PREFIX = "cb:book:pick_slot:"

# Booking date-pick callback (2026-05-21). Date-picker sits between
# master pick and slot pick — without it show_slots auto-selects the
# nearest available date and the user never gets to pick when. Payload
# carries the master_id + date so the slot-listing call can pin the
# date without re-fetching the master's date list. Format:
# ``cb:book:pick_date:<master_id>:<YYYY-MM-DD>``.
CALLBACK_BOOK_PICK_DATE_PREFIX = "cb:book:pick_date:"

# DRF-1325 — part-of-day pick. Sits between date pick and slot pick for the
# same reason the date picker sits between master pick and slot pick: a bare
# list of every free time of a day IS a calendar, and people ask for «вечер»,
# not for 19:00. Payload carries master + date + part + service so the tap is
# self-contained like every other booking callback. The ``part`` segment is
# one of ``apps.orchestrator.time_preference.PART_ORDER`` — or ``any``, the
# «Точное время» escape hatch back to the full list.
# Format: ``cb:book:pick_part:<master_id>:<YYYY-MM-DD>:<part>:<service_id>``.
CALLBACK_BOOK_PICK_PART_PREFIX = "cb:book:pick_part:"

# DRF-1325 — «Выбрать дату»: expand the three day chips into the full list of
# the master's free days. Format:
# ``cb:book:more_dates:<master_id>:<service_id>``.
CALLBACK_BOOK_MORE_DATES_PREFIX = "cb:book:more_dates:"


# Button labels — Russian per the salon brand voice (see
# ``apps.skills.booking.prompts`` for tone alignment).
LABEL_CONFIRM = "✅ Подтверждаю"
LABEL_RESCHEDULE = "🔄 Перенести"
LABEL_CANCEL = "❌ Отменить"

# B5 — the 2-button preview gate. Slightly different wording from
# the reminder buttons (``Отмена`` not ``Отменить``) because the
# preview-time semantic is "abort this booking flow", not "cancel
# the underlying booking". Avoids the user thinking ❌ ANY time means
# "cancel my upcoming appointment".
LABEL_BOOK_CONFIRM = "✅ Подтверждаю"
LABEL_BOOK_CANCEL = "❌ Отмена"


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


def confirm_2_button(
    token: str,
    *,
    label_yes: str = LABEL_BOOK_CONFIRM,
    label_no: str = LABEL_BOOK_CANCEL,
) -> list[dict[str, str]]:
    """B5 / DRF-841 — 2-button preview keyboard for destructive actions.

    Args:
      token: stringified
        :attr:`apps.booking.models.PendingBookingAction.id` UUID. Threads
        through both callbacks so the handler can look up the pending
        row.
      label_yes: override label for the affirmative button. Defaults to
        ``"✅ Подтверждаю"`` — the salon brand voice's standard
        affirmative.
      label_no: override label for the cancellation button. Defaults to
        ``"❌ Отмена"`` — note "Отмена" (abort this flow), NOT
        "Отменить" (cancel my booking), to keep the semantic distinct
        from the day-before reminder ❌ button.

    Returns:
      A list of two ``{label, callback}`` dicts: affirmative first
      (happy-path minimises mis-click cost on mobile), cancellation
      second.

    Used by:

    * ``confirm_booking`` tool — preview "create this YClients record?"
    * ``cancel_booking`` tool — preview "cancel record #X?"
    * ``reschedule_booking`` tool — preview "move record #X to time Y?"

    The three tools share the same keyboard shape because the underlying
    UX question is the same: "I'm about to do something destructive,
    please confirm". Centralising the helper means the buttons can't
    drift across the three flows.
    """
    return [
        {"label": label_yes, "callback": f"{CALLBACK_BOOK_CONFIRM_PREFIX}{token}"},
        {"label": label_no, "callback": f"{CALLBACK_BOOK_CANCEL_PREFIX}{token}"},
    ]


def url_button(label: str, url: str) -> list[dict[str, str]]:
    """B7 / DRF-843 — single-button URL keyboard for payment checkout.

    Args:
      label: button label rendered in the inline keyboard. e.g.
             ``"💳 Оплатить"`` for the buy_certificate flow.
      url: external URL the button opens. For payment checkout this
             is the ``checkout_url`` returned by Ayla
             ``POST /api/v1/payments/create`` (see
             :class:`apps.integrations.ayla_payments.client.PaymentCreateResponse`).
             Pre-#427: was ``apps.orders.models.Order.checkout_url`` —
             that table was retired when YooKassa lifecycle moved to
             Ayla per ADR-0009 §Domain ownership.

    Returns:
      A one-element list with a ``{label, url}`` dict. The channel
      adapter is responsible for translating this into MAX's URL-
      button widget (or Telegram's ``InlineKeyboardButton(url=...)``,
      or web JSON).

    Distinct shape from :func:`confirm_2_button` — that helper emits
    ``{label, callback}`` dicts (callback round-trips back to us);
    this one emits ``{label, url}`` (browser opens the URL directly).
    The channel adapter switches on the presence of ``url`` vs
    ``callback`` to pick the widget kind.
    """
    return [{"label": label, "url": url}]


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
