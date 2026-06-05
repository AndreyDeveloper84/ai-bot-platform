"""Master notification preferences service layer (master-mobile §M7).

Powers GET / PATCH ``/api/v1/master/notification-prefs/``. The HTTP
layer is a thin shell — validation, audit, and the urgent-forced-ON
contract live here so tests can pin the policy without standing up a
full request.

### Field policy

* ``urgent`` MUST stay True. The DB has a CheckConstraint as backstop;
  this layer raises :class:`NotificationPrefsError` ``urgent_forced_on``
  with HTTP 400 mapping before the constraint ever fires.
* Quiet-hours times: legal range is any HH:MM, but ``quiet_start ==
  quiet_end`` is rejected (``time_invalid``) because a zero-length
  window is ambiguous (always-quiet vs never-quiet). ``quiet_start >
  quiet_end`` is allowed — explicit support for overnight windows per
  §M7's default 21:00 → 09:00.
* Unknown JSON keys are rejected (``bad_request``) — defends against
  client typos silently failing.

### Audit

Every successful PATCH writes one ``master.notification_prefs_updated``
audit row with a ``changes`` diff: ``{field: {before, after}}`` for
each toggled field. No-op PATCHes (caller sent only unchanged values)
write no audit row.
"""

from __future__ import annotations

import logging
from datetime import time
from typing import Any

from django.db import transaction

from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster
from apps.events.vocabulary import MASTER_NOTIFICATION_PREFS_UPDATED
from apps.identity.models import BotUser
from apps.notifications.models import MasterNotificationPrefs

logger = logging.getLogger(__name__)


# Fields the PATCH endpoint accepts. Mirrors the model 1:1 minus the
# read-only / system-managed columns. Driven from a single tuple so
# the validator + diff builder can't drift apart.
_BOOL_FIELDS: tuple[str, ...] = (
    "new_booking",
    "booking_change",
    "personal_message",
    "urgent",
    "quiet_hours_enabled",
    "morning_brief",
    "evening_summary",
)

_TIME_FIELDS: tuple[str, ...] = (
    "quiet_start",
    "quiet_end",
)

ALLOWED_PATCH_FIELDS: frozenset[str] = frozenset(_BOOL_FIELDS + _TIME_FIELDS)


class NotificationPrefsError(Exception):
    """Validation failure inside :func:`update_prefs`.

    Carries a stable ``slug`` so the view layer can return a 400 with
    a predictable error envelope.
    """

    slug: str = "bad_request"

    def __init__(self, slug: str, detail: str) -> None:
        super().__init__(detail)
        self.slug = slug
        self.detail = detail


def get_or_create_prefs(master: CatalogMaster) -> MasterNotificationPrefs:
    """Return the master's prefs row, creating with §M7 defaults if absent.

    Idempotent — second call returns the existing row. The
    ``select_for_update`` ladder isn't needed here because the worst
    race produces a duplicate-key error on the OneToOne which Django
    surfaces as :class:`IntegrityError`; we let the second caller's
    ``.get()`` win on retry. For real concurrent first-create we wrap
    in ``transaction.atomic`` + ``get_or_create`` (Django handles the
    race internally on supported backends).
    """

    with transaction.atomic():
        prefs, _ = MasterNotificationPrefs.all_tenants.get_or_create(
            master=master,
            defaults={"tenant": master.tenant},
        )
    return prefs


