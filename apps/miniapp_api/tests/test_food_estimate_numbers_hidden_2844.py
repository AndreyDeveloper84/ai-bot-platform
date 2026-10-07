"""DRF-2844 — «Записать текстом» в Mini App соблюдает режим «Без чисел».

Решение владельца 04.10: добровольное скрытие калорий, БЖУ и числовых целей —
на ВСЕХ экранах. Экран текстового ввода режима не знает и рисует всё, что
пришло от ``food/estimate``; до этого листа ручка отдавала числа независимо от
выбора человека. Теперь в режиме они не едут: блюдо и порция остаются.

Режим здесь НАСТОЯЩИЙ — выбор записан через ``write_prefs``, а не подменён
предикат: подменённый доказал бы только, что ручка его зовёт.

* h1 — блюдо из справочника: по умолчанию числа есть; в режиме — калорий и БЖУ
  нет, блюдо и порция на месте;
* h2 — блюдо с оценкой ИИ: по умолчанию оценка есть; в режиме — нет;
* h3 — причина отсутствия оценки в режиме не едет (как с DRF-2822);
* h4 — запись из экрана режимом не меняется: уходит блюдо и граммы.
"""

# ruff: noqa: F811 -- fixtures are imported by name from the neighbouring test module
from __future__ import annotations

import pytest

from apps.identity.models import BotUser
from apps.integrations.ayla import DishEstimate
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
from apps.nutrition_proactive.prefs import numbers_hidden_for, write_prefs

NUMBER_KEYS = ("kcal", "protein_g", "fat_g", "carbs_g", "kcal_ai_estimate", "kcal_ai_status")

FROM_THE_REFERENCE = DishEstimate(
    matched_dish="борщ",
    portion_g=250.0,
    portion_estimated=False,
    kcal=120.0,
    protein_g=5.0,
    fat_g=4.0,
    carbs_g=12.0,
    raw={},
    kcal_ai_status="not_attempted",
)
ESTIMATED_BY_AI = DishEstimate(
    matched_dish="зыбзик",
    portion_g=300.0,
    portion_estimated=False,
    kcal=None,
    protein_g=None,
    fat_g=None,
    carbs_g=None,
    raw={},
    kcal_ai_estimate=750.0,
    kcal_ai_status="estimated",
)
NOT_ESTIMATED = DishEstimate(
    matched_dish="зыбзик",
    portion_g=300.0,
    portion_estimated=False,
    kcal=None,
    protein_g=None,
    fat_g=None,
    carbs_g=None,
    raw={},
    kcal_ai_status="unavailable",
)


@pytest.fixture
def hidden(bot_user):
    """Человек сам выбрал «Без чисел» — настоящей записью выбора.

    Настройки питания есть только у связанной оболочки (§2.4): у несвязанной
    выбора нет вовсе, и числа видны.
    """
    bot_user.customer_status = BotUser.CustomerStatus.LINKED
    bot_user.save(update_fields=["customer_status"])
    write_prefs(bot_user, {"numbers_hidden": True})
    assert numbers_hidden_for(bot_user) is True


def _estimate(client, bot_user, estimate: DishEstimate) -> dict:
    patcher, _ = _patch_client(estimate=estimate)
    with patcher:
        resp = _post(client, bot_user, "customer_food_estimate", {"text": "блюдо 250"})
    assert resp.status_code == 200, resp.content
    return resp.json()


class TestTheReferenceDish:
    def test_h1_by_default_the_numbers_are_there(self, client, bot_user) -> None:
        body = _estimate(client, bot_user, FROM_THE_REFERENCE)

        assert (body["kcal"], body["protein_g"], body["fat_g"], body["carbs_g"]) == (
            120.0,
            5.0,
            4.0,
            12.0,
        )

    def test_h1_numbers_hidden_keeps_the_dish_and_the_portion_only(
        self, client, bot_user, hidden
    ) -> None:
        body = _estimate(client, bot_user, FROM_THE_REFERENCE)

        assert body == {
            "matched_dish": "борщ",
            "portion_g": 250.0,
            "portion_estimated": False,
            **dict.fromkeys(NUMBER_KEYS),
        }


class TestTheAiEstimate:
    def test_h2_by_default_the_estimate_is_there(self, client, bot_user) -> None:
        body = _estimate(client, bot_user, ESTIMATED_BY_AI)

        assert body["kcal_ai_estimate"] == 750.0

    def test_h2_numbers_hidden_carries_no_estimate(self, client, bot_user, hidden) -> None:
        body = _estimate(client, bot_user, ESTIMATED_BY_AI)

        assert body == {
            "matched_dish": "зыбзик",
            "portion_g": 300.0,
            "portion_estimated": False,
            **dict.fromkeys(NUMBER_KEYS),
        }

    def test_h3_by_default_the_reason_is_there(self, client, bot_user) -> None:
        assert _estimate(client, bot_user, NOT_ESTIMATED)["kcal_ai_status"] == "unavailable"

    def test_h3_numbers_hidden_carries_no_reason(self, client, bot_user, hidden) -> None:
        body = _estimate(client, bot_user, NOT_ESTIMATED)

        assert body["matched_dish"] == "зыбзик"
        assert body["kcal_ai_status"] is None


class TestTheLogIsUntouched:
    def test_h4_the_entry_goes_with_the_dish_and_the_grams(self, client, bot_user, hidden) -> None:
        patcher, catalog = _patch_client()
        with patcher:
            resp = _post(client, bot_user, "customer_food_log", LOG_BODY)

        assert resp.status_code == 201, resp.content
        sent = catalog.log_meal.await_args.kwargs
        assert sent["dish_name"] == "борщ"
        assert sent["portion_multiplier"] == 2.5
