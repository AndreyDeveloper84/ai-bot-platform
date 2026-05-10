"""BotUser → Ayla external_user_id mapping (DRF-246).

Deterministic, stateless: `bot:{max_user_id}`. Ayla side creates a ProxyUser
on first call (`User.is_proxy=True`). Phase C migration replaces this with
real Ayla User UUIDs and links the existing FoodScan history via
`User.linked_proxy_id`.
"""
from __future__ import annotations

from services_app.models import BotUser


def external_user_id_for(bot_user: BotUser) -> str:
    """Return the Ayla `<source>:<id>` external_user_id for a BotUser.

    Pattern: `bot:{max_user_id}`. Stable across calls — no DB write needed.
    `max_user_id` is BigInteger and unique per MAX, fits Ayla regex
    `[A-Za-z0-9_-]{1,64}`.
    """
    return f"bot:{bot_user.max_user_id}"
