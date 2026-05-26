"""Pending booking intent cache for /auth/verify post-OAuth restoration.

Per memory `project_booking_flow_implementation_cut` founder cut #6 +
tech-lead handoff 2026-05-26: anonymous-gate context preservation is
P0 PRE_PILOT — losing the draft booking the user was assembling when
they hit the «sign in to continue» wall is a measurable conversion
killer.

# Flow

1. Anonymous user starts assembling a booking in the Mini App
   (master, service, slot, price quote, optional note, loyalty toggle).
2. User hits the OAuth gate — bot prompts «sign in to continue».
3. After OAuth completes, Mini App calls `POST /auth/verify` with the
   draft intent in the request body:
     ```
     {
       "pending_booking_intent": {
         "master_id": "...uuid...",
         "service_id": "...uuid...",
         "slot_iso": "2026-07-15T14:00:00+03:00",
         "price_quoted": 1800,
         "note": "массаж лица",
         "loyalty_apply": true
       }
     }
     ```
4. Backend validates + caches it (Redis, 10min TTL, keyed by bot_user.id).
5. Response includes the cached intent under `pending_booking_intent`.
6. Frontend reads the field and restores the booking screen state.

Re-launch path (Mini App killed + re-opened within 10min) calls
`/auth/verify` with no body — backend still returns the cached intent.

After 10min OR after the user successfully books / cancels the flow,
the intent expires / is cleared.

# Why cache (not a Conversation field)

Tech-lead Q-3 verdict 2026-05-26: short-lived cache. Reasons:
  - No new DB migration (1-2h scope target)
  - TTL behaviour is native to cache (no purge job needed)
  - Conversation isn't always present at the auth/verify moment;
    cache keyed on BotUser is robust to that
  - Privacy-friendly — drafts containing partial booking data don't
    persist past the user's session
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from django.core.cache import cache

# Cache TTL — short enough to keep drafts out of long-term storage,
# long enough to cover the OAuth round-trip + post-redirect Mini App
# re-mount. Per memory `project_booking_flow_implementation_cut` cut #6.
PENDING_INTENT_TTL_SECONDS = 600  # 10 minutes


# Whitelist of fields the cache will accept. Anything else is dropped
# silently — defensive vs frontend schema drift (extra fields don't
# break the contract; missing fields are tolerated).
_ALLOWED_FIELDS: dict[str, type] = {
    "master_id": str,
    "service_id": str,
    "slot_iso": str,
    "price_quoted": (int, float),  # type: ignore[dict-item]
    "note": str,
    "loyalty_apply": bool,
}

# Bounds (defensive — keep the cache value small + sane).
_MAX_NOTE_LEN = 500
_MAX_PRICE = 10_000_000  # 10M roubles ceiling


class PendingIntentInvalid(ValueError):
    """Raised when the caller-supplied intent fails validation.

    Caller (view) should translate to HTTP 400 with an error code.
    """


def _cache_key(bot_user_id: UUID | str) -> str:
    """Per-user cache key. UUID stringification is stable across calls."""
    return f"pending_booking_intent:{bot_user_id}"


def validate_intent(payload: Any) -> dict[str, Any]:
    """Coerce + validate a caller-supplied intent dict.

    Drops unknown fields, validates types, bounds-checks `price_quoted`
    and truncates `note`. Returns a sanitised dict ready to cache.

    Raises:
        PendingIntentInvalid: payload is not a dict OR a known field has
            the wrong type / out-of-range value.
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise PendingIntentInvalid(
            f"pending_booking_intent must be an object, got {type(payload).__name__}"
        )

    sanitised: dict[str, Any] = {}
    for field, expected_type in _ALLOWED_FIELDS.items():
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            # Caller explicitly cleared a field — don't store it.
            continue
        if not isinstance(value, expected_type):
            raise PendingIntentInvalid(
                f"pending_booking_intent.{field} must be {expected_type!r}, "
                f"got {type(value).__name__}"
            )
        if field == "note" and len(value) > _MAX_NOTE_LEN:
            value = value[:_MAX_NOTE_LEN]
        if field == "price_quoted" and not (0 <= value <= _MAX_PRICE):
            raise PendingIntentInvalid(
                f"pending_booking_intent.price_quoted out of range [0, {_MAX_PRICE}]: {value}"
            )
        sanitised[field] = value
    return sanitised


def store_intent(bot_user_id: UUID | str, payload: dict[str, Any]) -> None:
    """Persist the intent to cache. Empty payload → clears any prior cache."""
    key = _cache_key(bot_user_id)
    if not payload:
        cache.delete(key)
        return
    cache.set(key, payload, timeout=PENDING_INTENT_TTL_SECONDS)


def get_intent(bot_user_id: UUID | str) -> Optional[dict[str, Any]]:
    """Fetch the cached intent. Returns None when nothing cached / expired."""
    return cache.get(_cache_key(bot_user_id))


def clear_intent(bot_user_id: UUID | str) -> None:
    """Explicit cache eviction (e.g. after the booking flow completes)."""
    cache.delete(_cache_key(bot_user_id))
