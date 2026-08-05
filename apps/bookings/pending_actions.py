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

D-10 adds the TEXT confirmation helpers shared by the gate callback
skill and the booking skill's flow-continuation routing:

* :data:`_CONFIRM_VOCAB` / :data:`_CANCEL_VOCAB` with
  :func:`is_confirm_text` / :func:`is_cancel_text` — exact-match
  vocabulary for «подтверждаю» / «не надо» style turns.
* :func:`latest_relevant_pending` — the single resolver for "is there
  a pending row this user's text could plausibly refer to", bounding
  stale/expired/consumed relevance to :data:`PENDING_TEXT_GRACE`
  (90 seconds — a real double-tap / late-answer window, not more).

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

# D-10 — how long after expiry/consumption a pending row stays
# "relevant" for the TEXT confirmation path. Bounds the window where
# «подтверждаю» / «отмена» are claimed by the gate skill: within the
# grace window the user gets the controlled reply (expired / already
# handled); beyond it the text falls through to normal routing (echo /
# FAQ) with zero mutation risk.
#
# Review D-10 finding #1: the original 10-minute grace was far wider
# than the real double-tap / late-answer window — a «да» answering an
# unrelated FAQ question 5 minutes after a tap got hijacked into the
# canned "already handled" reply. 90 seconds covers genuine
# double-taps and slightly-late answers; anything older routes
# normally. (The ACTIVE preview window is unchanged — an unconsumed,
# unexpired row is relevant for its full 10-minute TTL regardless.)
PENDING_TEXT_GRACE = timedelta(seconds=90)


# ─── Text confirmation vocabulary (D-10) ─────────────────────────────
# Exact-match after normalization — substring matching would hijack
# unrelated turns («да, но…», «ничего не надо делать»). Both ё/е forms
# listed explicitly instead of a normalization fold, keeps the sets
# greppable.
_CONFIRM_VOCAB: frozenset[str] = frozenset(
    {
        "подтверждаю",
        "подтвердить",
        "подтверди",
        "да",
        "давай",
        "ок",
        "окей",
        "верно",
        "всё верно",
        "все верно",
        "согласен",
        "согласна",
        "точно",
        "угу",
        "ага",
        "конечно",
    }
)
_CANCEL_VOCAB: frozenset[str] = frozenset(
    {
        "отмена",
        "отменить",
        "не надо",
        "не нужно",
        "нет",
        "передумал",
        "передумала",
        "отбой",
        "не хочу",
        "стоп",
    }
)


def normalize_gate_text(text: str) -> str:
    """Lowercase, collapse whitespace, strip trailing punctuation/emoji."""
    normalized = " ".join((text or "").lower().split())
    return normalized.strip(" .,!?)»«…")


def is_confirm_text(text: str) -> bool:
    """True when ``text`` is exactly a confirmation phrase."""
    return normalize_gate_text(text) in _CONFIRM_VOCAB


def is_cancel_text(text: str) -> bool:
    """True when ``text`` is exactly a cancellation phrase."""
    return normalize_gate_text(text) in _CANCEL_VOCAB


def latest_relevant_pending(
    *,
    tenant: "Tenant",
    bot_user: "BotUser",
) -> PendingBookingAction | None:
    """Latest pending row still relevant for the text-confirmation path.

    Relevant means one of:

    * unconsumed and not expired → active preview awaiting a decision;
    * unconsumed, expired within :data:`PENDING_TEXT_GRACE` → controlled
      "too much time" reply via the normal consume path;
    * consumed within :data:`PENDING_TEXT_GRACE` → controlled "already
      handled" reply (duplicate confirm / double-tap).

    Anything older → ``None``: the turn keeps its previous routing and
    no mutation can fire. Rows are scoped by (tenant, bot_user) — the
    same ownership pair the callback tap path authorises against.
    """
    row = (
        PendingBookingAction.all_tenants.filter(
            tenant=tenant,
            bot_user=bot_user,
        )
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return None
    now = timezone.now()
    if row.consumed_at is None:
        if row.expires_at > now - PENDING_TEXT_GRACE:
            return row
        return None
    if row.consumed_at > now - PENDING_TEXT_GRACE:
        return row
    return None


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

    # Bookings/callbacks retro #1: include ``expires_at__gt=now`` in the
    # CAS filter. The Python-side check at line 157 reads an un-locked
    # snapshot; a token whose ``expires_at`` ticks past ``now`` between
    # that read and this UPDATE would otherwise be claimed despite being
    # expired. Mirrors the well-formed filter in ``discard_pending``.
    rowcount = PendingBookingAction.all_tenants.filter(
        pk=token,
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)
    if rowcount == 0:
        # CAS lost — could be either of two states; refresh and
        # disambiguate so the caller renders the right reply. Check
        # ``consumed_at`` BEFORE expiry: if both happened in quick
        # succession (consume won by another tap, then expiry ticked
        # past during our refresh), the user-facing truth is «another
        # tap handled it» rather than «too much time passed».
        row.refresh_from_db()
        if row.consumed_at is not None:
            return PendingActionLookup(row=row, already_consumed=True)
        return PendingActionLookup(row=row, expired=True)

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
