"""DRF-2766 фаза 2 — итог дня с оценками ИИ в чате: «≈», «неполный», не ноль.

Решение владельца 04.10, п.3: оценки ИИ входят в дневной итог; итог
помечается «≈ … ккал, включая оценки ИИ»; запись без значения — не ноль,
итог неполный. Решение главного окна 04.10: остаток и перебор («осталось N»,
«вышло на N больше») — только о дне, целиком посчитанном проверенными
числами; нудж по приблизительному числу — совет, его нет.

Жалоба владельца, с которой это началось: записал «≈ 150 ккал · Оценка ИИ», а
итог был «0 из 2588». Половина причины — здесь: клиент читал итог как
``or 0.0``, и «итога нет» становилось нулём.

Фикстуры с ориентиром — из ``test_render`` (они в списке
``nutrition_target_guard``).
"""

from __future__ import annotations

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc
from apps.nutrition_proactive import render
from apps.nutrition_proactive.tests import test_render as fx

AI_TAIL = ", включая оценки ИИ"
INCOMPLETE = "Итог неполный: не у всех записей есть калории."
UNKNOWN = "Калории: пока не посчитаны."


def _report(**summary_over) -> str:
    profile = fx.profile(goal="lose")
    return render.render_daily_report(
        fx.summary(entries=[{"id": 1}, {"id": 2}], **summary_over), fx.water(), profile
    )


class TestTheCaloriesLine:
    def test_a_fully_verified_day_reads_as_before_with_its_remark(self) -> None:
        text = _report(calories_total=1500.0)
        assert "Калории: 1500 из 1900 ккал." in text
        assert "До ориентира по калориям осталось 400 ккал." in text
        assert AI_TAIL not in text

    def test_ai_estimates_in_the_total_say_so_and_carry_no_remark(self) -> None:
        text = _report(calories_total=1500.0, calories_ai_included=1)
        assert f"Калории: ≈ 1500 из 1900 ккал{AI_TAIL}." in text
        assert "осталось" not in text
        assert "больше ориентира" not in text

    def test_an_overshoot_with_ai_estimates_is_not_called_an_overshoot(self) -> None:
        text = _report(calories_total=2500.0, calories_ai_included=1)
        assert f"Калории: ≈ 2500 из 1900 ккал{AI_TAIL}." in text
        assert "больше ориентира" not in text

    def test_twin_the_same_overshoot_verified_is_named(self) -> None:
        text = _report(calories_total=2500.0)
        assert "Калорий вышло на 600 ккал больше ориентира из профиля" in text

    def test_an_incomplete_total_says_so_and_carries_no_remark(self) -> None:
        text = _report(calories_total=1500.0, calories_unscored=1)
        assert "Калории: 1500 из 1900 ккал." in text
        assert INCOMPLETE in text
        assert "осталось" not in text

    def test_no_total_at_all_is_not_zero(self) -> None:
        text = _report(calories_total=None, calories_unscored=2)
        assert UNKNOWN in text
        assert "Калории: 0" not in text
        assert "осталось" not in text
        # Записи есть: это не «записей не было».
        assert "Сегодня записей не было" not in text

    def test_numbers_hidden_hides_the_approximation_and_the_mark_too(self) -> None:
        text = render.render_daily_report(
            fx.summary(entries=[{"id": 1}], calories_total=1500.0, calories_ai_included=1),
            fx.water(),
            fx.profile(),
            hide_numbers=True,
        )
        assert "Записей в дневнике сегодня: 1." in text
        assert "≈" not in text
        assert AI_TAIL not in text
        assert INCOMPLETE not in text


class TestTheClientReadsTheWire:
    """Разбор ответа каталога: «итога нет» остаётся отсутствием."""

    @staticmethod
    def _parse(body: dict) -> nc.SummaryResponse:
        client = nc.NutritionClient(base_url="https://ayla.test", service_token="t")
        resp = httpx.Response(200, json={"data": body})
        return client._parse_summary_response(
            resp, external_user_id="bot:max:2766", purpose=nc.BreakerPurpose.NUTRITION
        )

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(None, None), (0, 0.0), (447.0, 447.0), ("447", 447.0), (True, None), ("x", None)],
    )
    def test_the_total(self, raw, expected) -> None:
        assert self._parse({"date": "2026-09-16", "calories_total": raw}).calories_total == expected

    def test_the_counters(self) -> None:
        parsed = self._parse(
            {"calories_total": 447.0, "calories_ai_included": 1, "calories_unscored": 2}
        )
        assert (parsed.calories_ai_included, parsed.calories_unscored) == (1, 2)

    @pytest.mark.parametrize("raw", [None, -1, "x", True])
    def test_a_missing_or_garbage_counter_is_zero(self, raw) -> None:
        parsed = self._parse({"calories_total": 10.0, "calories_ai_included": raw})
        assert parsed.calories_total == 10.0
        assert parsed.calories_ai_included == 0
