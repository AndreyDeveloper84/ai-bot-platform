"""RFM computation tests (DRF-528 / Sprint 6 / P2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal


from apps.identity.services.rfm import RFMResult, compute_rfm


@dataclass
class _Visit:
    visited_at: datetime
    amount: Decimal


_AS_OF = datetime(2026, 5, 12, tzinfo=UTC)


def _visits_at_days_ago(days_list, amount=Decimal("2000")):
    return [_Visit(visited_at=_AS_OF - timedelta(days=d), amount=amount) for d in days_list]


class TestEmptyHistory:
    def test_no_visits_returns_new(self):
        r = compute_rfm([], as_of=_AS_OF)
        assert r.rfm_segment == "new"
        assert r.frequency_visits == 0
        assert r.monetary_total == Decimal("0")
        assert r.recency_days is None


class TestSegmentClassification:
    def test_single_visit_today_is_new(self):
        r = compute_rfm(_visits_at_days_ago([0]), as_of=_AS_OF)
        assert r.rfm_segment == "new"

    def test_two_visits_today_is_new(self):
        r = compute_rfm(_visits_at_days_ago([0, 5]), as_of=_AS_OF)
        assert r.rfm_segment == "new"  # frequency ≤ new_max_visits=2

    def test_champion_high_freq_recent(self):
        # 10 visits, most recent within 14 days → champion
        visits = _visits_at_days_ago([3, 14, 30, 45, 60, 75, 90, 105, 120, 135])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment == "champion"
        assert r.frequency_visits == 10
        assert r.recency_days == 3

    def test_loyal_medium_freq_recent(self):
        # 5 visits (>= loyal_min=4), most recent ≤ 14 days but < champion_min=8
        visits = _visits_at_days_ago([5, 30, 60, 90, 120])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment == "loyal"

    def test_loyal_within_45_days(self):
        # 5 visits, most recent 30 days ago → loyal band
        visits = _visits_at_days_ago([30, 60, 90, 120, 150])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment == "loyal"

    def test_at_risk_within_180_days(self):
        # 5 visits, most recent 90 days ago → at_risk
        visits = _visits_at_days_ago([90, 120, 150, 180, 210])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment == "at_risk"

    def test_hibernating_old_recency(self):
        # 5 visits, most recent 365 days ago → hibernating
        visits = _visits_at_days_ago([365, 400, 500, 600, 700])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment == "hibernating"

    def test_at_risk_recent_but_few_visits(self):
        # 3 visits (above `new` boundary but below loyal_min=4), within 45 days
        # → at_risk (early lapsing pattern)
        visits = _visits_at_days_ago([15, 30, 40])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment == "at_risk"


class TestResult:
    def test_returns_dataclass(self):
        r = compute_rfm([], as_of=_AS_OF)
        assert isinstance(r, RFMResult)

    def test_recency_uses_most_recent(self):
        # 10 vs 5 vs 100 days ago — recency = 5
        visits = _visits_at_days_ago([5, 10, 100])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.recency_days == 5

    def test_monetary_sums_decimals(self):
        visits = [
            _Visit(visited_at=_AS_OF, amount=Decimal("1500.50")),
            _Visit(visited_at=_AS_OF, amount=Decimal("2300.25")),
        ]
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.monetary_total == Decimal("3800.75")

    def test_frequency_counts_visits(self):
        visits = _visits_at_days_ago([1, 2, 3, 4, 5])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.frequency_visits == 5

    def test_future_visit_clamps_recency_to_zero(self):
        # Defensive: a visit in the future (clock drift) → recency 0, not negative.
        visit = _Visit(visited_at=_AS_OF + timedelta(hours=1), amount=Decimal("100"))
        r = compute_rfm([visit, *_visits_at_days_ago([100])], as_of=_AS_OF)
        assert r.recency_days == 0


class TestSettingsOverride:
    def test_settings_threshold_overrides_default(self, settings):
        # Tighten champion to require recency ≤ 1 day. Now a 5-day-recent
        # high-frequency user falls to loyal.
        settings.RFM_THRESHOLDS = {"champion_recency": 1}
        visits = _visits_at_days_ago([5, 14, 30, 45, 60, 75, 90, 105])  # 8 visits
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment == "loyal"

    def test_partial_settings_merge_keeps_defaults(self, settings):
        # Override only one key — others stay at default.
        settings.RFM_THRESHOLDS = {"new_max_visits": 0}
        # Single visit today → no longer `new` (override), recent + low freq → at_risk
        visits = _visits_at_days_ago([5])
        r = compute_rfm(visits, as_of=_AS_OF)
        assert r.rfm_segment != "new"


class TestAsOfDefault:
    def test_as_of_defaults_to_now_when_none(self):
        # Just verify it doesn't crash when as_of is omitted.
        r = compute_rfm(_visits_at_days_ago([0]))
        assert isinstance(r, RFMResult)
