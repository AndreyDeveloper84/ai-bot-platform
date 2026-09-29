"""DRF-2099 — ``diary_days``: провод к ``internal/diary/days/`` и отказы.

Предмет — как ответ каталога становится строками дней: одна строка на
день как отдал сервер (пустые дни присутствуют), пояс и период — с
провода; 5xx / сеть — «дневник не отвечает», 400 — отказ по имени; тело
не логируется.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import (
    DiaryDayRow,
    DiaryDaysResponse,
    NutritionAPIError,
    NutritionUnavailableError,
    get_nutrition_client,
    reset_nutrition_client,
)

EXT = "bot:max:2099"
WIRE = {
    "timezone": "Europe/Moscow",
    "from": "2026-09-14",
    "to": "2026-09-20",
    "days": [
        {"date": "2026-09-14", "meals_count": 0, "kcal": None, "has_entries": False},
        {"date": "2026-09-15", "meals_count": 2, "kcal": 500.0, "has_entries": True},
    ],
}


@pytest.fixture
def ayla(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any], None, None]:
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.NUTRITION_SERVICE_TOKEN = "svc-token-2099"  # noqa: S105 — test sentinel
    state: dict[str, Any] = {"status": 200, "json": {}, "seen": []}
    real_async = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(
            (request.method, request.url.path, dict(request.url.params), dict(request.headers))
        )
        if state.get("raise") is not None:
            raise state["raise"]
        return httpx.Response(state["status"], json=state["json"])

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    reset_nutrition_client()
    yield state
    reset_nutrition_client()


def _call(**kwargs):
    return asyncio.run(get_nutrition_client().diary_days(external_user_id=EXT, **kwargs))


class TestWire:
    def test_get_under_both_subject_headers_with_the_period(self, ayla: dict[str, Any]) -> None:
        ayla["json"] = {"data": WIRE}

        res = _call(date_from="2026-09-14", date_to="2026-09-20")

        method, path, params, headers = ayla["seen"][0]
        assert (method, path) == ("GET", "/api/v1/nutrition/internal/diary/days/")
        assert params == {"from": "2026-09-14", "to": "2026-09-20"}
        assert headers["x-service-token"] == "svc-token-2099"
        assert headers["x-external-user-id"] == EXT
        assert res == DiaryDaysResponse(
            timezone="Europe/Moscow",
            date_from="2026-09-14",
            date_to="2026-09-20",
            days=(
                DiaryDayRow("2026-09-14", 0, None, False),
                DiaryDayRow("2026-09-15", 2, 500.0, True),
            ),
        )

    def test_without_a_period_sends_no_params(self, ayla: dict[str, Any]) -> None:
        ayla["json"] = {"data": WIRE}
        _call()
        assert ayla["seen"][0][2] == {}


class TestRefusals:
    @pytest.mark.parametrize("status", [500, 503])
    def test_5xx_is_unavailable(self, ayla: dict[str, Any], status: int) -> None:
        ayla["status"] = status
        with pytest.raises(NutritionUnavailableError):
            _call()

    def test_400_is_a_named_refusal_not_an_empty_week(self, ayla: dict[str, Any]) -> None:
        ayla["status"] = 400
        ayla["json"] = {"error": {"code": "VALIDATION_ERROR", "message": "period"}}
        with pytest.raises(NutritionAPIError):
            _call(date_from="2026-08-01", date_to="2026-09-01")

    def test_a_malformed_body_is_not_an_empty_week(self, ayla: dict[str, Any]) -> None:
        ayla["json"] = {"data": {"days": "nope"}}
        with pytest.raises(NutritionUnavailableError):
            _call()
