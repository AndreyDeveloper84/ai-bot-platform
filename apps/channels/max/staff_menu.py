"""Button menu for the salon bot (DRF-1061).

### Why buttons and not conversation

Staff need actions, not interpretation. On 2026-08-14 the owner could not
book in the client bot because he had to type a long service name exactly
right (DRF-1070); the lesson applies doubly to people doing their job under
time pressure. Every entry point here is a tap.

### What is deliberately NOT in the menu

Only capabilities that actually work are shown. A button that opens a
"скоро" message is worse than no button: it costs a tap, teaches the person
the bot is unfinished, and hides the working entries among the dead ones.
So the menu carries the day view, the pending-requests queue, and the door
into the Mini App — and nothing else yet.

Manual booking, completing a visit and marking a no-show are absent for a
harder reason: **Ayla has no endpoint where the actor is a salon employee.**
The public create rejects everyone but the client, complete/no_show check
`is_specialist` and row ownership. A button here would be a promise the
backend cannot keep. That work is the salon-ops window's (DRF-1063/1064).

### Callback convention

``cb:{domain}:{action}[:{ref}]`` with domain ``staff``, decoded only by
``apps.orchestrator.ui.keyboards.parse_callback`` — which needs at least
three colon-separated segments, so ``cb:staff`` alone would be silently
dropped.

### Opening the Mini App

MAX renders a native ``open_app`` button when the bot has a Mini App
registered (``web_app``), and a plain link otherwise. Both come from the
bot's own registry entry rather than a global setting, because with one
Mini App address per bot a global would send staff to the customer app.
When neither is configured the button is omitted entirely — a dead button
is worse than a missing one.

The payload carries no route: the Mini App already routes by resolved role
(admin → /admin/team, master → /master/dashboard), and the frontend's
``_ROUTE_MAP`` knows only customer slugs today. Sending an unknown slug
would land the person on the default anyway.

It is also written in a DIFFERENT grammar from the callback buttons above,
and that is not a slip. MAX validates an ``open_app`` payload against
``^[A-Za-z0-9_-]{0,512}$`` — no colons — while ``cb:{domain}:{action}``
depends on them. See :data:`OPEN_APP_PAYLOAD` for how that was measured
and what it cost to learn.
"""

from __future__ import annotations

from typing import Any

# `cb:staff:*` — three segments minimum, per the shared contract in
# apps/orchestrator/ui/keyboards.py.
CB_DAY = "cb:staff:day"
CB_REQUESTS = "cb:staff:requests"

#: Approving one request. The request id rides in the 4th segment;
#: `parse_callback` splits on the first three colons only, so a UUID
#: survives intact.
CB_APPROVE_PREFIX = "cb:staff:req_ok:"

#: Payload of the Mini App button — and the one identifier in this module
#: that CANNOT use the ``cb:staff:*`` grammar above.
#:
#: MAX accepts ``^[A-Za-z0-9_-]{0,512}$`` in an ``open_app`` payload and
#: nothing else. That is measured, not assumed: ``https://botapi.max.ru``
#: was asked directly on 2026-08-30 — see ``OPEN_APP_PAYLOAD_RE`` in
#: ``apps/channels/max/outbound.py`` for the method and the full character
#: sweep — and the colon came back rejected.
#:
#: The line this replaces said the opposite — «colons are legal and
#: already used elsewhere» — and it was believed. On 2026-08-30
#: ``MAX_BOT_SALON_WEB_APP`` was set on the pilot for the first time, this
#: module started building the button, and MAX answered
#: ``400 proto.payload`` to every staff reply. The keyboard is not sent
#: separately from the text — it rides on the same ``send_message`` — so
#: the 400 took the whole answer down with it, not just the button, and
#: the salon bot went silent for its masters until the setting was pulled.
#:
#: The defect had been dormant since the module was written, invisible to
#: tests and to the pilot alike, because every contour had ``web_app``
#: empty — it woke up at the exact moment someone tried to switch the Mini
#: App entry ON.
OPEN_APP_PAYLOAD = "staff_open_app"


def _miniapp_button(entry, label: str) -> dict[str, str] | None:
    """The door into the Mini App, or None when this bot has no app.

    Prefers the native in-client button; falls back to an external link.
    Returns None rather than a button that cannot work.
    """

    if entry is None:
        return None

    web_app = getattr(entry, "web_app", "")
    if web_app:
        return {"label": label, "callback": OPEN_APP_PAYLOAD, "web_app": web_app}

    miniapp_url = getattr(entry, "miniapp_url", "")
    if miniapp_url:
        return {"label": label, "url": miniapp_url}

    return None


def menu_buttons(role_ctx, entry) -> list[dict[str, str]]:
    """Buttons for this person's roles, most-used first.

    Roles are additive (ADR-0008): an owner who also works as a master gets
    both halves, deduplicated — the day view means "the salon's day" for an
    admin and "my day" for a master, so it appears once, labelled for the
    stronger role.
    """

    buttons: list[dict[str, str]] = []
    is_admin_side = role_ctx.is_owner or role_ctx.is_admin or role_ctx.is_receptionist

    if is_admin_side:
        buttons.append({"label": "📅 Сегодня", "callback": CB_DAY})
        buttons.append({"label": "🗒 Заявки от мастеров", "callback": CB_REQUESTS})
    elif role_ctx.is_master:
        buttons.append({"label": "📅 Мой день", "callback": CB_DAY})

    label = "🏠 Кабинет салона" if is_admin_side else "🏠 Мой кабинет"
    app_button = _miniapp_button(entry, label)
    if app_button is not None:
        buttons.append(app_button)

    return buttons


def menu_attachments(role_ctx, entry) -> list[dict[str, Any]] | None:
    """Inline keyboard for the menu, or None when there is nothing to show."""

    from apps.channels.max.outbound import make_inline_keyboard_attachment

    buttons = menu_buttons(role_ctx, entry)
    if not buttons:
        return None
    return [make_inline_keyboard_attachment(buttons, columns=1)]


def menu_header(role_ctx, tenant) -> str:
    """One line naming the salon and what this person can do here."""

    salon = tenant.name or tenant.slug
    if role_ctx.is_owner or role_ctx.is_admin or role_ctx.is_receptionist:
        return f"Салон «{salon}»."
    if role_ctx.is_master:
        return f"Салон «{salon}». Ваш день и кабинет мастера."
    return f"Салон «{salon}»."
