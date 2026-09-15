"""DRF-1838 — ``update_meal`` / ``delete_meal`` / ``restore_meal``: named refusals.

The contract route table proves path, method and auth; the skill tests replace
the client with a double. Neither sees how an HTTP answer becomes an error the
chat can name. That mapping is the subject here: 404 «записи нет», 410 «окно
закрылось, удаление окончательно», 409 «запись ведёт учёт воды», 5xx «дневник
не отвечает» — each must stay distinguishable, or the person hears the wrong
sentence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import httpx
import pytest

from apps.integrations.ayla import (
    FoodLogResponse,
    MealDeletion,
    MealEditConflictError,
    MealNotFoundError,
    MealRestoreExpiredError,
    NutritionAPIError,
    NutritionUnavailableError,
    get_nutrition_client,
    reset_nutrition_client,
)

EXT = "bot:max:1838"
LOG_ID = "0b6f3c2e-9d1a-4c55-8e2f-1838aaaa0001"
ENTRY = {
    "id": LOG_ID,
    "dish_name": "борщ",
    "meal_type": "other",
    "calories": 125.0,
}


@pytest.fixture
def ayla(settings: Any, monkeypatch: pytest.MonkeyPatch) -> Generator[dict[str, Any], None, None]:
    """In-memory Ayla: answers with ``state["status"]`` / ``state["json"]``."""
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.NUTRITION_SERVICE_TOKEN = "svc-token-1838"  # noqa: S105 — test sentinel
    state: dict[str, Any] = {"status": 200, "json": {}, "seen": []}
    real_async = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        state["seen"].append((request.method, request.url.path))
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


def _call(method: str) -> Any:
    client = get_nutrition_client()
    if method == "update_meal":
        return asyncio.run(
            client.update_meal(external_user_id=EXT, log_id=LOG_ID, portion_multiplier=2.5)
        )
    return asyncio.run(getattr(client, method)(external_user_id=EXT, log_id=LOG_ID))


class TestSuccess:
    def test_update_patches_the_entry_and_returns_it(self, ayla) -> None:
        ayla["json"] = {"data": ENTRY}

        log = _call("update_meal")

        assert ayla["seen"] == [("PATCH", f"/api/v1/nutrition/internal/food-log/{LOG_ID}/")]
        assert isinstance(log, FoodLogResponse)
        assert (log.log_id, log.dish_name, log.calories) == (LOG_ID, "борщ", 125.0)

    def test_delete_returns_the_restore_window(self, ayla) -> None:
        ayla["json"] = {
            "data": {
                "entry_id": LOG_ID,
                "deleted": True,
                "restore_window_expires_at": "2026-09-15T12:15:00+00:00",
            }
        }

        deletion = _call("delete_meal")

        assert ayla["seen"] == [("DELETE", f"/api/v1/nutrition/internal/food-log/{LOG_ID}/")]
        assert deletion == MealDeletion(
            log_id=LOG_ID, restore_window_expires_at="2026-09-15T12:15:00+00:00"
        )

    def test_restore_posts_and_returns_the_entry(self, ayla) -> None:
        ayla["json"] = {"data": ENTRY}

        log = _call("restore_meal")

        assert ayla["seen"] == [("POST", f"/api/v1/nutrition/internal/food-log/{LOG_ID}/restore/")]
        assert log.log_id == LOG_ID


class TestNamedRefusals:
    @pytest.mark.parametrize("method", ["update_meal", "delete_meal", "restore_meal"])
    def test_404_is_not_found(self, ayla, method) -> None:
        ayla["status"] = 404
        ayla["json"] = {"error": {"code": "NOT_FOUND", "message": "Запись не найдена"}}

        with pytest.raises(MealNotFoundError):
            _call(method)

    def test_410_on_restore_is_the_final_deletion(self, ayla) -> None:
        ayla["status"] = 410
        ayla["json"] = {"error": {"code": "RESTORE_WINDOW_EXPIRED", "message": "..."}}

        with pytest.raises(MealRestoreExpiredError):
            _call("restore_meal")

    @pytest.mark.parametrize("method", ["update_meal", "delete_meal"])
    def test_409_is_the_water_mirror(self, ayla, method) -> None:
        ayla["status"] = 409
        ayla["json"] = {"error": {"code": "CONFLICT", "message": "..."}}

        with pytest.raises(MealEditConflictError):
            _call(method)

    @pytest.mark.parametrize("method", ["update_meal", "delete_meal", "restore_meal"])
    def test_5xx_is_unavailable(self, ayla, method) -> None:
        ayla["status"] = 503
        ayla["json"] = {}

        with pytest.raises(NutritionUnavailableError):
            _call(method)

    def test_other_4xx_is_a_plain_api_error_not_a_named_refusal(self, ayla) -> None:
        ayla["status"] = 400
        ayla["json"] = {"error": {"code": "VALIDATION_ERROR", "message": "..."}}

        with pytest.raises(NutritionAPIError) as caught:
            _call("update_meal")

        # POSITIVE first: it is an API error; then — none of the named sentences.
        assert "VALIDATION_ERROR" in str(caught.value)
        assert type(caught.value) is NutritionAPIError


class TestUncertainAndMalformed:
    """Ревью: таймаут — не «ничего не изменила»; битое тело 200 — названная ошибка."""

    def test_a_network_timeout_is_an_uncertain_outcome(self, ayla) -> None:
        ayla["raise"] = httpx.ReadTimeout("slow")

        with pytest.raises(NutritionUnavailableError) as caught:
            _call("delete_meal")

        # POSITIVE first: the request did leave — Ayla saw it.
        assert ayla["seen"] == [("DELETE", f"/api/v1/nutrition/internal/food-log/{LOG_ID}/")]
        assert type(caught.value).__name__ == "NutritionUncertainOutcomeError"

    def test_a_malformed_success_body_is_an_api_error(self, ayla) -> None:
        ayla["json"] = {"data": None}

        with pytest.raises(NutritionAPIError):
            _call("delete_meal")


class TestUnreadableSuccessIsUncertain:
    """Ревью Mini App: DELETE ответил 200, тело не читается — удаление ПРОШЛО.

    Назвать это обычной ошибкой значит сказать человеку «ничего не
    изменилось» про уже удалённую запись и не предложить её вернуть.
    """

    def test_an_unreadable_delete_body_is_an_uncertain_outcome(self, ayla) -> None:
        ayla["json"] = {"data": None}

        with pytest.raises(NutritionAPIError) as caught:
            _call("delete_meal")

        # POSITIVE first: Ayla did receive and answer the DELETE.
        assert ayla["seen"] == [("DELETE", f"/api/v1/nutrition/internal/food-log/{LOG_ID}/")]
        assert type(caught.value).__name__ == "NutritionUncertainOutcomeError"
