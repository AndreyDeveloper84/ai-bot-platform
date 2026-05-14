"""BotUser → Ayla ``external_user_id`` mapping.

Sprint 9 / I2 (DRF-826). Ported from
``mysite/maxbot/services/ayla_user_proxy.py`` with two adjustments for the
multi-channel platform:

1. **Channel-agnostic.** The mysite source built ``bot:{max_user_id}`` —
   safe because MAX was the only channel. The platform serves Telegram /
   WhatsApp / web alongside MAX, and Telegram chat 12345 must not collide
   with MAX user 12345 in Ayla's namespace. The platform form is
   ``bot:{channel}:{channel_user_id}``.

2. **No new model field.** The DRF-826 ticket draft mentioned a
   ``BotUser.ayla_user_id`` cache field + migration. Source review showed
   Ayla creates a ProxyUser on first call and the mapping is deterministic
   on the bot side — no cache needed. Skipping the field keeps the
   migration count low; we can revisit if a future Ayla deploy ever needs
   a stable Ayla UUID returned to us.

Phase 2 / multi-channel migration plan: when Ayla introduces real User
UUIDs and Phase C unifies proxy/real users via ``linked_proxy_id``, this
helper becomes a thin adapter over a lookup table. Until then, pure
function is enough.
"""

from __future__ import annotations

from apps.identity.models import BotUser


def external_user_id_for(bot_user: BotUser) -> str:
    """Return the Ayla ``external_user_id`` for a BotUser.

    Format: ``bot:{channel}:{channel_user_id}``. Stable across calls — no
    DB write. ``channel`` is one of {"max", "telegram", "whatsapp", "web"}
    per platform channel registry. ``channel_user_id`` is the upstream's
    user identifier (string; Telegram chat IDs are negative for groups,
    MAX IDs are bigint, web is UUID).

    Ayla validates the result against ``[A-Za-z0-9_-]{1,64}``. ``channel``
    is a constrained slug; ``channel_user_id`` is upstream-controlled and
    in practice fits — but if a channel ever ships an id with disallowed
    characters, that's a channel-adapter contract bug, not this helper's
    problem. We do not slugify silently — better to fail loudly upstream.
    """
    return f"bot:{bot_user.channel}:{bot_user.channel_user_id}"


def parse_external_user_id(external_id: str) -> tuple[str, str] | None:
    """Inverse of :func:`external_user_id_for`. Returns ``(channel, channel_user_id)``.

    Useful for audit-log post-mortem when only the Ayla ext-user-id is in
    hand and we want to find the originating BotUser. Returns ``None`` on
    malformed input rather than raising — caller decides whether to log
    or fall back.
    """
    if not external_id.startswith("bot:"):
        return None
    parts = external_id.split(":", 2)
    if len(parts) != 3:
        return None
    _, channel, channel_user_id = parts
    if not channel or not channel_user_id:
        return None
    return channel, channel_user_id
