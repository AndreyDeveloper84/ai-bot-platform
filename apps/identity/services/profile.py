"""Customer profile read / update / soft-delete (Phase 3 / F4).

The Mini App profile screen calls these from `apps.miniapp_api.views`.
Each function operates on a resolved :class:`BotUser` (the auth decorator
already pulled it under `tenant_scope`).

Soft-delete semantics
---------------------

Per Phase 3 design Q-C3 + user decision 2026-05-19:

* The "Delete my data" button writes ``BotUser.deleted_at`` AND scrubs
  PII (``display_name``, ``avatar_url``, ``client_name``, ``phone``,
  ``context``).
* The :class:`UserPreferences` row is deleted entirely (CASCADE'd by
  the FK is fine too; we delete explicitly so favorites are recomputed
  as ``None`` and the row is reclaimed before the deferred CASCADE fires).
* The :class:`BotUser` row itself stays — preserves FK targets from
  :class:`apps.booking.models.BookingRequest`, :class:`apps.conversations.models.Conversation`
  etc., so historical reporting / audit doesn't get gaps.
* Re-engagement after deletion: a subsequent Mini App tap won't pass
  auth (require_init_data filters ``deleted_at__isnull=True``); the
  user has to DM the bot fresh, which creates a new BotUser row with
  the same ``channel_user_id``... blocked by ``unique_together``.
  Acceptable trade-off — re-engagement requires manual admin action
  per the handoff §12 OP6 (CSM workflow).

Deviation from handoff §12 OP6
------------------------------

The handoff specifies «email to support@ then CSM-verified manual
delete». The Mini App implementation here is immediate (with 2-step
confirm in the UI) so the customer doesn't wait days for a CSM ticket.
Tracked in [[project-policy-deviation-pattern]] §profile-delete-r1.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.identity.models import BotUser, UserPreferences


@dataclass(frozen=True)
class ProfileSnapshot:
    """Read-only DTO the API view serialises to JSON."""

    bot_user_id: str
    display_name: str
    client_name: str
    phone_masked: str
    timezone: str
    joined_at: str  # ISO 8601
    # Preferences (always present — get_profile auto-creates on first read)
    preferences: dict[str, Any]
    # Favourites — top-N derived; computed elsewhere, populated here for F4
    favorite_master_name: str | None
    favorite_service_name: str | None


def _mask_phone(phone: str) -> str:
    """Mask middle digits for F4 display per handoff §12 mockup.

    `+79161234567` → `+7 ••• ••• 45 67`. Empty / non-conforming
    phones return blank — the UI shows a "Add phone" CTA instead.
    """
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 10:
        return ""
    last4 = digits[-4:]
    return f"+{digits[0]} ••• ••• {last4[:2]} {last4[2:]}"


def get_profile(bot_user: BotUser) -> ProfileSnapshot:
    """Return the F4 profile snapshot for ``bot_user``.

    Creates the :class:`UserPreferences` row on first read with the
    handoff defaults — simpler than dual code paths in every consumer.
    Favourites are stubbed to ``None`` for now; v1.1 will compute from
    booking history.
    """
    prefs, _ = UserPreferences.all_tenants.get_or_create(
        bot_user=bot_user,
        defaults={"tenant": bot_user.tenant},
    )
    return ProfileSnapshot(
        bot_user_id=str(bot_user.id),
        display_name=bot_user.display_name,
        client_name=bot_user.client_name,
        phone_masked=_mask_phone(bot_user.phone),
        timezone=bot_user.timezone,
        joined_at=bot_user.first_seen.isoformat(),
        preferences={
            "notify_reminders": prefs.notify_reminders,
            "notify_retention": prefs.notify_retention,
            "notify_promo": prefs.notify_promo,
            "notify_birthday": prefs.notify_birthday,
            "birthday_date": prefs.birthday_date.isoformat() if prefs.birthday_date else None,
        },
        favorite_master_name=None,
        favorite_service_name=None,
    )


_EDITABLE_USER_FIELDS = {"client_name", "timezone"}
_EDITABLE_PREF_FIELDS = {
    "notify_reminders",
    "notify_retention",
    "notify_promo",
    "notify_birthday",
    "birthday_date",
}


class ProfileUpdateError(ValueError):
    """Raised when the PATCH body contains unknown / invalid fields."""


@transaction.atomic
def update_profile(bot_user: BotUser, payload: dict[str, Any]) -> ProfileSnapshot:
    """Partial update of ``BotUser`` + ``UserPreferences`` from a PATCH body.

    Ignores fields the customer can't change (e.g. ``display_name``,
    ``phone`` — channel-side data). Unknown keys raise
    :class:`ProfileUpdateError` so the API responds with 400 + slug,
    rather than silently dropping a typo.
    """
    unknown = set(payload) - _EDITABLE_USER_FIELDS - _EDITABLE_PREF_FIELDS
    if unknown:
        raise ProfileUpdateError(f"unknown fields: {sorted(unknown)}")

    # Field max_lengths mirror apps/identity/models.py: BotUser.client_name(150) +
    # BotUser.timezone(64). Hardcoded to avoid runtime _meta walks on every
    # PATCH (and to keep mypy happy with the Field | ForeignObjectRel union).
    _USER_MAX_LEN = {"client_name": 150, "timezone": 64}
    user_dirty = False
    for key in _EDITABLE_USER_FIELDS & payload.keys():
        value = payload[key]
        if not isinstance(value, str):
            raise ProfileUpdateError(f"{key} must be a string")
        setattr(bot_user, key, value.strip()[: _USER_MAX_LEN[key]])
        user_dirty = True
    if user_dirty:
        bot_user.save(update_fields=[*(_EDITABLE_USER_FIELDS & payload.keys()), "last_seen"])

    prefs, _ = UserPreferences.all_tenants.get_or_create(
        bot_user=bot_user,
        defaults={"tenant": bot_user.tenant},
    )
    prefs_dirty: list[str] = []
    for key in _EDITABLE_PREF_FIELDS & payload.keys():
        value = payload[key]
        if key == "birthday_date":
            parsed = _parse_date(value)
            prefs.birthday_date = parsed
        else:  # boolean toggle
            if not isinstance(value, bool):
                raise ProfileUpdateError(f"{key} must be a boolean")
            setattr(prefs, key, value)
        prefs_dirty.append(key)
    if prefs_dirty:
        prefs.save(update_fields=[*prefs_dirty, "updated_at"])

    return get_profile(bot_user)


def _parse_date(value: Any) -> date | None:
    """Accept ISO 8601 date string or null. Anything else → 400."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ProfileUpdateError("birthday_date must be an ISO 8601 date or null")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ProfileUpdateError(f"birthday_date invalid: {exc}") from exc


