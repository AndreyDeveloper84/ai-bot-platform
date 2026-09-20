"""Отказы по бюджету распознавания — ожидаемые, не сбой (DRF-2195, Сканер-1b).

Каталог (#519, DRF-2145) отвечает на скан фото двумя ШТАТНЫМИ отказами:

* ``429 FOOD_SCAN_DAILY_LIMIT`` — личный потолок человека на сутки (с
  ``retry_after`` до полуночи UTC);
* ``503 FOOD_SCAN_BUDGET_EXHAUSTED`` — общий дневной потолок.

Оба — ответ системы, которая работает, а не признак того, что каталог лёг.

# Класс дыры, ради которого этот файл

``_parse_scan_response`` считает failure по СТАТУСУ: ``>= 500`` → breaker.
Пока 503 бюджета попадает в эту ветку, каждый исчерпанный день кормит общий
предохранитель клиента питания — и после пяти отказов подряд ВЫКЛЮЧАЕТСЯ ВЕСЬ
контур: запись еды текстом, дневник, сводка, ориентиры. То есть штатный
«на сегодня хватит фото» гасит функции, к фото отношения не имеющие. Ровно
это уже случалось с постоянным 503 у suggest и alerting (DRF-2130/2158),
поэтому узел «breaker не открылся» здесь обязателен и стоит первым.

Ожидаемые коды читаются ДО общей развилки по статусу — иначе порядок ветвей
и есть дефект.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc
from apps.integrations.ayla.tests.test_nutrition_client import (  # переиспользуем стенд
    _client_with_handler,
    _set_transport,
)

DAILY_LIMIT_BODY: dict[str, Any] = {
    "error": {
        "code": "FOOD_SCAN_DAILY_LIMIT",
        "message": "daily scan limit reached",
        "details": {"retry_after": 3600},
    }
}
BUDGET_BODY: dict[str, Any] = {
    "error": {"code": "FOOD_SCAN_BUDGET_EXHAUSTED", "message": "daily budget exhausted"}
}


def _responder(status: int, body: dict[str, Any]):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


class TestBudgetRefusalsAreNotFailures:
    @pytest.mark.asyncio
    async def test_daily_limit_429_raises_its_own_error(self) -> None:
        client, transport = _client_with_handler(_responder(429, DAILY_LIMIT_BODY))
        _set_transport(transport)

        with pytest.raises(nc.ScanDailyLimitError) as exc:
            await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")

        assert exc.value.retry_after == 3600

    @pytest.mark.asyncio
    async def test_budget_503_raises_its_own_error_not_unavailable(self) -> None:
        client, transport = _client_with_handler(_responder(503, BUDGET_BODY))
        _set_transport(transport)

        with pytest.raises(nc.ScanBudgetExhaustedError):
            await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")

    @pytest.mark.asyncio
    async def test_repeated_budget_503_does_not_open_the_breaker(self) -> None:
        """Сердце листа: исчерпанный бюджет не гасит запись еды и дневник."""
        client, transport = _client_with_handler(_responder(503, BUDGET_BODY))
        _set_transport(transport)

        for _ in range(6):  # порог breaker — 5 отказов за 60 с
            with pytest.raises(nc.ScanBudgetExhaustedError):
                await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")

        assert client._circuit.is_open(now=time.monotonic()) is False

    @pytest.mark.asyncio
    async def test_repeated_daily_limit_429_does_not_open_the_breaker(self) -> None:
        client, transport = _client_with_handler(_responder(429, DAILY_LIMIT_BODY))
        _set_transport(transport)

        for _ in range(6):
            with pytest.raises(nc.ScanDailyLimitError):
                await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")

        assert client._circuit.is_open(now=time.monotonic()) is False

    @pytest.mark.asyncio
    async def test_positive_pair_real_5xx_still_counts_as_failure(self) -> None:
        """Положительная пара: настоящий 503 без кода бюджета — по-прежнему сбой."""
        client, transport = _client_with_handler(_responder(503, {"error": {"code": "BOOM"}}))
        _set_transport(transport)

        for _ in range(5):
            with pytest.raises(nc.NutritionUnavailableError):
                await client.scan_photo(external_user_id="bot:1", image_bytes=b"...")

        assert client._circuit.is_open(now=time.monotonic()) is True

    @pytest.mark.asyncio
    async def test_budget_errors_are_not_unavailable_subclasses(self) -> None:
        """Лестница навыка ловит `NutritionUnavailableError` — отказы бюджета
        не должны в неё попадать, иначе человек увидит «попробуй через минуту»
        вместо «напиши словами»."""
        assert not issubclass(nc.ScanDailyLimitError, nc.NutritionUnavailableError)
        assert not issubclass(nc.ScanBudgetExhaustedError, nc.NutritionUnavailableError)
        assert issubclass(nc.ScanDailyLimitError, nc.NutritionAPIError)
        assert issubclass(nc.ScanBudgetExhaustedError, nc.NutritionAPIError)
