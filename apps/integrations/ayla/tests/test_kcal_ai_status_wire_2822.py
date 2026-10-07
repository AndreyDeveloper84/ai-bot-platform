"""DRF-2822 — ``kcal_ai_status`` на проводе ``internal/food-estimate/``.

Каталог называет, почему числа ИИ нет; клиент доносит знакомое значение и
молчит о незнакомом (карточка тогда говорит нейтрально).

Тела здесь СИНТЕТИЧЕСКИЕ — по согласованному контракту из листа: каталог поле
ещё не выложил. Живую фикстуру ``food_estimate_ai_live_2761.json`` этот лист
не трогает; её переснимают с настоящего ответа после выкладки каталога.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc

CONTRACT = {
    "estimated",
    "unavailable",
    "disabled",
    "not_attempted",
    "not_permitted",
    "not_applicable",
    "declined",
}


@pytest.fixture(autouse=True)
def _transport(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    original = httpx.AsyncClient
    holder: dict[str, Any] = {"transport": None}

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if holder["transport"] is not None:
            kwargs["transport"] = holder["transport"]
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    yield holder
    holder["transport"] = None


def _body(**over: Any) -> dict[str, Any]:
    data = {
        "matched_dish": "зыбзик квантовый",
        "portion_g": 300.0,
        "portion_estimated": False,
        "portion_source": "unknown",
        "kcal": None,
        "kcal_per_100g": None,
        "kcal_ai_estimate": None,
        "protein_g": None,
        "fat_g": None,
        "carbs_g": None,
    }
    data.update(over)
    return {"data": data}


async def _estimate(holder: dict[str, Any], body: dict[str, Any]) -> nc.DishEstimate:
    holder["transport"] = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    client = nc.NutritionClient(base_url="https://ayla.test", service_token="t")
    return await client.estimate_dish(
        external_user_id="bot:max:2822", dish_name="зыбзик квантовый", portion_g=300
    )


def test_the_bot_knows_exactly_the_agreed_statuses() -> None:
    assert nc.KCAL_AI_STATUSES == CONTRACT


@pytest.mark.asyncio
@pytest.mark.parametrize("status", sorted(CONTRACT))
async def test_a_known_status_rides_through(_transport, status: str) -> None:
    result = await _estimate(_transport, _body(kcal_ai_status=status))

    assert result.matched_dish == "зыбзик квантовый"
    assert result.kcal_ai_status == status


@pytest.mark.asyncio
@pytest.mark.parametrize("junk", ["unparsed", "UNAVAILABLE", "", None, 1, True, ["disabled"]])
async def test_an_unknown_value_is_not_a_reason(_transport, junk: Any) -> None:
    result = await _estimate(_transport, _body(kcal_ai_status=junk))

    # Положительная пара: ответ разобран, блюдо на месте.
    assert result.matched_dish == "зыбзик квантовый"
    assert result.kcal_ai_status is None


@pytest.mark.asyncio
async def test_a_catalog_from_before_the_field_gives_no_reason(_transport) -> None:
    result = await _estimate(_transport, _body())

    assert result.matched_dish == "зыбзик квантовый"
    assert result.kcal_ai_status is None
