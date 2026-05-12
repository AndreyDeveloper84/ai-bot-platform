"""Churn risk + lifecycle stage (DRF-530 / Sprint 6 / P4).

Phase 0 heuristic mapping (recency, avg_visit_interval, frequency) →
churn_risk ∈ [0, 1] + lifecycle_stage ∈ {new, active, lapsing, churned}.

### Why not ML in Phase 0

A 1-tenant pilot with <6 months of real visit data can't train a
churn classifier without overfitting. The heuristic gives operators
a transparent score they can sanity-check against intuition, and the
signature (`ChurnResult`) is stable so Phase 1 ML swap is a body
replacement, not an API rewrite.

### Heuristic formula

Given `recency_days` (days since last visit) and `avg_visit_interval_days`
(mean gap between historical visits):

- ratio = recency / avg_interval
- churn_risk = clamp(ratio / 3.0, 0.0, 1.0)
  - ratio ≤ 1   → 0..0.33  (active, visited within typical cadence)
  - ratio ≈ 2   → 0.67     (lapsing, one cadence overdue)
  - ratio ≥ 3   → 1.0      (churned, three cadences overdue)

`lifecycle_stage`:
- `new`      if frequency < 3 (insufficient history)
- `active`   if ratio ≤ 1.0
- `lapsing`  if 1.0 < ratio ≤ 3.0
- `churned`  if ratio > 3.0

### Defensive cases

- `avg_visit_interval_days is None` (frequency < 2): can't compute ratio
  → lifecycle_stage = `new`, churn_risk = 0.0
- `recency_days is None` (no visits): same as above
- `avg_visit_interval_days == 0` (all visits same day): treat as 30 (monthly
  cadence assumption, mirrors LTV same-day cluster handling)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChurnResult:
    """Output of :func:`compute_churn_risk`. Persisted by P6 recompute service."""

    churn_risk: float  # [0.0, 1.0]
    lifecycle_stage: str  # 'new' | 'active' | 'lapsing' | 'churned'


# Same-day-cluster fallback cadence (days). Matches the same-day cluster
# assumption in ltv.compute_ltv — keeps both heuristics consistent.
_SAME_DAY_FALLBACK_INTERVAL = 30


def compute_churn_risk(
    *,
    recency_days: int | None,
    avg_visit_interval_days: int | None,
    frequency_visits: int = 0,
) -> ChurnResult:
    """Compute churn risk + lifecycle stage from RFM-shaped inputs.

    Args:
      recency_days: days since last visit. None = no visits yet.
      avg_visit_interval_days: mean gap between visits. None when
        frequency < 2 (need at least 2 visits to estimate cadence).
      frequency_visits: visit count (drives the `new` stage gate).

    Returns:
      :class:`ChurnResult`. Score clamped to [0.0, 1.0].
    """

    # New: not enough history to score churn meaningfully.
    if frequency_visits < 3 or recency_days is None or avg_visit_interval_days is None:
        return ChurnResult(churn_risk=0.0, lifecycle_stage="new")

    interval = avg_visit_interval_days
    if interval <= 0:
        # Same-day cluster — assume monthly cadence (mirrors LTV heuristic).
        interval = _SAME_DAY_FALLBACK_INTERVAL

    ratio = recency_days / interval
    score = min(max(ratio / 3.0, 0.0), 1.0)

    if ratio <= 1.0:
        stage = "active"
    elif ratio <= 3.0:
        stage = "lapsing"
    else:
        stage = "churned"

    return ChurnResult(churn_risk=score, lifecycle_stage=stage)