DELETE_CONFIRMATION_TOKEN = "УДАЛИТЬ"


class DeletionConfirmationMismatch(ValueError):
    """Raised when the body doesn't carry the expected confirmation token."""


@transaction.atomic
def soft_delete_user(bot_user: BotUser, confirmation: str) -> None:
    """Scrub PII + mark ``deleted_at`` + drop preferences.

    Two-step confirmation: the customer must type
    :data:`DELETE_CONFIRMATION_TOKEN` exactly in the UI textarea. The
    Mini App relays that string here; mismatch raises. Re-running on an
    already-deleted user is a no-op (idempotent).

    DRF-956 runtime hardening: also clear ``display_name`` and
    ``avatar_url`` so the erased profile exposes no channel-side PII.
    """
    if confirmation != DELETE_CONFIRMATION_TOKEN:
        raise DeletionConfirmationMismatch(f"confirmation must equal {DELETE_CONFIRMATION_TOKEN!r}")
    if bot_user.deleted_at is not None:
        return  # already scrubbed
    bot_user.deleted_at = timezone.now()
    bot_user.display_name = ""
    bot_user.avatar_url = ""
    bot_user.client_name = ""
    bot_user.phone = ""
    bot_user.context = {}
    bot_user.save(
        update_fields=[
            "deleted_at",
            "display_name",
            "avatar_url",
            "client_name",
            "phone",
            "context",
            "last_seen",
        ]
    )
    UserPreferences.all_tenants.filter(bot_user=bot_user).delete()
