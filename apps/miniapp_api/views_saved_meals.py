"""Избранные блюда — прокси Mini App к каталогу под субъектом (DRF-2092, F12).

    GET    /customer/saved-meals            → каталог ``internal/saved-meals/``
    POST   /customer/saved-meals            → снимок ИЛИ ``food_log_id`` записи
    DELETE /customer/saved-meals/{meal_id}  → скрыть

Источник — каталог (beautygo_backend#505): строки живут под внешним
идентификатором человека и переживают переустановку; здесь ничего не
хранится. Изоляция — по построению: субъект называется каталогу
``external_user_id_for(bot_user)`` из подписанного initData; чужой id в
запросе не существует как понятие.

Отдельный модуль, а не :mod:`apps.miniapp_api.views` — тот в работе у
DRF-2071; ворота и отказы берутся оттуда импортом, чтобы у одной поверхности
не выросло второе понятие «дневник выключен» или «каталог не отвечает».

Ворота — как у соседей на этой же поверхности:

* GET — ``NUTRITION_ENABLED`` + согласие ПДн (``_diary_entry_gate``,
  ``needs_consent=True``): чтение личных записей, как ``wellness/today``;
* POST — плюс согласие дневника из реестра (``_food_text_gate`` →
  ``diary_is_granted``, DRF-1963): «в избранное» пишет личные данные, как
  запись текстом (F8);
* DELETE — как удаление записи (DRF-1838): согласия не требует — убрать
  своё человек вправе всегда, это не новая обработка.

Логи — без идентификатора канала (DRF-2009): в строке только ``bot_user.pk``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.identity.models import BotUser
from apps.miniapp_api.views import (
    _diary_entry_gate,
    _error,
    _food_entry_refusal,
    _food_text_gate,
    _food_text_json,
    require_init_data,
)

logger = logging.getLogger(__name__)

_PORTION_MIN = 1
_PORTION_MAX = 5000


def _row_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row.meal_id,
        "dish_name": row.dish_name,
        "portion_g": row.portion_g,
        "calories": row.calories,
        "protein_g": row.protein_g,
        "fat_g": row.fat_g,
        "carbs_g": row.carbs_g,
        "source_food_log_id": row.source_food_log_id,
        "created_at": row.created_at,
    }


def _optional_number(body: dict[str, Any], key: str) -> float | None | JsonResponse:
    value = body.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return _error("malformed", f"{key} must be a non-negative number", 400)
    return float(value)


@csrf_exempt
@require_http_methods(["GET", "POST"])
@require_init_data
def customer_saved_meals(request: HttpRequest) -> HttpResponse:
    """GET — список избранного; POST — сохранить (из записи или снимком)."""
    from apps.integrations.ayla import (
        NutritionAPIError,
        external_user_id_for,
        get_nutrition_client,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    external_id = external_user_id_for(bot_user)

    if request.method == "GET":
        refused = _diary_entry_gate(bot_user, needs_consent=True)
        if refused is not None:
            return refused
        try:
            rows = asyncio.run(
                get_nutrition_client().list_saved_meals(external_user_id=external_id)
            )
        except NutritionAPIError as exc:
            return _food_entry_refusal(exc, external_id=external_id, step="saved_meals.list")
        return JsonResponse({"items": [_row_payload(r) for r in rows]})

    refused = _food_text_gate(bot_user)
    if refused is not None:
        return refused
    body = _food_text_json(request)
    if isinstance(body, JsonResponse):
        return body

    food_log_id = body.get("food_log_id")
    kwargs: dict[str, Any]
    if food_log_id is not None:
        if not isinstance(food_log_id, str) or not food_log_id.strip():
            return _error("malformed", "food_log_id must be a non-empty string", 400)
        kwargs = {"food_log_id": food_log_id.strip()}
    else:
        dish = body.get("dish_name")
        portion = body.get("portion_g")
        if not isinstance(dish, str) or not dish.strip():
            return _error("malformed", "dish_name is required (or food_log_id)", 400)
        if (
            isinstance(portion, bool)
            or not isinstance(portion, (int, float))
            or not (_PORTION_MIN <= portion <= _PORTION_MAX)
        ):
            return _error(
                "malformed",
                f"portion_g must be a number between {_PORTION_MIN} and {_PORTION_MAX}",
                400,
            )
        kwargs = {"dish_name": dish.strip(), "portion_g": float(portion)}
        for key in ("calories", "protein_g", "fat_g", "carbs_g"):
            parsed = _optional_number(body, key)
            if isinstance(parsed, JsonResponse):
                return parsed
            kwargs[key] = parsed

    try:
        row, created = asyncio.run(
            get_nutrition_client().save_meal(external_user_id=external_id, **kwargs)
        )
    except NutritionAPIError as exc:
        return _food_entry_refusal(exc, external_id=external_id, step="saved_meals.save")
    logger.info(
        "miniapp_api.saved_meals.saved bot_user=%s created=%s from_record=%s",
        bot_user.pk,
        created,
        food_log_id is not None,
    )
    return JsonResponse(_row_payload(row), status=201 if created else 200)


@csrf_exempt
@require_http_methods(["DELETE"])
@require_init_data
def customer_saved_meal(request: HttpRequest, meal_id: str) -> HttpResponse:
    """DELETE — скрыть из избранного; чужая или уже скрытая — 404."""
    from apps.integrations.ayla import (
        NutritionAPIError,
        external_user_id_for,
        get_nutrition_client,
    )

    meal_id = (meal_id or "").strip()
    if not meal_id:
        return _error("malformed", "meal_id is required", 400)
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    refused = _diary_entry_gate(bot_user, needs_consent=False)
    if refused is not None:
        return refused
    external_id = external_user_id_for(bot_user)
    try:
        deleted_id = asyncio.run(
            get_nutrition_client().delete_saved_meal(external_user_id=external_id, meal_id=meal_id)
        )
    except NutritionAPIError as exc:
        return _food_entry_refusal(exc, external_id=external_id, step="saved_meals.delete")
    return JsonResponse({"id": deleted_id, "deleted": True})
