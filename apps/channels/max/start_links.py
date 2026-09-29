"""Start links into the salon bot — the one handover form that works.

A start link is ``https://max.ru/<bot>?start=<payload>``. Opening it
creates the chat with the bot and delivers ``<payload>`` as the
``bot_started`` event's ``payload`` field, which the parser folds into
the synthetic text «/start <payload>»
(:func:`apps.channels.max.parser._parse_bot_started`).

### Why the salon bot, always

Only ``ingress:max_salon`` reaches
:mod:`apps.channels.max.salon_handler`, the single place that reads
either payload this module builds:

* ``master_invite_<uuid>`` → the invitation button (DRF-1424);
* ``inv_<code>``           → a staff access code (DRF-1061).

A link naming the customer-facing bot would deliver the same payload to
the conversational pipeline, which has no opinion about either — the
token would arrive and be dropped, silently.

### Why this module exists rather than a second copy

``views_invite`` grew this builder for the master invitation; the staff
access code needs the identical resolution — same registry lookup, same
«no ``web_app`` means no link» rule, same URL shape — for a different
payload. Two copies of a rule that decides which bot a credential opens
is precisely the shape that drifts: one of them gets the fix, the other
keeps sending people to the wrong bot and says nothing.

### The empty string is a value, not a failure

When the deployment has no salon bot for this tenant, or has one with no
Mini App name, there is no handle to build a URL from **and** the bot
could not build its reply button on arrival either. So the link would
open a conversation whose only possible answer is an apology. A missing
link is a visible gap the caller can report to the person holding the
phone; a link that leads to an apology is the working-looking dead end
#1332 spent a whole PR removing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MAX_START_LINK_TEMPLATE = "https://max.ru/{bot}?start={payload}"
"""The URL shape, verified live on the pilot 30.08.

The owner opened ``https://max.ru/id583403546770_3_bot?start=master_invite_test``
and the consumer received, on ``ingress:max_salon``::

    {"update_type": "bot_started", "chat_id": 315714313,
     "user": {"user_id": 83146139, ...},
     "payload": "master_invite_test", "user_locale": "ru"}

``<bot>`` is the registry entry's ``web_app``. Not an inference from the
URL's shape: the pilot names the salon bot
``MAX_BOT_SALON_WEB_APP=id583403546770_3_bot``, character-for-character
the handle in the link above. The value MAX wants in an ``open_app``
button's ``web_app`` and the value that addresses the bot in a
``max.ru`` URL are the same string.

**Not** ``max://bot/<slug>?start=…``. That scheme is unimplemented; the
phone answers «Не удалось открыть ссылку» (#1332 removed it for exactly
that reason, and it is not coming back through this door).
"""


def salon_bot_handle(tenant) -> str:
    """This salon's staff-bot handle, or ``""`` when there is none.

    Looks the entry up by ``(tenant.slug, max_salon)`` rather than by
    tenant alone: a tenant may legitimately have more than one bot — a
    per-tenant client bot and this one — and matching on tenant would
    return whichever was declared first in ``MAX_BOTS``, quite possibly
    the client bot.

    ``tenant`` is anything carrying ``.slug`` — the model in production,
    a ``SimpleNamespace`` in
    ``apps/channels/tests/test_salon_web_app_enablement.py``. A caller
    without one gets ``AttributeError`` on the line below rather than a
    ``getattr`` default: an unknown tenant must not quietly resolve to
    «no salon bot», because that answer is indistinguishable from a
    correctly-configured contour that simply has none.
    """

    from apps.channels.bot_registry import effective_registry, resolve_by_stream
    from apps.channels.max.salon_handler import SALON_STREAM

    slug = tenant.slug
    # По потоку, не по тенанту записи (DRF-1705): ссылка для мастера
    # соло-тенанта раньше не собиралась вовсе — записи с его тенантом нет.
    entry = resolve_by_stream(SALON_STREAM, effective_registry())
    if entry is None or not entry.web_app:
        logger.warning(
            "channels.max.start_links.no_salon_bot tenant=%s — no salon bot with a "
            "Mini App name (MAX_BOT_<SLUG>_WEB_APP on the entry whose stream is %s), "
            "so nothing shareable can be built and delivery has no link to offer.",
            slug,
            SALON_STREAM,
        )
        return ""
    return entry.web_app


def salon_start_link(tenant, payload: str) -> str:
    """``https://max.ru/<salon bot>?start=<payload>``, or ``""``.

    ``payload`` must be a flat slug. MAX rejects an ``open_app`` payload
    containing ``=``, ``&`` or ``?`` with HTTP 400 ``proto.payload``
    (Guard 3 in :mod:`apps.channels.max.outbound`), and the salon bot
    echoes the master-invite payload straight back into such a button —
    so a querystring appended here would make every opened invitation
    poison the consumer. Callers build the payload from a UUID or from
    the four-character code alphabet, neither of which can trip it.
    """

    handle = salon_bot_handle(tenant)
    if not handle:
        return ""
    return MAX_START_LINK_TEMPLATE.format(bot=handle, payload=payload)


__all__ = ["MAX_START_LINK_TEMPLATE", "salon_bot_handle", "salon_start_link"]
