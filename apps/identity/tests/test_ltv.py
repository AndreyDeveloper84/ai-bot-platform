"""LTV computation tests (DRF-529 / Sprint 6 / P3)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


from apps.identity.services.ltv import LTVResult, compute_ltv


@dataclass
class _Visit:
    visited_at: datetime
    amount: Decimal


_AS_OF = datetime(2026, 5, 12, tzinfo=UTC)


def _visits(days_back_amounts: list[tuple[int, str]]) -> list[_Visit]:
    return [
        _Visit(visited_at=_AS_OF - timedelta(days=d), amount=Decimal(a))
        for d, a in days_back_amounts
    ]


class TestEmpty:
    def test_no_visits_zero(self):
        r = compute_ltv([], as_of=_AS_OF)
        assert r.total_spend == Decimal("0")
        assert r.predicted_ltv_12m == Decimal("0")


class TestSingleVisit:
    def test_single_visit_total_only(self):
        # One visit — total counted, projection 0 (no cadence).
        r = compute_ltv(_visits([(5, "2500")]), as_of=_AS_OF)
        assert r.total_spend == Decimal("2500")
        assert r.predicted_ltv_12m == Decimal("0")


class TestProjection:
    def test_monthly_cadence_projects_12_visits(self):
        # 4 visits spanning ~90 days = 30 days/visit ⇒ 365/30 ≈ 12.17 visits/yr
        # Avg check 2000 ⇒ 12.17 × 2000 ≈ 24333.33
        visits = _visits([(0, "2000"), (30, "2000"), (60, "2000"), (90, "2000")])
        r = compute_ltv(visits, as_of=_AS_OF)
        assert r.total_spend == Decimal("8000")
        # Projection ~24,333.33 (some rounding)
        assert Decimal("24000") <= r.predicted_ltv_12m <= Decimal("24500")

    def test_weekly_cadence_high_projection(self):
        # 5 visits over 28 days = 7 days/visit ⇒ 365/7 ≈ 52 visits/yr
        # Avg check 1500 ⇒ ~78,000
        visits = _visits([(0, "1500"), (7, "1500"), (14, "1500"), (21, "1500"), (28, "1500")])
        r = compute_ltv(visits, as_of=_AS_OF)
        assert r.total_spend == Decimal("7500")
        assert r.predicted_ltv_12m > Decimal("70000")

    def test_quarterly_cadence_low_projection(self):
        # 2 visits 90 days apart ⇒ 365/90 ≈ 4 visits/yr × 2000 = 8000
        visits = _visits([(0, "2000"), (90, "2000")])
        r = compute_ltv(visits, as_of=_AS_OF)
        assert r.total_spend == Decimal("4000")
        assert Decimal("7500") <= r.predicted_ltv_12m <= Decimal("8500")


class TestSameDayCluster:
    def test_same_day_cluster_projects_monthly(self):
        # 5 visits all same day (package buy) → assume monthly going forward.
        # 12 visits/yr × 2000 avg = 24000
        visits = _visits([(0, "2000"), (0, "2000"), (0, "2000"), (0, "2000"), (0, "2000")])
        r = compute_ltv(visits, as_of=_AS_OF)
        assert r.predicted_ltv_12m == Decimal("24000.00")


class TestDecimalPrecision:
    def test_total_preserves_decimal(self):
        r = compute_ltv(_visits([(0, "1500.50"), (30, "2300.25")]), as_of=_AS_OF)
        assert r.total_spend == Decimal("3800.75")

    def test_projection_quantized_to_cents(self):
        r = compute_ltv(_visits([(0, "1000"), (45, "1000"), (90, "1000")]), as_of=_AS_OF)
        # Verify scale ≤ 2 (cents).
        _, _, exp = r.predicted_ltv_12m.as_tuple()
        assert exp >= -2


class TestResult:
    def test_returns_dataclass(self):
        r = compute_ltv([], as_of=_AS_OF)
        assert isinstance(r, LTVResult)
