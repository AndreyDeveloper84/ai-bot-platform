"""Месячный потолок минут распознавания (вопрос 7 ТЗ; настройка, по умолчанию выкл.).

Счётчик секунд аудио за календарный месяц в кэше (Redis на пилоте). Не
источник правды для бухгалтерии — предохранитель от «кто-то шлёт
голосовые по кругу»: при исчерпании человек получает отказ
``voice_provider_unavailable`` с причиной ``budget`` в логе, а не тишину.

Best-effort: если кэш недоступен, распознавание **разрешается** и пишется
WARNING — без Redis бот и так не работает, а ложный отказ хуже
пропущенного лимита на одну минуту.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Final

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_KEY_PREFIX: Final[str] = "speech:stt:seconds:"
#: 40 суток — переживает любой месяц с запасом, потом ключ сам исчезает.
_KEY_TTL_S: Final[int] = 40 * 24 * 3600


def _cap_seconds() -> int:
    minutes = int(getattr(settings, "VOICE_STT_MONTHLY_MINUTES_CAP", 0) or 0)
    return max(0, minutes) * 60


def month_key(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{_KEY_PREFIX}{now:%Y-%m}"


def reserve(seconds: float, *, now: datetime | None = None) -> bool:
    """Зачесть ``seconds`` в месяц; ``False`` — потолок исчерпан, вызов делать нельзя.

    Резервируем **до** вызова провайдера (иначе последняя минута всегда
    проскакивает), при отказе провайдера ничего не возвращаем — цена
    неточности одна минута в месяц, зато без гонок.
    """
    cap = _cap_seconds()
    if cap <= 0:
        return True
    key = month_key(now)
    amount = max(1, int(round(seconds)))
    try:
        cache.add(key, 0, timeout=_KEY_TTL_S)
        used = cache.incr(key, amount)
    except Exception as exc:  # noqa: BLE001 — кэш недоступен: пропускаем, не роняем ход
        logger.warning("speech.budget.cache_unavailable exc=%s", type(exc).__name__)
        return True
    if used > cap:
        logger.warning("speech.budget.exhausted used_s=%d cap_s=%d", used, cap)
        return False
    return True
