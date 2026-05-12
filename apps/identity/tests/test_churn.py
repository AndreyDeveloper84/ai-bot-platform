"""Churn risk + lifecycle stage tests (DRF-530 / Sprint 6 / P4)."""

from __future__ import annotations

from apps.identity.services.churn import ChurnResult, compute_churn_risk


class TestNewStage:
    def test_zero_visits_is_new(self):
        r = compute_churn_risk(recency_days=None, avg_visit_interval_days=None, frequency_visits=0)
        assert r.lifecycle_stage == "new"
        assert r.churn_risk == 0.0

    def test_one_visit_is_new(self):
        # frequency=1 → no interval can be computed → new
        r = compute_churn_risk(recency_days=5, avg_visit_interval_days=None, frequency_visits=1)
        assert r.lifecycle_stage == "new"

    def test_two_visits_is_new(self):
        # frequency=2 → interval exists but <3 visits → still new
        r = compute_churn_risk(recency_days=5, avg_visit_interval_days=30, frequency_visits=2)
        assert r.lifecycle_stage == "new"


class TestActiveStage:
    def test_recent_within_interval(self):
        # recency=15d ≤ interval=30d → ratio 0.5 → active, low churn
        r = compute_churn_risk(recency_days=15, avg_visit_interval_days=30, frequency_visits=5)
        assert r.lifecycle_stage == "active"
        assert 0.0 <= r.churn_risk <= 0.34

    def test_at_interval_boundary(self):
        # ratio exactly 1.0 → still active
        r = compute_churn_risk(recency_days=30, avg_visit_interval_days=30, frequency_visits=5)
        assert r.lifecycle_stage == "active"


class TestLapsingStage:
    def test_two_intervals_overdue_is_lapsing(self):
        # ratio = 60/30 = 2.0 → lapsing, churn_risk ≈ 0.67
        r = compute_churn_risk(recency_days=60, avg_visit_interval_days=30, frequency_visits=5)
        assert r.lifecycle_stage == "lapsing"
        assert 0.60 <= r.churn_risk <= 0.70


class TestChurnedStage:
    def test_three_intervals_is_boundary(self):
        # ratio exactly 3.0 → still lapsing (3.0 inclusive on lapsing side)
        r = compute_churn_risk(recency_days=90, avg_visit_interval_days=30, frequency_visits=5)
        assert r.lifecycle_stage == "lapsing"
        assert r.churn_risk == 1.0  # clamp kicks in at ratio=3

    def test_beyond_three_intervals_is_churned(self):
        # ratio = 120/30 = 4.0 → churned
        r = compute_churn_risk(recency_days=120, avg_visit_interval_days=30, frequency_visits=5)
        assert r.lifecycle_stage == "churned"
        assert r.churn_risk == 1.0

    def test_very_old_recency_clamps_score(self):
        # ratio = 365/30 ≈ 12 → score clamped to 1.0
        r = compute_churn_risk(recency_days=365, avg_visit_interval_days=30, frequency_visits=10)
        assert r.churn_risk == 1.0
        assert r.lifecycle_stage == "churned"


class TestSameDayClusterFallback:
    def test_zero_interval_uses_monthly_fallback(self):
        # avg_interval=0 (package buy, all visits same day) → fallback 30d
        # recency=15 ⇒ ratio 0.5 ⇒ active
        r = compute_churn_risk(recency_days=15, avg_visit_interval_days=0, frequency_visits=5)
        assert r.lifecycle_stage == "active"

    def test_negative_interval_treated_as_fallback(self):
        # Defensive: negative shouldn't happen but if it does, fallback applies.
        r = compute_churn_risk(recency_days=30, avg_visit_interval_days=-1, frequency_visits=5)
        assert r.lifecycle_stage == "active"


class TestResult:
    def test_returns_dataclass(self):
        r = compute_churn_risk(recency_days=None, avg_visit_interval_days=None, frequency_visits=0)
        assert isinstance(r, ChurnResult)

    def test_score_in_unit_range(self):
        for recency in [0, 10, 30, 60, 90, 180, 365]:
            r = compute_churn_risk(
                recency_days=recency, avg_visit_interval_days=30, frequency_visits=5
            )
            assert 0.0 <= r.churn_risk <= 1.0
