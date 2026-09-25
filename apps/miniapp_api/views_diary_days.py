"""Дневник за неделю — прокси Mini App к каталогу под субъектом (DRF-2099).

    GET /customer/diary/days?from&to  → каталог ``internal/diary/days/``
    GET /customer/diary/day?date=     → каталог ``internal/summary/?date=``

Источник дня — каталог (beautygo_backend#511): границы суток он считает
по поясу человека (``NutritionProfile.timezone``, иначе UTC), и здесь ни
дат, ни поясов не пересчитывают — период проходит как есть, ответ тоже.
Изоляция — по построению: субъект называется каталогу
``external_user_id_for(bot_user)`` из подписанного initData.

Отдельный модуль, а не :mod:`apps.miniapp_api.views` — тот в работе у
соседних окон; ворота и отказы берутся оттуда импортом, чтобы у одной
поверхности не выросло второе понятие «дневник выключен».

Ворота — как у сводки (``wellness/today``): ``NUTRITION_ENABLED`` +
согласие ПДн (``_diary_entry_gate``, ``needs_consent=True``) — чтение
личных записей.

``nutrition_numbers_hidden`` — тот же производный булев, что у сводки, и с
тем же умолчанием: наружу уходит ОДИН булев, а не ``health_flags``; когда
анкета не прочиталась, ключа нет и экран прячет числа (fail-closed). Анкеты
нет вовсе — не отказ чтения: флага нет, числа показываются.

Прошлые дни здесь только читаются: правка и удаление записей за прошлые
дни — предел этой поверхности (не в DRF-2099).

Логи — без идентификатора канала (DRF-2009): только ``bot_user.pk``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
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
    require_init_data,
)

logger = logging.getLogger(__name__)


def _numbers_hidden(profile_res: Any, *, bot_user: BotUser) -> bool | None:
    """Как в ``customer_wellness_today``: ``None`` — анкета не прочиталась."""
    if isinstance(profile_res, Exception):
        logger.warning(
            "diary_days.profile_unavailable bot_user=%s err=%s",
            bot_user.pk,
            type(profile_res).__name__,
        )
        return None
    if profile_res is None:
        return False
    return bool((profile_res.health_flags or {}).get("eating_disorder"))


def _day_payload(row: Any) -> dict[str, Any]:
    return {
        "date": row.date,
        "meals_count": row.meals_count,
        "kcal": row.kcal,
        "has_entries": row.has_entries,
    }


def _iso_date_or_none(raw: str | None) -> str | None:
    """``YYYY-MM-DD`` ровно; иначе ``None`` — вызывающий решает, отказ это."""
    if not raw or len(raw) != 10:
        return None
    try:
        dt.date.fromisoformat(raw)
    except ValueError:
        return None
    return raw


@csrf_exempt
@require_http_methods(["GET"])
@require_init_data
def customer_diary_days(request: HttpRequest) -> HttpResponse:
    """Строка на каждый день периода; без периода — неделя каталога."""
    from apps.integrations.ayla import (
        NutritionAPIError,
        external_user_id_for,
        get_nutrition_client,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    refused = _diary_entry_gate(bot_user, needs_consent=True)
    if refused is not None:
        return refused
    external_id = external_user_id_for(bot_user)

    date_from = _iso_date_or_none(request.GET.get("from"))
    date_to = _iso_date_or_none(request.GET.get("to"))
    if (request.GET.get("from") and date_from is None) or (
        request.GET.get("to") and date_to is None
    ):
        return _error("malformed", "from/to must be YYYY-MM-DD", 400)

    async def _reads() -> tuple[Any, Any]:
        client = get_nutrition_client()
        return await asyncio.gather(
            client.diary_days(external_user_id=external_id, date_from=date_from, date_to=date_to),
            client.get_profile(external_user_id=external_id),
            return_exceptions=True,
        )

    days_res, profile_res = asyncio.run(_reads())
    if isinstance(days_res, NutritionAPIError):
        return _food_entry_refusal(days_res, external_id=external_id, step="diary_days")
    if isinstance(days_res, BaseException):
        raise days_res

    payload: dict[str, Any] = {
        "timezone": days_res.timezone,
        "from": days_res.date_from,
        "to": days_res.date_to,
        "days": [_day_payload(r) for r in days_res.days],
    }
    hidden = _numbers_hidden(profile_res, bot_user=bot_user)
    if hidden is not None:
        payload["nutrition_numbers_hidden"] = hidden
    return JsonResponse(payload)


#: Типы, которые ручка снимка готова объявить браузеру. Всё остальное
#: уезжает как поток байтов: см. про один источник в ``customer_food_photo``.
_PHOTO_TYPES_SHOWN = frozenset({"image/jpeg", "image/png", "image/webp"})


@csrf_exempt
@require_http_methods(["GET"])
@require_init_data
def customer_food_photo(request: HttpRequest, log_id: str) -> HttpResponse:
    """Снимок записи дневника — сам файл, через бот (DRF-2455, §77 п.40).

    Зачем через бот, а не ссылкой на хранилище: адрес MinIO внутренний для
    контейнера — телефон его не видит; и бакет создаётся ``public-read``,
    то есть утёкшая ссылка работала бы у любого, кто её получил. Снимки
    еды люди делают дома, и цена такой утечки не гипотетическая.

    Владение проверяет каталог (чужая запись — 404). Здесь — та же
    проверка входа, что у остального дневника: без ``initData`` ручка не
    отвечает вовсе, иначе знание адреса давало бы доступ к снимку.
    """
    from apps.integrations.ayla import (
        NutritionAPIError,
        external_user_id_for,
        get_nutrition_client,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    refused = _diary_entry_gate(bot_user, needs_consent=True)
    if refused is not None:
        return refused
    external_id = external_user_id_for(bot_user)

    try:
        photo = asyncio.run(
            get_nutrition_client().food_photo(
                external_user_id=external_id,
                log_id=str(log_id),
            )
        )
    except NutritionAPIError as exc:
        return _food_entry_refusal(exc, external_id=external_id, step="food_photo")

    if photo is None:
        # Снимка нет: записана текстом или удалён по сроку (§134). Пустое
        # тело с кодом 200 экран прочитал бы как «фото есть, но сломано».
        return _error("not_found", "photo not found", 404)

    content, content_type = photo
    # DRF-2455 — тип НЕ отражается как пришёл. Mini App и эта ручка живут
    # в одном источнике (`/` — приложение, `/api/` — бот), поэтому объект,
    # объявленный `text/html` или `image/svg+xml`, исполнился бы в нём
    # вместе с initData. `nosniff` закрывает только угадывание типа, а не
    # объявленный. Перечень — тот же, что на загрузке снимка.
    safe_type = content_type.split(";")[0].strip().lower()
    if safe_type not in _PHOTO_TYPES_SHOWN:
        safe_type = "application/octet-stream"
    response = HttpResponse(content, content_type=safe_type)
    response["Content-Disposition"] = "inline"
    # Приватно и ненадолго: снимок принадлежит одному человеку, а через 30
    # суток его не станет — общий кэш держать его не должен.
    response["Cache-Control"] = "private, max-age=300"
    return response


@csrf_exempt
@require_http_methods(["GET"])
@require_init_data
def customer_diary_day(request: HttpRequest) -> HttpResponse:
    """Записи одного дня — сводка каталога за дату; пустой день — пустой список."""
    from apps.integrations.ayla import (
        NutritionAPIError,
        external_user_id_for,
        get_nutrition_client,
    )

    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]
    refused = _diary_entry_gate(bot_user, needs_consent=True)
    if refused is not None:
        return refused
    date = _iso_date_or_none(request.GET.get("date"))
    if date is None:
        return _error("malformed", "date must be YYYY-MM-DD", 400)
    external_id = external_user_id_for(bot_user)

    async def _reads() -> tuple[Any, Any]:
        client = get_nutrition_client()
        return await asyncio.gather(
            client.daily_summary(external_user_id=external_id, date=date),
            client.get_profile(external_user_id=external_id),
            return_exceptions=True,
        )

    summary_res, profile_res = asyncio.run(_reads())
    if isinstance(summary_res, NutritionAPIError):
        return _food_entry_refusal(summary_res, external_id=external_id, step="diary_day")
    if isinstance(summary_res, BaseException):
        raise summary_res

    # Записи едут как отдала сводка (те же ключи, что у ``wellness/today``):
    # у каждой настоящее БЖУ и ``entry_origin`` — экран дня их только показывает.
    payload: dict[str, Any] = {
        "date": summary_res.date or date,
        "calories_total": summary_res.calories_total,
        "entries": list(summary_res.entries or []),
    }
    hidden = _numbers_hidden(profile_res, bot_user=bot_user)
    if hidden is not None:
        payload["nutrition_numbers_hidden"] = hidden
    return JsonResponse(payload)
