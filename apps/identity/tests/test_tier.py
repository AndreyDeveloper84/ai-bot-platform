"""Loyalty tier tests (DRF-531 / Sprint 6 / P5)."""

from __future__ import annotations

from decimal import Decimal


from apps.identity.services.tier import compute_tier


class TestDefaultThresholds:
    def test_zero_is_bronze(self):
        assert compute_tier(monetary_total=Decimal("0"), frequency_visits=0) == "bronze"

    def test_below_silver_is_bronze(self):
        assert compute_tier(monetary_total=Decimal("14999"), frequency_visits=4) == "bronze"

    def test_silver_boundary(self):
        assert compute_tier(monetary_total=Decimal("15000"), frequency_visits=5) == "silver"

    def test_silver_money_low_freq_is_bronze(self):
        # Money OK but frequency below floor → bronze
        assert compute_tier(monetary_total=Decimal("15000"), frequency_visits=4) == "bronze"

    def test_silver_freq_low_money_is_bronze(self):
        # Freq OK but money below floor → bronze
        assert compute_tier(monetary_total=Decimal("14999"), frequency_visits=5) == "bronze"

    def test_gold_boundary(self):
        assert compute_tier(monetary_total=Decimal("40000"), frequency_visits=10) == "gold"

    def test_platinum_boundary(self):
        assert compute_tier(monetary_total=Decimal("100000"), frequency_visits=20) == "platinum"

    def test_highest_tier_wins(self):
        # Platinum-grade money + Silver-grade freq → platinum if both pass.
        # Money huge, freq 25 (>= platinum_min_visits=20) → platinum
        assert compute_tier(monetary_total=Decimal("500000"), frequency_visits=25) == "platinum"

    def test_money_huge_but_freq_only_for_silver_caps_at_silver(self):
        # Mass-spend single-package buyer — frequency caps the tier.
        assert compute_tier(monetary_total=Decimal("500000"), frequency_visits=5) == "silver"


class TestSettingsOverride:
    def test_settings_threshold_overrides(self, settings):
        # Raise platinum bar — now an otherwise-platinum customer is gold.
        settings.TIER_THRESHOLDS = {
            "platinum": {"min_monetary": Decimal("200000"), "min_visits": 30},
        }
        result = compute_tier(monetary_total=Decimal("100000"), frequency_visits=20)
        assert result == "gold"

    def test_inline_overrides_win_over_settings(self, settings):
        settings.TIER_THRESHOLDS = {
            "platinum": {"min_monetary": Decimal("999999"), "min_visits": 999},
        }
        result = compute_tier(
            monetary_total=Decimal("50000"),
            frequency_visits=15,
            overrides={"platinum": {"min_monetary": Decimal("40000"), "min_visits": 10}},
        )
        assert result == "platinum"


class TestNumericInputs:
    def test_accepts_float_monetary(self):
        # Defensive: caller may pass float from a serializer round-trip.
        assert compute_tier(monetary_total=15000.0, frequency_visits=5) == "silver"

    def test_accepts_int_monetary(self):
        assert compute_tier(monetary_total=40000, frequency_visits=10) == "gold"
