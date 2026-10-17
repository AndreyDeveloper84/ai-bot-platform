"""DRF-2761 — оценка калорий ИИ на поверхностях дневника: дошла и помечена.

Владелец (02.10.2026) назвал поверхности отдельно: «Оценка ИИ» показывается не
только в карточке и подтверждении, но и **в дневнике, на экранах дня, в Mini
App**. Проверенное число рисуется как раньше; оценка — с пометкой и в итог
дня не входит (её не суммирует каталог: у такой записи ``calories`` пуст).

* s1 — общий читатель записей дня (``food_history.meals_from_summary``) несёт
  оценку отдельным полем и только когда проверенного числа нет;
* s2 — отчёт за день в чате: строка записи с оценкой помечена «Оценка ИИ»,
  проверенная — как раньше, запись без чисел — одно название;
* s3 — провод бота к Mini App: оценка едет своим ключом в оценке блюда и в
  ответе записи; ответ каталога без нового поля — оценки нет (не ошибка).
"""

# ruff: noqa: F811 -- fixtures are imported by name from the neighbouring test module
from __future__ import annotations

from dataclasses import dataclass, replace

from apps.integrations.ayla import DishEstimate, FoodLogResponse
from apps.miniapp_api.tests.test_food_text_2091 import (  # noqa: F401 — фикстуры
    LOG_BODY,
    _bot_token,
    _diary_on,
    _patch_client,
    _post,
    bot_user,
    diary_consent,
    personal_consent,
    tenant,
)
from apps.nutrition_proactive.render import _entry_lines
from apps.nutrition_proactive.tests.test_render import summary
from apps.orchestrator.food_history import meals_from_summary

VERIFIED = {"dish_name": "Борщ", "calories": 150.0, "ai_calories": None, "meal_type": "lunch"}
ESTIMATED = {"dish_name": "Зыбзик", "calories": None, "ai_calories": 750.0, "meal_type": "lunch"}
NUMBERLESS = {"dish_name": "Чай", "calories": None, "ai_calories": None, "meal_type": "snack"}


class TestTheSharedReader:
    def test_s1_the_estimate_rides_in_its_own_field(self) -> None:
        meals = meals_from_summary(summary(entries=[VERIFIED, ESTIMATED, NUMBERLESS]))

        assert [(m.dish, m.calories, m.ai_calories) for m in meals] == [
            ("Борщ", 150, 0),
            ("Зыбзик", 0, 750),
            ("Чай", 0, 0),
        ]

    def test_s1_a_verified_number_beats_the_estimate(self) -> None:
        both = {**VERIFIED, "ai_calories": 999.0}

        (meal,) = meals_from_summary(summary(entries=[both]))

        assert (meal.calories, meal.ai_calories) == (150, 0)

    def test_s1_junk_in_the_estimate_field_is_not_a_number(self) -> None:
        rows = [{**ESTIMATED, "ai_calories": junk} for junk in (True, "750", -5, None, [750])]

        meals = meals_from_summary(summary(entries=rows))

        # Положительная пара: строки прочитаны, блюда на месте.
        assert [m.dish for m in meals] == ["Зыбзик"] * 5
        assert [m.ai_calories for m in meals] == [0, 0, 0, 0, 0]

    def test_s1_a_row_from_an_older_catalog_has_no_estimate(self) -> None:
        old = {"dish_name": "Зыбзик", "calories": None, "meal_type": "lunch"}

        (meal,) = meals_from_summary(summary(entries=[old]))

        assert (meal.dish, meal.calories, meal.ai_calories) == ("Зыбзик", 0, 0)


class TestTheChatDayReport:
    def test_s2_the_estimated_entry_is_marked_and_the_verified_one_is_not(self) -> None:
        lines = _entry_lines(summary(entries=[VERIFIED, ESTIMATED, NUMBERLESS]))

        assert lines == [
            "Что было записано:",
            "• Борщ — 150 ккал",
            "• Зыбзик — ≈ 750 ккал · Оценка ИИ",
            "• Чай",
        ]


@dataclass
class _Estimate:
    matched_dish: str = "зыбзик"
    portion_g: float = 300.0
    portion_estimated: bool = False
    kcal: float | None = None
    protein_g: float | None = None
    fat_g: float | None = None
    carbs_g: float | None = None


class TestTheWireToTheMiniApp:
    def test_s3_the_estimate_reaches_the_mini_app_under_its_own_key(self, client, bot_user):
        estimate = DishEstimate(
            matched_dish="зыбзик",
            portion_g=300.0,
            portion_estimated=False,
            kcal=None,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            raw={},
            kcal_ai_estimate=750.0,
        )
        patcher, _ = _patch_client(estimate=estimate)
        with patcher:
            resp = _post(client, bot_user, "customer_food_estimate", {"text": "зыбзик 300"})

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["matched_dish"] == "зыбзик"
        assert body["kcal_ai_estimate"] == 750.0
        # Проверенного числа нет: оценка его не подменяет.
        assert body["kcal"] is None

    def test_s3_a_reference_estimate_carries_no_ai_number(self, client, bot_user):
        estimate = DishEstimate(
            matched_dish="борщ",
            portion_g=250.0,
            portion_estimated=False,
            kcal=120.0,
            protein_g=5.0,
            fat_g=4.0,
            carbs_g=12.0,
            raw={},
        )
        patcher, _ = _patch_client(estimate=estimate)
        with patcher:
            body = _post(client, bot_user, "customer_food_estimate", {"text": "борщ 250"}).json()

        assert body["kcal"] == 120.0
        assert body["kcal_ai_estimate"] is None

    def test_s3_an_answer_without_the_new_field_is_not_an_error(self, client, bot_user):
        """Двойник без поля — как ответ клиента до листа: оценки нет, 200."""
        patcher, _ = _patch_client(estimate=_Estimate())
        with patcher:
            resp = _post(client, bot_user, "customer_food_estimate", {"text": "зыбзик 300"})

        assert resp.status_code == 200, resp.content
        assert resp.json()["matched_dish"] == "зыбзик"
        assert resp.json()["kcal_ai_estimate"] is None

    def test_s3_the_logged_entry_carries_the_estimate_under_its_own_key(self, client, bot_user):
        log = FoodLogResponse(
            log_id="01J9FOODTEXT000000000000AA",
            dish_name="зыбзик",
            meal_type="other",
            calories=None,
            raw={},
            ai_calories=750.0,
        )
        patcher, _ = _patch_client(log=log)
        with patcher:
            resp = _post(client, bot_user, "customer_food_log", {**LOG_BODY, "dish_name": "зыбзик"})

        assert resp.status_code == 201, resp.content
        body = resp.json()
        assert body["dish_name"] == "зыбзик"
        assert body["ai_calories"] == 750.0
        assert body["calories"] is None

    def test_s3_a_verified_log_carries_no_ai_number(self, client, bot_user):
        log = replace(
            FoodLogResponse(
                log_id="01J9FOODTEXT000000000000AA",
                dish_name="борщ",
                meal_type="other",
                calories=300.0,
                raw={},
            )
        )
        patcher, _ = _patch_client(log=log)
        with patcher:
            body = _post(client, bot_user, "customer_food_log", LOG_BODY).json()

        assert body["calories"] == 300.0
        assert body["ai_calories"] is None
