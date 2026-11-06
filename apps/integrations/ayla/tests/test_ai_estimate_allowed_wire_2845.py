"""DRF-2845 — ``ai_estimate_allowed`` на проводе к каталогу.

Согласие на ИИ-оценку лежит в боте, модель зовёт каталог. Единственный способ
донести решение — поле в теле запроса, и ехать оно обязано ВСЕГДА: каталог
читает отсутствие поля как «не разрешено», и молчащий бот выключил бы оценку
всем.

Тело ответа здесь синтетическое: предмет узлов — что бот ОТПРАВИЛ.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc

ESTIMATE_ANSWER = {
    "data": {
        "matched_dish": "зыбзик",
        "portion_g": 300.0,
        "portion_estimated": False,
        "kcal": None,
        "kcal_ai_estimate": None,
    }
}
LOG_ANSWER = {"data": {"id": "log-2845", "dish_name": "зыбзик", "meal_type": "other"}}


@pytest.fixture(autouse=True)
def _sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Тела запросов, ушедших каталогу."""
    original = httpx.AsyncClient
    bodies: list[dict[str, Any]] = []

    def _answer(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        answer = ESTIMATE_ANSWER if request.url.path.endswith("food-estimate/") else LOG_ANSWER
        return httpx.Response(200, json=answer)

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_answer)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    yield bodies


def _client() -> nc.NutritionClient:
    return nc.NutritionClient(base_url="https://ayla.test", service_token="t")


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_the_estimate_carries_the_decision(_sent, allowed: bool) -> None:
    result = await _client().estimate_dish(
        external_user_id="bot:max:2845",
        dish_name="зыбзик",
        portion_g=300,
        ai_estimate_allowed=allowed,
    )

    assert result.matched_dish == "зыбзик"
    assert _sent == [{"dish_name": "зыбзик", "portion_g": 300, "ai_estimate_allowed": allowed}]


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [True, False])
async def test_the_log_carries_the_decision(_sent, allowed: bool) -> None:
    result = await _client().log_meal(
        external_user_id="bot:max:2845",
        dish_name="зыбзик",
        meal_type="other",
        ai_estimate_allowed=allowed,
    )

    assert result.log_id == "log-2845"
    (body,) = _sent
    assert body["dish_name"] == "зыбзик"
    assert body["ai_estimate_allowed"] is allowed


@pytest.mark.asyncio
async def test_the_estimate_cannot_be_called_without_deciding() -> None:
    with pytest.raises(TypeError, match="ai_estimate_allowed"):
        await _client().estimate_dish(  # type: ignore[call-arg]
            external_user_id="bot:max:2845", dish_name="зыбзик"
        )


@pytest.mark.asyncio
async def test_the_log_cannot_be_called_without_deciding() -> None:
    with pytest.raises(TypeError, match="ai_estimate_allowed"):
        await _client().log_meal(  # type: ignore[call-arg]
            external_user_id="bot:max:2845", dish_name="зыбзик", meal_type="other"
        )
