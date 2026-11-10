"""Окно в часах салона → два момента со смещением салона (DRF-2936).

Одно правило на две двери: предпросмотр закрытия времени
(``GET masters/<id>/schedule/impact/``) и отгул в местной форме
(``POST masters/<id>/time-off/`` с ``date`` + ``from`` + ``to``). Экран
шлёт дату и часы салона, смещение прикладывает сервер по поясу салона
(``salon_zone``, DRF-2595) — экран смещение не считает: это та ошибка,
из-за которой хронология показывала «11:00» вместо «14:00».

``24:00`` в ``to`` — конец этих суток, то есть следующая полночь. Без неё
«весь день» не выразить: ``time`` не знает 24:00, а окно 00:00–23:59
теряет последнюю минуту, которую каталог при закрытии дня считает.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls, datetime, time, timedelta, tzinfo

#: ``HH:MM``; секунды допускаются — прежний разбор (``time.fromisoformat``)
#: их принимал, и сужать контракт ради отказа нечем.
_HHMM = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?")

#: Конец суток в ``to``: следующая полночь.
END_OF_DAY = "24:00"


@dataclass(frozen=True)
class WindowRefusal:
    """Почему окно не разобрано — словами для ``detail``."""

    detail: str


def parse_local_window(
    day_raw: object, from_raw: object, to_raw: object, tz: tzinfo
) -> tuple[str, str] | WindowRefusal:
    """``date`` + ``from`` + ``to`` в часах салона → ``(start_at, end_at)`` ISO.

    ``from`` — ``HH:MM``; ``to`` — ``HH:MM`` или ``24:00``. Конец строго
    позже начала.
    """

    if not isinstance(day_raw, str) or not isinstance(from_raw, str) or not isinstance(to_raw, str):
        return WindowRefusal("date (YYYY-MM-DD), from (HH:MM) and to (HH:MM) are required")
    try:
        day = date_cls.fromisoformat(day_raw)
    except ValueError:
        return WindowRefusal("date must be YYYY-MM-DD")
    if not _HHMM.fullmatch(from_raw):
        return WindowRefusal("from must be HH:MM")
    start = time.fromisoformat(from_raw)
    start_at = datetime.combine(day, start, tzinfo=tz)
    if to_raw == END_OF_DAY:
        end_at = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=tz)
    elif _HHMM.fullmatch(to_raw):
        end_at = datetime.combine(day, time.fromisoformat(to_raw), tzinfo=tz)
    else:
        return WindowRefusal("to must be HH:MM or 24:00")
    if end_at <= start_at:
        return WindowRefusal("to must be after from")
    return start_at.isoformat(), end_at.isoformat()
