"""Сигнал бюджета распознавания фото операторам в MAX (DRF-2196, Сканер-2).

Каталог (#519, DRF-2145) уже считает снимки по суткам UTC и уже знает, что
пересёк 80 % и 100 % общего дневного потолка. Дальше сигнал упирается в
стену: приёмника алертов у каталога нет — ``_send_signal`` умеет ровно
``sentry_sdk.capture_message``, а ``SENTRY_DSN`` на пилоте пуст, значит это
no-op. Остаётся строка в логе, которую никто не читает. Единственный
работающий канал к людям — :func:`apps.observability.alerting.page` →
MAX-чат операторов (DRF-2158), и он здесь, в боте.

# Что это за модуль

Вариант-независимое ЯДРО: ему дают числа (сколько израсходовано, каков
потолок, какие это сутки), оно решает — звучать ли сигналу и каким.
**Как числа сюда доедут, здесь не решается.** Обсуждаются два адаптера:

* **(а)** каталог публикует ``system.module.health.degraded`` в
  ``/api/v1/internal/events/ingest`` (рельс с HMAC, ретраями и
  dead-letter у каталога уже есть; имя в закрытом словаре бота уже
  есть, но пока не в ``_KNOWN_NAMES`` ingest — то есть добавление его
  туда правит контракт §3, а это уровень ADR-0009);
* **(б)** бот опрашивает ручку бюджета каталога по расписанию (шаблон —
  :func:`apps.catalog.tasks.alert_stale_catalog_sync`; ручки, впрочем,
  у каталога сегодня нет, её пришлось бы завести).

Выбор за владельцем; адаптер — отдельным листом. Ядро переживает любой
выбор, поэтому и сделано первым.

**Третий вариант отброшен и назван, чтобы не всплыл позже:** бот получает
``used``/``limit`` в теле отказа (DRF-2195) и мог бы звать ``page`` сам,
без каталога вовсе. Не годится — так достижимы только 100 % и только
когда человек уже упёрся, а 80 % (раннее предупреждение, ради которого
лист и заведён) недостижим в принципе. Считать снимки самому —
дублирование канонического состояния: счётчик принадлежит каталогу
(ADR-0009, правило 1), и своя копия соврала бы при любом другом клиенте.

# Два капкана :func:`page`, и почему они правило класса, а не деталь

1. **Дедуп ``page()`` — ГЛОБАЛЬНОЕ окно** ``ALERTS_DEDUP_TTL_SECONDS``
   (умолчание 300 с, один knob на весь бот). «Одна страница в сутки» его
   средствами не выражается: поднять окно до суток — значит заглушить
   дедуп всему остальному. Поэтому свой ключ по дню, ДО вызова ``page``,
   — той же формы, что у каталога (``food_scan:signal:{day}:{level}``).
2. **``severity="critical"`` дедуп ОБХОДИТ** (``alerting._is_duplicate``:
   «never dedup critical»). Поэтому 100 % — ``error``, а не ``critical``:
   иначе «одна страница в сутки» превратилась бы в страницу на КАЖДУЮ
   попытку скана после исчерпания бюджета, то есть ровно в тот шум, от
   которого оператор глушит канал. Тот же вывод независимо записан в
   докстринге :func:`apps.catalog.tasks.alert_stale_catalog_sync`:
   «оператор, который заглушил канал, — это состояние, ради
   предотвращения которого лист и существует».

# Страж не роняет то, что стережёт

Сигнал не важнее работы: :func:`signal_budget` никогда не бросает наружу.
Потолок ``0`` или отрицательный — молчание, а не падение; упавший сток
алертинга — предупреждение в лог, а не исключение в вызывающего. Тот же
принцип, что у ``page()`` («best-effort and never raises») и у
``_signal_if_crossed`` каталога («сигнал никогда не важнее скана»).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Final

from django.core.cache import cache

from apps.observability.alerting import Severity, page

logger = logging.getLogger(__name__)

#: Пороги — те же, что у каталога (``food_scan_budget.SIGNAL_THRESHOLDS``).
#: Уровни намеренно ``warning`` / ``error`` и никогда ``critical`` — см.
#: капкан 2 в докстринге модуля.
THRESHOLDS: Final[tuple[tuple[float, Severity], ...]] = ((0.8, "warning"), (1.0, "error"))

_DEDUP_KEY: Final = "scan_budget_alert:{day}:{level}"

#: Что это значит людям — а не «счётчик достиг числа». Оператор читает
#: последствие: фото перестали распознаваться, и сами по себе не начнут
#: раньше полуночи UTC, когда счётчик обнулится.
_CONSEQUENCE_AT_LIMIT: Final = (
    "Фото не распознаются до полуночи UTC — до этого момента распознавание "
    "по фото людям недоступно, запись еды словами работает как обычно."
)
_CONSEQUENCE_AT_WARNING: Final = (
    "Бюджет кончится до полуночи UTC, если расход не изменится. После этого "
    "фото не распознаются, запись еды словами останется."
)


def _seconds_until_midnight_utc() -> int:
    now = datetime.now(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((midnight - now).total_seconds()))


def _first_today(day: str, level: Severity) -> bool:
    """Первый ли это сигнал такого уровня в эти сутки.

    Потеря кэша читается как «уже звучал» — молчание, а не шквал. Довод:
    ``page()`` дедуплицирует ЧЕРЕЗ ТОТ ЖЕ кэш, поэтому при его потере
    открытый отказ дал бы не «одну лишнюю страницу», а страницу на каждую
    попытку скана — то самое, от чего оператор глушит канал. Каталог в
    ``_signal_if_crossed`` выбрал то же самое.
    """
    try:
        return bool(
            cache.add(
                _DEDUP_KEY.format(day=day, level=level), 1, timeout=_seconds_until_midnight_utc()
            )
        )
    except Exception as exc:  # noqa: BLE001 — потеря кэша не роняет вызывающего
        logger.warning(
            "observability.scan_budget.dedup_unavailable day=%s level=%s err=%s",
            day,
            level,
            type(exc).__name__,
        )
        return False


def signal_budget(
    *,
    used: int,
    limit: int,
    day: str,
    cost_usd: str | None = None,
) -> None:
    """Поднять страницу операторам, если сутки пересекли порог бюджета.

    Args:
      used: израсходовано распознаваний за сутки (общий счётчик каталога).
      limit: общий суточный потолок.
      day: сутки в ISO (UTC) — он же часть ключа дедупа.
      cost_usd: суточная стоимость строкой, справка. ``None`` — «не
        посчитано» (цены не заданы либо провайдер не отдал ``usage``);
        сигнал звучит всё равно, просто без справки.

    Ничего не возвращает и никогда не бросает: сигнал не важнее работы.
    """
    if limit <= 0:
        # Потолка нет — порогов тоже. Не падение и не сигнал: делить на
        # ноль нечего, а «звучать всегда» при выключенном бюджете значит
        # звучать зря.
        logger.info("observability.scan_budget.no_limit used=%s limit=%s day=%s", used, limit, day)
        return

    crossed = [(ratio, level) for ratio, level in THRESHOLDS if used >= limit * ratio]
    if not crossed:
        return

    # Пересечено несколько порогов разом (расход прыгнул с нуля к потолку):
    # звучит СТАРШИЙ, младшие гасятся молча — их ключ дедупа занимается, но
    # страница не идёт. Каталог в `_signal_if_crossed` шлёт все пересечённые;
    # здесь это было бы «80 % и 100 % одной пачкой», то есть предупреждение о
    # том, что уже случилось. Младший порог не выбрасывается, а помечается
    # израсходованным: иначе он прозвучал бы следующим сканом, после того как
    # оператор уже прочитал про 100 %.
    *lower, (ratio, level) = crossed
    for _lower_ratio, lower_level in lower:
        _first_today(day, lower_level)

    if _first_today(day, level):
        percent = int(ratio * 100)
        at_limit = ratio >= 1.0
        title = f"Бюджет распознавания фото: {percent} % за сутки ({used}/{limit})"
        body = (
            f"{_CONSEQUENCE_AT_LIMIT if at_limit else _CONSEQUENCE_AT_WARNING}\n\n"
            f"сутки (UTC): {day}\n"
            f"израсходовано: {used} из {limit} ({used / limit:.0%})\n"
            f"стоимость за сутки, USD: {cost_usd if cost_usd is not None else 'не посчитана'}\n\n"
            "Потолки — FOOD_SCAN_DAILY_TOTAL (общий) и FOOD_SCAN_DAILY_PER_USER "
            "(личный) в настройках каталога; счётчик обнуляется в полночь UTC.\n"
            "В сигнале нет и не может быть идентификаторов людей: каталог "
            "считает снимки, а не тех, кто их прислал."
        )

        # `page` — best-effort и сам не бросает, но сток за ним чужой:
        # оборачиваем, потому что страж не должен ронять то, что стережёт.
        try:
            page(level, title, body, dedup_key=f"scan_budget:{day}:{level}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "observability.scan_budget.page_failed day=%s level=%s err=%s",
                day,
                level,
                type(exc).__name__,
            )


__all__ = ["THRESHOLDS", "signal_budget"]
