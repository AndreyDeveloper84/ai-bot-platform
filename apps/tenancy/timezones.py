"""Пояс салона — одно правило на весь бот (DRF-2595).

До этого модуля правило было записано тринадцать раз, и записи расходились:
одиннадцать давали Москву, ``salon_greeting`` — UTC (час следующего визита в
приветствии мастера на три часа раньше, счётчик «сегодня» с 00:00 до 03:00 МСК
— за вчера), ``views_profile_card`` падал на битом имени, и лишь две из
тринадцати писали в журнал, что пояс салона битый. Каждая новая поверхность
заводила четырнадцатую запись — и выбирала запасной пояс заново.

Правило:

* имя из ``Tenant.timezone`` — если это известный IANA-пояс;
* пусто → :data:`FALLBACK_TZ` молча: пояс не задан, и у поля тот же default;
* битое имя → :data:`FALLBACK_TZ` и строка ``tenancy.bad_tenant_tz`` в журнал.
  Журнал — не украшение: до DRF-2595 так делали ``salon_day`` и
  ``time_preference``, а наивное сведение стёрло бы и эти две строки;
Отказ вместо подмены — ДВА разных вопроса, и у помощника два разных флага.
Их склейка в один (``strict``) однажды уже дала неточный список мест:

* ``refuse_broken=True`` — имя НЕПРИГОДНО (опечатка): исходное исключение после
  ``tenancy.bad_tenant_tz``;
* ``refuse_empty=True`` — пояс НЕ ЗАДАН (стёрт: у поля есть default, «никогда
  не задавали» не бывает): :class:`EmptyTenantTimezone` после
  ``tenancy.empty_tenant_tz``. Причины в журнале разные — разное лечение.

Какие флаги ставить, решает цена ошибки на выходе пути и его прежнее поведение:

* выход — ОБЯЗАТЕЛЬСТВО (создание записи, предложенные часы): оба флага. МСК
  там правдоподобен, а неверный час — человек, который приедет не тогда. До
  DRF-2595 эти пути звали ``ZoneInfo(tenant.timezone)`` и отказывали на обоих;
* выход — ПОКАЗ (карточка профиля, полоса готовности): только
  ``refuse_broken``. Там стояло ``ZoneInfo(... or "Europe/Moscow")`` — пусто
  давало МСК, отказывало только битое; отказ на пустом сломал бы экран ради
  косметической неточности;
* остальные пути — ни одного флага.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

#: Запасной пояс — один и назван. Москва, а не UTC: салоны пилота в Пензе
#: (UTC+3), и все клиентские и мастерские поверхности уже показывали время по
#: Москве, когда пояс не читался; UTC в запасе давал время на три часа раньше
#: настоящего. У поля ``Tenant.timezone`` тот же default.
FALLBACK_TZ = "Europe/Moscow"


class EmptyTenantTimezone(ValueError):
    """``refuse_empty``: пояс салона пуст. ``ValueError`` — как у прежнего
    ``ZoneInfo("")`` на этих путях, чтобы вызывающие ловили то же, что ловили."""


def salon_zone(tenant: Any, *, refuse_broken: bool = False, refuse_empty: bool = False) -> ZoneInfo:
    """Пояс салона ``tenant`` по правилу модуля; ``tenant=None`` — как пустой."""

    name = str(getattr(tenant, "timezone", "") or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "tenancy.bad_tenant_tz tenant=%s tz=%r",
                getattr(tenant, "pk", None),
                name[:64],
            )
            if refuse_broken:
                raise
    elif refuse_empty:
        logger.warning("tenancy.empty_tenant_tz tenant=%s", getattr(tenant, "pk", None))
        raise EmptyTenantTimezone(f"tenant {getattr(tenant, 'pk', None)} has no timezone")
    return ZoneInfo(FALLBACK_TZ)


def salon_iso(moment: datetime | None, zone: ZoneInfo) -> str | None:
    """Момент в ISO со смещением салона; ``None`` → ``None``.

    Момент не сдвигается — меняется только запись: наивный считается уже
    салонным (так его пишет расписание), осознанный переводится в пояс.
    """

    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=zone).isoformat()
    return moment.astimezone(zone).isoformat()
