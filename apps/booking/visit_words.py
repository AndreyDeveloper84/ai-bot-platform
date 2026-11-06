"""Строка записи в чате — слова владельца 28.09 (DRF-2569), один дом.

``docs/OWNER_WORDS_DECISIONS_2026-09-28.md``, п.1–2:

    Массаж — мастер Марина · Формула тела, 25.09.2026 в 09:00

* время — ``ДД.ММ.ГГГГ в ЧЧ:ММ`` в поясе салона записи; сырое ISO/UTC
  человеку не показывается;
* мастер — «мастер {Имя}», без склонения («у Ольга», «с Марина» — нет);
* салон — через «·» сразу после мастера, время — в конце.

Строку собирают два чатовых носителя под одной шапкой «Твои предстоящие
записи:» — навык записи (``skills/booking/tools.py``) и глобальный бот
(``orchestrator/visits.py``). До этого модуля у них было две разные строки
на один вопрос человека; дом один — чтобы не разошлись снова.
"""

from __future__ import annotations

import logging
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


def visit_time_words(visit_at: str, salon_tz: str, *, fallback_tz: tzinfo | None = None) -> str:
    """ISO → «25.09.2026 в 09:00» в поясе салона.

    Не разобралось — пустая строка: время опускается, а не печатается как
    есть. Время без пояса оставляется как есть — придумывать ему смещение
    значило бы гадать. ``fallback_tz`` — только для носителя, у которого
    пояс салона бывает недостижим; вызывающий обязан назвать это пределом.
    """
    if not visit_at:
        return ""
    try:
        moment = datetime.fromisoformat(visit_at.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("visit_words.unparseable_visit_at raw=%r", visit_at)
        return ""
    if moment.tzinfo is not None:
        zone: tzinfo | None = fallback_tz
        if salon_tz:
            try:
                zone = ZoneInfo(salon_tz)
            except (ZoneInfoNotFoundError, ValueError):
                zone = fallback_tz
        if zone is not None:
            moment = moment.astimezone(zone)
    return moment.strftime("%d.%m.%Y в %H:%M")


def booking_line(*, service: str, master: str, salon: str, when: str) -> str:
    """«Массаж — мастер Марина · Формула тела, 25.09.2026 в 09:00» (без «• »)."""
    who = f"мастер {master}" if master else ""
    if salon:
        who = f"{who} · {salon}" if who else salon
    tail = ", ".join(p for p in (who, when) if p)
    head = service or "—"
    return f"{head} — {tail}" if tail else head


__all__ = ["booking_line", "visit_time_words"]
