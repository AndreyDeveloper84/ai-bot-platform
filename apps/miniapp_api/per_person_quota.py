"""Квота на человека для байтовых прокси Mini App (DRF-2618, DRF-2629).

Прокси картинок (фото мастера — ``master_media``, снимок дневника —
``views_diary_days.customer_food_photo``) ходят в каталог одним адресом бота,
а квота каталога — на адрес. Без своей квоты на человека один зациклившийся
клиент выбирает её за всех. Этот тормоз стоит до похода в каталог.

Фиксированное окно в общем кэше Django (в проде — Redis,
``_assert_production_cache_backend``): ``add`` ставит срок ровно один раз, в
начале окна, и окно не ползёт за запросами (тот же приём, что у
``identity.services.staff_invites``). Кэш недоступен — пропускаем: это
тормоз, а не пропуск, и отказ картинок всем из-за кэша хуже минуты без
тормоза.
"""

from __future__ import annotations

import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


def over_quota(scope: str, identity: str, *, per_minute: int) -> bool:
    """Сверх ли квоты этот запрос человека в этой области (``scope``)."""
    from django.core.cache import cache

    key = f"miniapp.{scope}.quota:{identity}"
    try:
        if cache.add(key, 1, timeout=WINDOW_SECONDS):
            return False
        return int(cache.incr(key)) > per_minute
    except ValueError:
        return False  # ключ истёк между add и incr — первый запрос окна
    except Exception as exc:  # noqa: BLE001 — тормоз, не ворота
        logger.warning("miniapp_api.%s.quota_unavailable exc=%s", scope, type(exc).__name__)
        return False


def rate_limited(error: str, detail: str) -> JsonResponse:
    """Отказ по квоте: 429 с ``Retry-After`` на длину окна."""
    response = JsonResponse({"error": error, "detail": detail}, status=429)
    response["Retry-After"] = str(WINDOW_SECONDS)
    return response