def _validate_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Normalise + validate a partial-update payload.

    Returns the cleaned dict (string time values coerced to
    :class:`datetime.time`). Raises :class:`NotificationPrefsError`
    on any rejection.
    """

    if not isinstance(patch, dict):
        raise NotificationPrefsError("bad_request", "body must be a JSON object")

    unknown = set(patch.keys()) - ALLOWED_PATCH_FIELDS
    if unknown:
        # Deterministic ordering for stable test assertions / error
        # detail formatting.
        first = sorted(unknown)[0]
        raise NotificationPrefsError("bad_request", f"unknown field: {first}")

    cleaned: dict[str, Any] = {}

    for fname in _BOOL_FIELDS:
        if fname not in patch:
            continue
        raw = patch[fname]
        if not isinstance(raw, bool):
            raise NotificationPrefsError("bad_request", f"{fname} must be boolean")
        cleaned[fname] = raw

    for fname in _TIME_FIELDS:
        if fname not in patch:
            continue
        raw = patch[fname]
        if not isinstance(raw, str):
            raise NotificationPrefsError("time_invalid", f"{fname} must be 'HH:MM' string")
        cleaned[fname] = _parse_hhmm(raw, fname)

    # Cross-field invariant: urgent MUST stay True. We surface this
    # BEFORE the DB CheckConstraint so the client gets a stable slug
    # rather than a 500 from IntegrityError.
    if cleaned.get("urgent") is False:
        raise NotificationPrefsError(
            "urgent_forced_on",
            "«Срочно» cannot be disabled (safety — §M7 line 805)",
        )

    return cleaned


def _parse_hhmm(raw: str, fname: str) -> time:
    """Parse ``"HH:MM"`` into :class:`datetime.time`. 400 on bad input."""

    parts = raw.split(":")
    if len(parts) != 2:
        raise NotificationPrefsError("time_invalid", f"{fname} must be 'HH:MM'")
    try:
        hh = int(parts[0])
        mm = int(parts[1])
    except ValueError as exc:
        raise NotificationPrefsError("time_invalid", f"{fname} must be 'HH:MM'") from exc
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise NotificationPrefsError("time_invalid", f"{fname} out of range")
    return time(hh, mm)


def _build_diff(
    prefs: MasterNotificationPrefs, cleaned: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return ``{field: {before, after}}`` for every field that changed.

    Time values are stringified to ``"HH:MM"`` so the audit row's
    JSON payload stays roundtrip-stable (Django's default JSON encoder
    refuses :class:`datetime.time`).
    """

    diff: dict[str, dict[str, Any]] = {}
    for fname, new_val in cleaned.items():
        old_val = getattr(prefs, fname)
        if old_val == new_val:
            continue
        if isinstance(old_val, time):
            old_repr: Any = old_val.strftime("%H:%M")
        else:
            old_repr = old_val
        if isinstance(new_val, time):
            new_repr: Any = new_val.strftime("%H:%M")
        else:
            new_repr = new_val
        diff[fname] = {"before": old_repr, "after": new_repr}
    return diff


def update_prefs(
    master: CatalogMaster,
    *,
    patch: dict[str, Any],
    actor: BotUser,
) -> MasterNotificationPrefs:
    """Apply a partial update to the master's prefs.

    Behaviour:
      * Creates the row on first access (same contract as
        :func:`get_or_create_prefs`).
      * Validates each supplied field; rejects unknown keys and
        ``urgent=False`` before any DB write.
      * Validates the resulting quiet-hours window: enabled + identical
        start/end → ``time_invalid`` (zero-length window is ambiguous).
      * Writes an audit row IFF at least one field changed value.

    Raises :class:`NotificationPrefsError` on validation failure (view
    layer maps slug → 400 + JSON envelope).
    """

    cleaned = _validate_patch(patch)

    with transaction.atomic():
        prefs = get_or_create_prefs(master)
        # Lock the row so a concurrent PATCH on the same master can't
        # build a stale diff. The OneToOne row is small; the lock is
        # held only for the validation + save window.
        prefs = MasterNotificationPrefs.all_tenants.select_for_update().get(pk=prefs.pk)

        # Resolve the resulting quiet-hours window after the patch
        # so we can validate start != end across mixed-source values
        # (one in patch, the other from existing row).
        resulting_enabled = cleaned.get("quiet_hours_enabled", prefs.quiet_hours_enabled)
        resulting_start = cleaned.get("quiet_start", prefs.quiet_start)
        resulting_end = cleaned.get("quiet_end", prefs.quiet_end)

        if resulting_enabled and resulting_start == resulting_end:
            raise NotificationPrefsError(
                "time_invalid",
                "quiet_start must differ from quiet_end when quiet_hours_enabled",
            )

        diff = _build_diff(prefs, cleaned)
        if not diff:
            # No-op PATCH — return existing row without audit churn.
            return prefs

        for fname, new_val in cleaned.items():
            setattr(prefs, fname, new_val)
        # ``urgent`` is never sent here as False (validator already
        # rejected). The CheckConstraint stays as belt-and-braces.
        prefs.save(update_fields=list(cleaned.keys()) + ["updated_at"])

        write_audit(
            MASTER_NOTIFICATION_PREFS_UPDATED,
            target="notifications.MasterNotificationPrefs",
            target_id=prefs.id,
            payload={
                "tenant_id": str(master.tenant_id),
                "master_id": str(master.id),
                "bot_user_id": str(actor.id),
                "changes": diff,
            },
            actor_id=actor.id,
        )

    return prefs


def serialise_prefs(prefs: MasterNotificationPrefs) -> dict[str, Any]:
    """Wire-format dict for the HTTP envelope. ``HH:MM`` for times."""

    return {
        "new_booking": prefs.new_booking,
        "booking_change": prefs.booking_change,
        "personal_message": prefs.personal_message,
        "urgent": prefs.urgent,
        "quiet_hours_enabled": prefs.quiet_hours_enabled,
        "quiet_start": prefs.quiet_start.strftime("%H:%M"),
        "quiet_end": prefs.quiet_end.strftime("%H:%M"),
        "morning_brief": prefs.morning_brief,
        "evening_summary": prefs.evening_summary,
        "updated_at": prefs.updated_at.isoformat(),
    }
