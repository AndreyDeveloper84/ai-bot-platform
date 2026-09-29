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


class TestTimezoneSourceAfterUnsetBecameEmpty:
    """«Не задано» перестало выглядеть как Москва (DRF-1606).

    До этой правки умолчанием колонки стоял настоящий пояс, и
    ``resolve_timezone`` сравнивал строку с ``Europe/Moscow``, чтобы
    догадаться, выбирал ли человек хоть что-то. Значит **явный
    московский ответ был неотличим от молчания** и уезжал на пояс
    тенанта с источником ``"tenant"``.

    Теперь молчание пусто, и разница выразима. Тест держит именно её:
    два человека с одинаковым итоговым поясом, но разным источником.
    """

    class _Tenant:
        timezone = "Europe/Moscow"

    class _User:
        def __init__(self, tz: str) -> None:
            self.timezone = tz
            self.tenant = TestTimezoneSourceAfterUnsetBecameEmpty._Tenant()

    def test_silence_and_an_explicit_moscow_are_now_different(self) -> None:
        silent = self._User("")
        chose_moscow = self._User("Europe/Moscow")

        tz_silent, source_silent = prefs.resolve_timezone(silent)
        tz_chosen, source_chosen = prefs.resolve_timezone(chose_moscow)

        # Пояс у обоих один — и это НЕ то, что различает случаи.
        assert str(tz_silent) == str(tz_chosen) == "Europe/Moscow"
        # А вот происхождение разное, и раньше оно было одинаковым.
        assert source_silent == "tenant"
        assert source_chosen == "botuser"

    def test_an_explicit_zone_wins_over_the_salon(self) -> None:
        user = self._User("Asia/Yekaterinburg")
        tz, source = prefs.resolve_timezone(user)
        assert str(tz) == "Asia/Yekaterinburg"
        assert source == "botuser"

    def test_empty_falls_through_to_the_salon(self) -> None:
        tz, source = prefs.resolve_timezone(self._User(""))
        assert str(tz) == "Europe/Moscow"
        assert source == "tenant"

    def test_whitespace_is_silence_too(self) -> None:
        # Пробел — не ответ. Иначе «не задано» получило бы второе
        # написание, а два способа сказать одно — это два способа
        # разойтись.
        tz, source = prefs.resolve_timezone(self._User("   "))
        assert source == "tenant"
        assert str(tz) == "Europe/Moscow"

    def test_garbage_does_not_pass_as_a_zone(self) -> None:
        """Мусор в колонке не становится поясом человека.

        Пока `PATCH /me` не проверяет значение (DRF-1477, пункт 3), сюда
        может доехать что угодно. Оно обязано провалиться на салон, а не
        уронить рассылку.
        """
        tz, source = prefs.resolve_timezone(self._User("не знаю"))
        assert source == "tenant"
        assert str(tz) == "Europe/Moscow"

    def test_no_tenant_and_no_choice_lands_on_the_fallback(self) -> None:
        class _Orphan:
            timezone = ""
            tenant = None

        tz, source = prefs.resolve_timezone(_Orphan())
        assert str(tz) == prefs.FALLBACK_TZ
        assert source == "fallback"

    def test_the_sentinel_is_gone_not_parked_next_to_the_new_check(self) -> None:
        """§79 — лучшее имя пропуска это отсутствие пропуска.

        Оставить `UNSET_TZ_SENTINEL` рядом с новой проверкой значило бы
        завести второй способ сказать «не задано».
        """
        # Положительная стража ВПЕРЕДИ и на тех же данных: `hasattr` на
        # этом модуле умеет находить имена. Без неё «имени нет» было бы
        # верно и для переименованного модуля, и для опечатки в самой
        # проверке — то есть доказывало бы что угодно.
        assert hasattr(prefs, "FALLBACK_TZ")
        assert hasattr(prefs, "resolve_timezone")
        assert not hasattr(prefs, "UNSET_TZ_SENTINEL")
