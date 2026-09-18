"""DRF-2092 — ``list_saved_meals`` / ``save_meal`` / ``delete_saved_meal``: провод и отказы.

Предмет — как HTTP-ответ каталога (``internal/saved-meals/``, beautygo_backend#505)
становится строкой или исключением, которое экран может назвать: 200/201 —
строка и «создана/уже была», 404 — «записи нет» (чужая или скрытая — каталог
не различает намеренно), 5xx — «дневник не отвечает», обрыв сети —
«неизвестно, дошло ли». Путь, метод и оба заголовка субъекта — тоже здесь:
без них изоляция каталога не о чем.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Generator
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import (
    MealNotFoundError,
    NutritionUncertainOutcomeError,
    NutritionUnavailableError,
    SavedMealRow,
    get_nutrition_client,
    reset_nutrition_client,
)

EXT = "bot:max:2092"
MEAL_ID = "0b6f3c2e-9d1a-4c55-8e2f-2092aaaa0001"
LOG_ID = "0b6f3c2e-9d1a-4c55-8e2f-2092bbbb0001"
ROW = {
    "id": MEAL_ID,
    "dish_name": "Борщ",
    "portion_g": 250.0,
    "calories": 125.0,
    "protein_g": 5.0,
    "fat_g": 7.5,
    "carbs_g": 10.0,
    "source_food_log_id": None,
    "created_at": "2026-09-18T12:00:00+00:00",
}


@pytest.fixture
def ayla(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any], None, None]:
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.NUTRITION_SERVICE_TOKEN = "svc-token-2092"  # noqa: S105 — test sentinel
    state: dict[str, Any] = {"status": 200, "json": {}, "seen": []}
    real_async = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append(
            (
                request.method,
                request.url.path,
                dict(request.headers),
                request.content.decode() if request.content else "",
            )
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


def _seen(state: dict[str, Any]) -> tuple[str, str, dict[str, str], str]:
    assert len(state["seen"]) == 1, state["seen"]
    return state["seen"][0]


class TestWire:
    def test_list_is_a_get_under_both_subject_headers(self, ayla: dict[str, Any]) -> None:
        ayla["json"] = {"data": {"items": [ROW]}}

        rows = asyncio.run(get_nutrition_client().list_saved_meals(external_user_id=EXT))

        method, path, headers, _ = _seen(ayla)
        assert (method, path) == ("GET", "/api/v1/nutrition/internal/saved-meals/")
        assert headers["x-service-token"] == "svc-token-2092"
        assert headers["x-external-user-id"] == EXT
        assert rows == [
            SavedMealRow(
                meal_id=MEAL_ID,
                dish_name="Борщ",
                portion_g=250.0,
                calories=125.0,
                protein_g=5.0,
                fat_g=7.5,
                carbs_g=10.0,
                source_food_log_id=None,
                created_at="2026-09-18T12:00:00+00:00",
            )
        ]

    def test_save_from_a_record_posts_food_log_id(self, ayla: dict[str, Any]) -> None:
        ayla["status"] = 201
        ayla["json"] = {"data": {**ROW, "source_food_log_id": LOG_ID}}

        row, created = asyncio.run(
            get_nutrition_client().save_meal(external_user_id=EXT, food_log_id=LOG_ID)
        )

        method, path, _, body = _seen(ayla)
        assert (method, path) == ("POST", "/api/v1/nutrition/internal/saved-meals/")
        assert json.loads(body) == {"food_log_id": LOG_ID}
        assert created is True
        assert row.source_food_log_id == LOG_ID

    def test_save_from_a_snapshot_posts_dish_and_portion_and_reads_200_as_existing(
        self, ayla: dict[str, Any]
    ) -> None:
        ayla["status"] = 200
        ayla["json"] = {"data": ROW}

        row, created = asyncio.run(
            get_nutrition_client().save_meal(
                external_user_id=EXT, dish_name="Борщ", portion_g=250.0, calories=125.0
            )
        )

        _, _, _, body = _seen(ayla)
        assert json.loads(body) == {"dish_name": "Борщ", "portion_g": 250.0, "calories": 125.0}
        assert created is False
        assert row.meal_id == MEAL_ID

    def test_delete_is_a_delete_on_the_row(self, ayla: dict[str, Any]) -> None:
        ayla["json"] = {"data": {"id": MEAL_ID, "deleted": True}}

        deleted_id = asyncio.run(
            get_nutrition_client().delete_saved_meal(external_user_id=EXT, meal_id=MEAL_ID)
        )

        method, path, _, _ = _seen(ayla)
        assert (method, path) == ("DELETE", f"/api/v1/nutrition/internal/saved-meals/{MEAL_ID}/")
        assert deleted_id == MEAL_ID


class TestRefusals:
    @pytest.mark.parametrize("method", ["delete_saved_meal", "save_from_record"])
    def test_404_is_not_found(self, ayla: dict[str, Any], method: str) -> None:
        ayla["status"] = 404
        ayla["json"] = {"error": {"code": "NOT_FOUND", "message": "Запись не найдена"}}
        client = get_nutrition_client()
        call = (
            client.delete_saved_meal(external_user_id=EXT, meal_id=MEAL_ID)
            if method == "delete_saved_meal"
            else client.save_meal(external_user_id=EXT, food_log_id=LOG_ID)
        )
        with pytest.raises(MealNotFoundError):
            asyncio.run(call)

    @pytest.mark.parametrize("status", [500, 502, 503])
    def test_5xx_is_unavailable(self, ayla: dict[str, Any], status: int) -> None:
        ayla["status"] = status
        with pytest.raises(NutritionUnavailableError):
            asyncio.run(get_nutrition_client().list_saved_meals(external_user_id=EXT))

    def test_a_network_failure_on_save_is_uncertain_not_refused(self, ayla: dict[str, Any]) -> None:
        """Запрос ушёл, ответа нет: строка МОГЛА появиться — экран не должен
        говорить «не сохранено» тем же словом, что «каталог отказал»."""
        ayla["raise"] = httpx.ReadTimeout("slow")
        with pytest.raises(NutritionUncertainOutcomeError):
            asyncio.run(
                get_nutrition_client().save_meal(
                    external_user_id=EXT, dish_name="Борщ", portion_g=250.0
                )
            )

    def test_a_200_with_a_malformed_body_is_not_an_empty_list(self, ayla: dict[str, Any]) -> None:
        """Пустой список — «избранного нет»; тело не разобрать — «каталог не
        ответил». Спутать их значит показать человеку пустой экран вместо ошибки."""
        ayla["json"] = {"data": "not-a-dict"}
        with pytest.raises(NutritionUnavailableError):
            asyncio.run(get_nutrition_client().list_saved_meals(external_user_id=EXT))
