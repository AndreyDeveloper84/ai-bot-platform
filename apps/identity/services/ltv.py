"""LTV computation (DRF-529 / Sprint 6 / P3).

Phase 0: linear projection from observed visit cadence + average check
to a 12-month forward estimate. No ML — Phase 1 swaps the body behind
the same :class:`LTVResult` signature.

### Why linear

For a 1-tenant Phase 0 with limited historical data, a 12-month
extrapolation from ``frequency × avg_check`` is honest enough to drive
tier assignment + churn risk weighting. A linear model also has the
property that operators can predict the output by hand from the
ClientProfile aggregates — important for trust during pilot.

Sprint 6 ships the function + signature; Phase 1 replaces the body
with a regression / survival model when we have ≥6 months of real
Booking data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Protocol


class _VisitLike(Protocol):
    visited_at: datetime
    amount: Decimal


@dataclass(frozen=True)
class LTVResult:
    """Output of :func:`compute_ltv`. Persisted by P6 recompute service."""

    total_spend: Decimal  # lifetime sum (== monetary_total from RFM)
    predicted_ltv_12m: Decimal  # forward 12-month projection


# Days in the projection window (12 months).
_PROJECTION_WINDOW_DAYS = 365


def compute_ltv(
    visits: Iterable[_VisitLike],
    *,
    as_of: datetime | None = None,
) -> LTVResult:
    """Compute lifetime spend + 12-month forward projection.

    Args:
      visits: iterable of visit-like objects.
      as_of: anchor for projection start. Defaults to ``datetime.now(UTC)``.

    Returns:
      :class:`LTVResult`. Empty iterable → both 0.

    ### Projection math (Phase 0)

    Let ``N`` = visit count, ``S`` = total spend.

    - If ``N < 2``: not enough history — return ``predicted_ltv_12m = 0``
      (one visit doesn't define a cadence).
    - Otherwise: let ``span_days`` = days between first and last visit.
      Avg cadence = ``span_days / (N - 1)`` days/visit.
      Avg check = ``S / N``.
      Projected visits over next 365 days = ``365 / avg_cadence``.
      ``predicted_ltv_12m = projected_visits × avg_check``.

    ### Edge cases

    - ``span_days == 0`` (all visits same day): cadence undefined →
      project as if user visits monthly (12 visits over 12 months).
      This is generous but matches operator intuition: a customer who
      bought a 10-pack on one day still has 10 visits' worth of value
      to deliver.
    """

    as_of = as_of or datetime.now(timezone.utc)
    visit_list = list(visits)
    n = len(visit_list)
    total = sum((Decimal(v.amount) for v in visit_list), start=Decimal("0"))

    if n == 0:
        return LTVResult(total_spend=Decimal("0"), predicted_ltv_12m=Decimal("0"))

    if n < 2:
        return LTVResult(total_spend=total, predicted_ltv_12m=Decimal("0"))

    avg_check = total / Decimal(n)

    earliest = min(v.visited_at for v in visit_list)
    latest = max(v.visited_at for v in visit_list)
    span_days = max((latest - earliest).days, 0)

    if span_days == 0:
        # Same-day cluster — assume monthly cadence going forward.
        projected_visits = Decimal("12")
    else:
        avg_cadence_days = Decimal(span_days) / Decimal(n - 1)
        projected_visits = Decimal(_PROJECTION_WINDOW_DAYS) / avg_cadence_days

    predicted = (projected_visits * avg_check).quantize(Decimal("0.01"))

    return LTVResult(total_spend=total, predicted_ltv_12m=predicted)
