"""Telegram channel adapter — Phase 1 / CH1 (DRF-848).

Mirrors the shape of :mod:`apps.channels.max`:

- :mod:`apps.channels.telegram.parser` — ``parse_inbound(payload)`` →
  ``CanonicalEvent`` (or ``None`` for ignored update types).
- :mod:`apps.channels.telegram.outbound` — ``send_message`` / ``send_photo``
  / ``answer_callback_query``. All outbound HTTP goes through
  ``api.telegram.org/bot<token>/<method>`` with a proxy (Roskomnadzor
  block) and ``requests.post(..., proxies=...)``.
- :mod:`apps.channels.telegram.handler` — ``handle_inbound(payload, tenant)``,
  the full pipeline: parse → resolve BotUser → skill dispatch → outbound.
- :mod:`apps.channels.telegram.keyboards` — converters from the platform's
  channel-agnostic ``[{label, callback|url}]`` keyboard format
  (:mod:`apps.bookings.keyboards`) to Telegram's
  ``InlineKeyboardMarkup`` JSON.
- :mod:`apps.channels.telegram.webhook` — Django view at
  ``/api/v1/channels/telegram/webhook/<slug:tenant_slug>/``.
- :mod:`apps.channels.telegram.proxy` — single source of truth for the
  ``TELEGRAM_PROXY``/``OPENAI_PROXY`` fallback chain.

**Security**:

- Per-tenant bot token + webhook secret on
  :class:`apps.tenancy.models.Tenant` (NOT a global setting).
- Webhook auth via ``X-Telegram-Bot-Api-Secret-Token`` header, compared
  with ``hmac.compare_digest``.
- Bot token NEVER logged — masked in :meth:`Tenant.__repr__` and in
  admin list_display.
- Mandatory proxy for outbound (Russian network block).

**Not in this PR**:

- food_scanner photo flow — :func:`parser.parse_inbound` preserves
  ``has_attachments=True`` and the raw ``file_id`` list on the
  ``CanonicalEvent.attachments`` field, but actually feeding the photo
  bytes through the nutrition tool is its own ticket (DRF-849+).
"""

from __future__ import annotations

from apps.channels.telegram.handler import handle_inbound
from apps.channels.telegram.outbound import (
    TelegramAPIError,
    answer_callback_query,
    send_message,
    send_photo,
)
from apps.channels.telegram.parser import CanonicalEvent, parse_inbound

__all__ = [
    "CanonicalEvent",
    "TelegramAPIError",
    "answer_callback_query",
    "handle_inbound",
    "parse_inbound",
    "send_message",
    "send_photo",
]
