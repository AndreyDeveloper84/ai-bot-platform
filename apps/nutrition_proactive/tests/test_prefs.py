"""Time arithmetic and preference parsing (DRF-1285).

Pure-function tests, no database. The proportional-norm cases are the ones
worth reading: they encode the difference between a reminder and a reproach.
"""

from __future__ import annotations

import pytest

from apps.nutrition_proactive import prefs


class TestProportionalNorm:
    """``min(1, elapsed/16) * norm``, elapsed counted from 09:00 local."""

    def test_noon_expects_three_sixteenths(self) -> None:
        # 12:00 is 3h into a 16h day: 3/16 = 18.75% of 2000 ml = 375 ml.
        assert prefs.proportional_norm_ml(2000, local_hour=12) == 375

    def test_wakeup_hour_expects_nothing(self) -> None:
        assert prefs.proportional_norm_ml(2000, local_hour=9) == 0

    def test_before_wakeup_expects_nothing(self) -> None:
        assert prefs.proportional_norm_ml(2000, local_hour=8) == 0

    def test_end_of_day_expects_full_norm(self) -> None:
        # 01:00 = 16h after 09:00 -> factor capped at 1.0.
        assert prefs.proportional_norm_ml(2000, local_hour=1) == 2000

    def test_never_exceeds_norm(self) -> None:
        for hour in range(24):
            assert prefs.proportional_norm_ml(2000, local_hour=hour) <= 2000

    def test_monotonic_through_the_waking_day(self) -> None:
        values = [prefs.proportional_norm_ml(2000, local_hour=h) for h in range(9, 24)]
        assert values == sorted(values)

    def test_threshold_is_half_the_proportional_norm(self) -> None:
        assert prefs.water_threshold_ml(2000, local_hour=12) == pytest.approx(187.5)
        # And emphatically NOT half the daily norm, which would be 1000 ml.
        assert prefs.water_threshold_ml(2000, local_hour=12) < 1000


class TestQuietHours:
    @pytest.mark.parametrize("hour", [22, 23, 0, 1, 5, 8])
    def test_quiet(self, hour: int) -> None:
        assert prefs.is_quiet_hour(hour) is True

    @pytest.mark.parametrize("hour", [9, 12, 17, 21])
    def test_awake(self, hour: int) -> None:
        assert prefs.is_quiet_hour(hour) is False


class TestReportTime:
    def test_default_is_off(self) -> None:
        assert prefs.report_time({}) == prefs.REPORT_OFF
        assert prefs.report_hour({}) is None

    def test_valid_value(self) -> None:
        assert prefs.report_hour({"daily_report_time": "21:00"}) == 21

    @pytest.mark.parametrize(
        "value", ["25:00", "9:00", "21", "", "twenty one", None, 21, {"h": 21}]
    )
    def test_malformed_reads_as_off(self, value) -> None:
        """A corrupt setting must never be repaired into a sending state."""
        assert prefs.report_hour({"daily_report_time": value}) is None


class TestWaterEnabled:
    def test_default_is_off(self) -> None:
        assert prefs.water_enabled({}) is False

    def test_truthy_but_not_true_is_off(self) -> None:
        assert prefs.water_enabled({"water_reminders": 1}) is False
        assert prefs.water_enabled({"water_reminders": "yes"}) is False

    def test_explicit_true(self) -> None:
        assert prefs.water_enabled({"water_reminders": True}) is True


class TestWaterCounters:
    def test_rolls_over_on_a_new_local_day(self) -> None:
        from datetime import date

        stored = {
            "water": {
                "date": "2026-08-22",
                "sent": 3,
                "last_total_ml": 900,
                "ignored_streak": 2,
            }
        }
        counters = prefs.water_counters(stored, date(2026, 8, 23))
        assert counters["sent"] == 0
        assert counters["last_total_ml"] == 0
        # The streak survives the roll: three ignored reminders across two
        # days are still three ignored reminders.
        assert counters["ignored_streak"] == 2

    def test_same_day_is_preserved(self) -> None:
        from datetime import date

        stored = {
            "water": {
                "date": "2026-08-23",
                "sent": 2,
                "last_total_ml": 400,
                "ignored_streak": 1,
            }
        }
        counters = prefs.water_counters(stored, date(2026, 8, 23))
        assert counters["sent"] == 2
        assert counters["last_total_ml"] == 400
