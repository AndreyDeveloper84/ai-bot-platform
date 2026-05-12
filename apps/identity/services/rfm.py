"""RFM computation (DRF-528 / Sprint 6 / P2).

Pure function over an iterable of :class:`BookingFact` (or any
duck-typed object with ``visited_at`` + ``amount``). No DB writes —
:func:`apps.identity.services.recompute.recompute_profile` (P6) is
the only writer to :class:`ClientProfile`.

### Inputs

- ``visits``: iterable of objects with at least ``.visited_at`` (aware
  datetime) and ``.amount`` (``Decimal`` or numeric). Order-agnostic
  (the function sorts internally for recency).
- ``as_of``: anchor datetime for recency calculation. Defaults to ``now()``.

### Segment thresholds

Read from ``settings.RFM_THRESHOLDS`` so operators can retune without
a redeploy. Defaults match small-salon traffic shape (champion/loyal/
at_risk/hibernating) plus a ``new`` segment when the customer has
fewer than ``new_max_visits`` visits regardless of recency.

### Output

:class:`RFMResult` — frozen dataclass with recency_days (or None),
frequency_visits, monetary_total, rfm_segment. Caller persists.

### Phase 1 swap

The function operates on an in-memory iterable. Phase 1 hooks the
real Booking model by passing a Booking queryset adapter that yields
the same shape. Signature is stable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Protocol

from django.conf import settings


class _VisitLike(Protocol):
    visited_at: datetime
    amount: Decimal


_DEFAULT_THRESHOLDS: dict = {
    # New: any user below this visit count is `new`, regardless of recency.
    "new_max_visits": 2,
    # Recency days bands (most_recent_visit_days_ago):
    # ≤ champion_recency → champion candidate
    # ≤ loyal_recency → loyal candidate
    # ≤ at_risk_recency → at_risk candidate
    # > at_risk_recency → hibernating
    "champion_recency": 14,
    "loyal_recency": 45,
    "at_risk_recency": 180,
    # Frequency floor for champion (must also have champion-recency).
    "champion_min_visits": 8,
    # Frequency floor for loyal (champion-recency too few visits → still loyal).
    "loyal_min_visits": 4,
}


@dataclass(frozen=True)
class RFMResult:
    """Output of :func:`compute_rfm`. Persisted by P6 recompute service."""

    recency_days: int | None
    frequency_visits: int
    monetary_total: Decimal
    rfm_segment: str  # 'champion' | 'loyal' | 'at_risk' | 'hibernating' | 'new'


def _thresholds() -> dict:
    """Merge settings overrides on top of defaults so partial config works."""

    cfg = getattr(settings, "RFM_THRESHOLDS", None) or {}
    return {**_DEFAULT_THRESHOLDS, **cfg}


def compute_rfm(
    visits: Iterable[_VisitLike],
    *,
    as_of: datetime | None = None,
) -> RFMResult:
    """Compute RFM aggregates + segment for a single bot_user's visit history.

    Args:
      visits: iterable of visit-like objects (``.visited_at``, ``.amount``).
      as_of: anchor for recency calc. Defaults to ``datetime.now(UTC)``.

    Returns:
      :class:`RFMResult`. Empty iterable → ``new`` segment, frequency 0,
      monetary 0, recency None.
    """

    as_of = as_of or datetime.now(timezone.utc)
    th = _thresholds()

    visit_list = list(visits)
    frequency = len(visit_list)
    monetary = sum((Decimal(v.amount) for v in visit_list), start=Decimal("0"))

    if frequency == 0:
        return RFMResult(
            recency_days=None,
            frequency_visits=0,
            monetary_total=Decimal("0"),
            rfm_segment="new",
        )

    most_recent = max(v.visited_at for v in visit_list)
    recency_days = max(0, (as_of - most_recent).days)

    segment = _classify(
        recency_days=recency_days,
        frequency=frequency,
        thresholds=th,
    )

    return RFMResult(
        recency_days=recency_days,
        frequency_visits=frequency,
        monetary_total=monetary,
        rfm_segment=segment,
    )


def _classify(*, recency_days: int, frequency: int, thresholds: dict) -> str:
    """Return segment from recency + frequency. Pure logic, no I/O."""

    # New trumps recency — a freshly registered user with 1 visit is `new`
    # even if visited today; they need history before segment claims meaning.
    if frequency <= thresholds["new_max_visits"]:
        # Exception: if recency is already old, they're hibernating-ish even
        # with few visits — but in salon practice a 1-visit person with old
        # recency is just "lapsed new", which we keep as `new` for Phase 0.
        # Phase 1 may refine.
        return "new"

    if recency_days <= thresholds["champion_recency"]:
        if frequency >= thresholds["champion_min_visits"]:
            return "champion"
        # Recent but not frequent enough — loyal.
        return "loyal"

    if recency_days <= thresholds["loyal_recency"]:
        if frequency >= thresholds["loyal_min_visits"]:
            return "loyal"
        # Visited within 6 weeks but few visits → at_risk (early lapsing).
        return "at_risk"

    if recency_days <= thresholds["at_risk_recency"]:
        return "at_risk"

    return "hibernating"
