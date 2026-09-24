"""DRF-2371 — «не посчитано» не равно нулю: клиент питания.

Каталог после DRF-2371 сохраняет запись и тогда, когда числа вывести
неоткуда: порция неизвестна, блюда нет в справочнике. В ответе на месте
калорий стоит ``null`` — и это **не ноль**: ноль означает «съел и не
получил калорий», а здесь мы просто не знаем.

Клиент бота превращал отсутствие в ноль шаблоном ``float(... or 0.0)``.
Дальше вниз по пути ноль уже неотличим от посчитанного, и человек читал
«Записала: борщ — 0 ккал» о блюде, которого никто не считал.

Узлы ниже держат границу: отсутствие остаётся отсутствием.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc


def _client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[nc.NutritionClient, httpx.MockTransport]:
    transport = httpx.MockTransport(handler)
    return (
        nc.NutritionClient(base_url="https://ayla.test", service_token="t"),
        transport,
    )


@pytest.fixture(autouse=True)
def _patch_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        transport = getattr(_patch_async_client, "_TRANSPORT", None)
        if transport is None:
            return original(*args, **kwargs)
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


def _set_transport(transport: httpx.MockTransport) -> None:
    _patch_async_client._TRANSPORT = transport  # type: ignore[attr-defined]


class TestLogMealKeepsAbsence:
    """``POST internal/food-log`` — запись есть, числа нет."""

    @pytest.mark.asyncio
    async def test_null_calories_stay_none(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "log-1",
                        "dish_name": "ризотто с трюфелем",
                        "meal_type": "dinner",
                        "calories": None,
                    }
                },
            )

        client, transport = _client_with_handler(handler)
        _set_transport(transport)

        result = await client.log_meal(
            external_user_id="bot:1", dish_name="ризотто с трюфелем", meal_type="dinner"
        )
        # Утверждение о наличии — до утверждения об отсутствии: запись легла.
        assert result.log_id == "log-1"
        assert result.dish_name == "ризотто с трюфелем"
        assert result.calories is None

    @pytest.mark.asyncio
    async def test_real_zero_is_not_absence(self) -> None:
        """Положительная пара: настоящий ноль каталога остаётся нулём.

        Иначе «отсутствие» стало бы затычкой для любого ложного значения —
        и узел выше проходил бы, ничего не доказывая.
        """

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": "log-2",
                        "dish_name": "вода",
                        "meal_type": "other",
                        "calories": 0.0,
                    }
                },
            )

        client, transport = _client_with_handler(handler)
        _set_transport(transport)

        result = await client.log_meal(
            external_user_id="bot:1", dish_name="вода", meal_type="other"
        )
        assert result.calories == 0.0


class TestEstimateKeepsAbsence:
    """``POST internal/food-estimate`` — блюдо названо, числа нет."""

    @pytest.mark.asyncio
    async def test_null_kcal_stays_none(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "matched_dish": "ризотто с трюфелем",
                        "portion_g": 250,
                        "portion_estimated": True,
                        "kcal": None,
                        "protein_g": None,
                        "fat_g": None,
                        "carbs_g": None,
                    }
                },
            )

        client, transport = _client_with_handler(handler)
        _set_transport(transport)

        result = await client.estimate_dish(
            external_user_id="bot:1", dish_name="ризотто с трюфелем"
        )
        assert result.matched_dish == "ризотто с трюфелем"
        assert result.portion_g == 250
        assert result.kcal is None
