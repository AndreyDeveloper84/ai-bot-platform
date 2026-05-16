"""Pending-action service layer for B5 destructive booking confirms.

DRF-841 / Phase 1 / B5 (absorbs B4 / DRF-840). Thin wrapper around the
:class:`apps.booking.models.PendingBookingAction` row to keep the
"create with TTL" + "consume atomically" + "expired check" idiom in
exactly one place.

Two operations:

* :func:`create_pending` — write a row with ``expires_at = now + TTL``
  and return its UUID pk. Caller threads that pk into the inline
  keyboard payload.
* :func:`consume_pending` — compare-and-set ``consumed_at`` from NULL
  to now, returning the row only when the CAS won AND the row hasn't
  expired. Idempotent: a double-tap returns ``None`` on the second
  call (caller maps that to the "already handled" reply).

### TTL

10 minutes per spec. Long enough for the user to actually read the
preview card; short enough that a stale message clicked an hour later
returns "слишком много времени прошло" instead of silently executing a
destructive action with possibly-stale slot data.

### Why a CAS, not a separate "claim" + "execute" call

A single ``filter(consumed_at__isnull=True).update(consumed_at=now())``
SQL statement is atomic at the database level — two concurrent tap
events (the network jitter "double-tap" case) result in exactly one
rowcount=1 winner; the loser silently no-ops. No race window. The
``update`` returns the rowcount, not the row, so we re-fetch on the
winner branch — one extra select for clarity is worth the read-after-
write determinism.

### Cleanup

Not implemented in this PR — the row count is bounded by traffic
(10-minute TTL + at most one pending per active booking flow per
user). Phase 2 can add a daily cleanup beat. For now, the
``expires_at`` index makes a "DELETE WHERE expires_at < now - 24h"
prune trivial when ops want to run it.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from django.utils import timezone

from apps.booking.models import PendingBookingAction

if TYPE_CHECKING:
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# 10-minute TTL — module-scope constant so tests can monkeypatch.
PENDING_ACTION_TTL = timedelta(minutes=10)


def create_pending(
    *,
    tenant: "Tenant",
    bot_user: "BotUser",
    kind: str,
    payload: dict[str, Any],
    ttl: timedelta | None = None,
) -> UUID:
    """Persist a pending destructive action; return its opaque token.

    Args:
      tenant: owning :class:`apps.tenancy.models.Tenant`.
      bot_user: the user whose tap will execute this action.
      kind: one of :attr:`PendingBookingAction.Kind` values.
      payload: per-kind argument bundle (see model docstring).
      ttl: optional override of :data:`PENDING_ACTION_TTL`.

    Returns:
      The UUID pk of the new row. Caller embeds it in the inline
      keyboard payload (``cb:book:<verb>:<token>``).
    """
    expires_at = timezone.now() + (ttl or PENDING_ACTION_TTL)
    row = PendingBookingAction.all_tenants.create(
        tenant=tenant,
        bot_user=bot_user,
        kind=kind,
        payload=payload,
        expires_at=expires_at,
    )
    logger.info(
        "bookings.pending.created token=%s kind=%s expires_at=%s",
        row.pk,
        kind,
        expires_at.isoformat(),
    )
    return row.pk


class PendingActionLookup:
    """Lightweight result type for :func:`consume_pending`.

    Three states the caller cares about:

    * row is ``None`` — token not found (deleted, never existed, or
      wrong UUID format upstream).
    * ``expired=True`` — found but the TTL elapsed before tap.
    * ``already_consumed=True`` — found, valid, but a prior tap
      already CAS-claimed it.
    * Otherwise: ``row`` is the freshly-claimed row; caller executes.

    The :func:`consume_pending` factory sets exactly one of the three
    flags per call so the caller's branching is one ``if`` ladder.
    """

    def __init__(
        self,
        *,
        row: PendingBookingAction | None,
        expired: bool = False,
        already_consumed: bool = False,
    ) -> None:
        self.row = row
        self.expired = expired
        self.already_consumed = already_consumed

    @property
    def ok(self) -> bool:
        return self.row is not None and not self.expired and not self.already_consumed


def consume_pending(token: UUID) -> PendingActionLookup:
    """Atomically claim the pending action identified by ``token``.

    Returns a :class:`PendingActionLookup` describing one of:

    * row missing
    * row expired (caller renders "too much time passed" reply)
    * row already consumed (caller renders "already handled" reply)
    * row claimed successfully (caller executes the destructive verb)

    The CAS is a single ``update(consumed_at=now())`` filtered by
    ``consumed_at__isnull=True``. A double-tap is safe — the second
    call's CAS fails (rowcount=0) and returns ``already_consumed``.
    """
    try:
        row = PendingBookingAction.all_tenants.get(pk=token)
    except PendingBookingAction.DoesNotExist:
        logger.info("bookings.pending.not_found token=%s", token)
        return PendingActionLookup(row=None)

    now = timezone.now()
    if row.expires_at <= now:
        logger.info(
            "bookings.pending.expired token=%s expires_at=%s",
            token,
            row.expires_at.isoformat(),
        )
        return PendingActionLookup(row=row, expired=True)

    if row.consumed_at is not None:
        logger.info("bookings.pending.already_consumed token=%s", token)
        return PendingActionLookup(row=row, already_consumed=True)

    rowcount = PendingBookingAction.all_tenants.filter(
        pk=token,
        consumed_at__isnull=True,
    ).update(consumed_at=now)
    if rowcount == 0:
        # Race lost to a concurrent tap — re-fetch to surface the
        # already-consumed flag rather than the in-memory stale copy.
        row.refresh_from_db()
        return PendingActionLookup(row=row, already_consumed=True)

    # We claimed it. Re-fetch to return the current row state with
    # consumed_at populated (callers may read it for audit).
    row.refresh_from_db()
    logger.info("bookings.pending.consumed token=%s kind=%s", token, row.kind)
    return PendingActionLookup(row=row)


def discard_pending(token: UUID) -> bool:
    """Mark a pending action as consumed without executing it.

    Used on ❌ "Отмена" taps: no destructive call, just clear the row
    so a subsequent ✅ tap returns "already handled" instead of
    surprising the user. Returns ``True`` on a successful CAS,
    ``False`` if the row was already consumed / expired.
    """
    now = timezone.now()
    rowcount = PendingBookingAction.all_tenants.filter(
        pk=token,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)
    logger.info("bookings.pending.discarded token=%s rowcount=%d", token, rowcount)
    return rowcount > 0
