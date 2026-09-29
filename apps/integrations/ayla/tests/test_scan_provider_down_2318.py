"""Стойкий отказ распознавателя фото — не «временно недоступно» (DRF-2318).

Каталог (#549) на стойкий отказ провайдера (счёт не активен, ключ отвергнут,
квота исчерпана, ключ не задан) отвечает прежним 503 ``FOOD_API_UNAVAILABLE``,
но с ``details: {"permanent": true, "reason": "<слово>"}``. Клиент поднимает
своё исключение — не наследник :class:`NutritionUnavailableError` (иначе
навык сказал бы «попробуй через минуту») и не кормит общий предохранитель
питания: неоплаченный распознаватель не должен гасить дневник и запись текстом.

* c1 — стойкий 503 → ``ScanProviderDownError`` с причиной;
* c2 — повторы не открывают предохранитель;
* c3 — положительная пара: 503 без ``permanent`` — по-прежнему недоступность.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc
from apps.integrations.ayla.tests.test_nutrition_client import (  # переиспользуем стенд
    _client_with_handler,
    _patch_async_client,  # noqa: F401 — autouse: httpx.AsyncClient на мок-транспорт
    _set_transport,
)

PERMANENT_BODY: dict[str, Any] = {
    "error": {
        "code": "FOOD_API_UNAVAILABLE",
        "message": "Сервис распознавания временно недоступен",
        "details": {"permanent": True, "reason": "billing_not_active"},
    }
}
TEMPORARY_BODY: dict[str, Any] = {
    "error": {"code": "FOOD_API_UNAVAILABLE", "message": "Сервис распознавания временно недоступен"}
}


def _responder(status: int, body: dict[str, Any]):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


class TestC1PermanentIsItsOwnError:
    @pytest.mark.asyncio
    async def test_permanent_503_raises_provider_down(self) -> None:
        client, transport = _client_with_handler(_responder(503, PERMANENT_BODY))
        _set_transport(transport)
        with pytest.raises(nc.ScanProviderDownError) as exc:
            await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")
        assert isinstance(exc.value, nc.ScanProviderDownError)  # наличие: своя ошибка
        assert exc.value.reason == "billing_not_active"
        assert not isinstance(exc.value, nc.NutritionUnavailableError)

    @pytest.mark.asyncio
    async def test_an_unknown_reason_is_not_carried_verbatim(self) -> None:
        body = {
            "error": {**PERMANENT_BODY["error"], "details": {"permanent": True, "reason": "x y!"}}
        }
        client, transport = _client_with_handler(_responder(503, body))
        _set_transport(transport)
        with pytest.raises(nc.ScanProviderDownError) as exc:
            await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")
        assert exc.value.reason == "unknown"


class TestC2TheBreakerStaysClosed:
    @pytest.mark.asyncio
    async def test_repeated_permanent_503_does_not_open_the_breaker(self) -> None:
        client, transport = _client_with_handler(_responder(503, PERMANENT_BODY))
        _set_transport(transport)
        for _ in range(6):  # порог breaker — 5 отказов за 60 с
            with pytest.raises(nc.ScanProviderDownError):
                await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")
        assert client._circuit.is_open(now=time.monotonic()) is False


class TestC3PositivePairTemporaryStaysUnavailable:
    @pytest.mark.asyncio
    async def test_503_without_permanent_is_unavailability(self) -> None:
        client, transport = _client_with_handler(_responder(503, TEMPORARY_BODY))
        _set_transport(transport)
        for _ in range(5):
            with pytest.raises(nc.NutritionUnavailableError):
                await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")
        assert client._circuit.is_open(now=time.monotonic()) is True
