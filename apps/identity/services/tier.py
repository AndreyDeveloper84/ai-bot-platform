"""Loyalty tier computation (DRF-531 / Sprint 6 / P5).

Pure mapping from RFM aggregates → tier label ∈ {bronze, silver, gold,
platinum}. Operators retune the bands via ``settings.TIER_THRESHOLDS``;
no redeploy needed.

### Why a function over a ClientProfile

Caller (P6 recompute) has computed RFMResult + LTVResult already.
Tier needs ``monetary_total`` + ``frequency_visits`` — both already
in RFMResult. Phase 1 may expand to factor lifetime tenure + churn
risk; the input shape stays stable (named-tuple-friendly).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings


_DEFAULT_THRESHOLDS: dict = {
    # (min_monetary, min_visits) — first match wins, highest first.
    "platinum": {"min_monetary": Decimal("100000"), "min_visits": 20},
    "gold": {"min_monetary": Decimal("40000"), "min_visits": 10},
    "silver": {"min_monetary": Decimal("15000"), "min_visits": 5},
    # Bronze is the floor — everyone else.
}


def _thresholds() -> dict:
    cfg = getattr(settings, "TIER_THRESHOLDS", None) or {}
    return {**_DEFAULT_THRESHOLDS, **cfg}


def compute_tier(
    *,
    monetary_total: Decimal | float | int,
    frequency_visits: int,
    overrides: dict[str, Any] | None = None,
) -> str:
    """Map (monetary, frequency) to tier label.

    Args:
      monetary_total: lifetime spend.
      frequency_visits: lifetime visit count.
      overrides: optional dict that wins over settings (used by tests).

    Returns:
      One of 'bronze' | 'silver' | 'gold' | 'platinum'.
    """

    th = {**_thresholds(), **(overrides or {})}
    money = Decimal(monetary_total)

    # Highest tier wins. Order matters.
    for tier in ("platinum", "gold", "silver"):
        cfg = th.get(tier)
        if not cfg:
            continue
        if money >= Decimal(cfg["min_monetary"]) and frequency_visits >= int(cfg["min_visits"]):
            return tier
    return "bronze"
