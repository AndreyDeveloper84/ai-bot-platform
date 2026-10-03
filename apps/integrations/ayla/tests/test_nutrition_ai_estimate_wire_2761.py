"""DRF-2761 — клиент питания читает оценку калорий ИИ с живого провода.

Ответы — из ``fixtures/food_estimate_ai_live_2761.json``: сняты с настоящих
ручек каталога (``internal/food-estimate``, ``internal/food-log``), а не
написаны от руки. Обе стороны данных, построенные одним автором, сходятся
друг с другом, а не с каталогом.

* w1 — оценка блюда: число ИИ своим полем, проверенного ``kcal`` нет;
* w2 — блюдо из справочника: проверенное ``kcal`` есть, оценки ИИ нет;
* w3 — запись: оценка своим полем, ``calories`` пуст;
* w4 — ответ каталога без нового поля (до листа) — оценки нет, не ошибка.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc

_LIVE = json.loads(
    (Path(__file__).parent / "fixtures" / "food_estimate_ai_live_2761.json").read_text(
        encoding="utf-8"
    )
)["responses"]


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    original = httpx.AsyncClient
    holder: dict[str, httpx.MockTransport | None] = {"transport": None}

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if holder["transport"] is not None:
            kwargs["transport"] = holder["transport"]
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    _patch_async_client.holder = holder  # type: ignore[attr-defined]
    yield
    holder["transport"] = None


def _answering(status: int, body: dict[str, Any]) -> nc.NutritionClient:
    _patch_async_client.holder["transport"] = httpx.MockTransport(  # type: ignore[attr-defined]
        lambda request: httpx.Response(status, json=body)
    )
    return nc.NutritionClient(base_url="https://ayla.test", service_token="t")


class TestTheLiveWire:
    @pytest.mark.asyncio
    async def test_w1_the_estimate_of_a_dish_rides_in_its_own_field(self) -> None:
        live = _LIVE["estimate_ai"]
        client = _answering(live["status"], live["body"])

        result = await client.estimate_dish(
            external_user_id="bot:max:2761", dish_name="зыбзик квантовый", portion_g=300
        )

        assert result.matched_dish == "зыбзик квантовый"
        assert result.kcal_ai_estimate == 750.0
        assert result.kcal is None
        assert (result.protein_g, result.fat_g, result.carbs_g) == (None, None, None)
        assert result.raw["source"] == "ai_estimate"

    @pytest.mark.asyncio
    async def test_w2_a_reference_dish_carries_no_ai_number(self) -> None:
        live = _LIVE["estimate_reference"]
        client = _answering(live["status"], live["body"])

        result = await client.estimate_dish(
            external_user_id="bot:max:2761", dish_name="борщ", portion_g=300
        )

        assert result.kcal == 147.0
        assert result.kcal_ai_estimate is None
        assert result.raw["source"] == "seed_ru"

    @pytest.mark.asyncio
    async def test_w3_the_logged_entry_carries_the_estimate_in_its_own_field(self) -> None:
        live = _LIVE["log_ai"]
        client = _answering(live["status"], live["body"])

        result = await client.log_meal(
            external_user_id="bot:max:2761",
            dish_name="зыбзик квантовый",
            meal_type="other",
            portion_multiplier=3.0,
        )

        assert result.dish_name == "зыбзик квантовый"
        assert result.ai_calories == 750.0
        assert result.calories is None

    @pytest.mark.asyncio
    async def test_w4_an_answer_from_before_the_feature_has_no_estimate(self) -> None:
        body = json.loads(json.dumps(_LIVE["estimate_ai"]["body"]))
        del body["data"]["kcal_ai_estimate"]
        client = _answering(200, body)

        result = await client.estimate_dish(
            external_user_id="bot:max:2761", dish_name="зыбзик квантовый", portion_g=300
        )

        assert result.matched_dish == "зыбзик квантовый"
        assert result.kcal_ai_estimate is None
