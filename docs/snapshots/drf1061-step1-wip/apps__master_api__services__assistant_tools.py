"""What the master's assistant is allowed to look up (DRF-1061 step 1).

Three read-only tools over data the platform already computes. Nothing here
writes; the one write the assistant will ever get — filing a time-off
request — lands in step 3 behind an explicit confirmation tap.

### The master is not an argument

No tool takes a master id. It is resolved from the `BotUser` who is
speaking, before the model is consulted. Were it an argument, «покажи день
Ольги» would be a working request — and the model's arguments are steered
by text a person types. A tool that can name its own subject is a tool that
can be talked into naming someone else's.

### Shapes, not rows

Each tool returns small plain dicts rather than ORM objects. The result is
fed back into a prompt, so every extra field costs tokens and widens what
can leak. Client phone numbers appear in none of them (DRF-1039) — the
salon's own chat surface has held that line since it was built, and a
text channel is a reason to keep it, not to relax it.

### Reading the mirror that has the data

Visits come from `visit_source`, i.e. `RemoteBookingProxy` — the mirror fed
by Ayla events. The local `BookingRequest` table has four rows on the pilot
and a master on none of them (DRF-1085), so a tool reading it would answer
«записей нет» to a master with a full day.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date as date_cls, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Hard ceiling on rows returned to the model. A month of visits in a
#: prompt is tokens spent to make an answer worse.
MAX_ROWS = 40

#: Longest span `my_week` will read. Wider questions are a Mini App job.
MAX_SPAN_DAYS = 31

#: Slot search runs inside the salon's working hours, not the clock's.
#: Pilot has no per-master schedule surfaced here yet; these are the hours
#: «Формула тела» actually works, and a slot proposed at 03:00 would be
#: worse than no answer.
DEFAULT_DAY_START = time(9, 0)
DEFAULT_DAY_END = time(21, 0)

MIN_SLOT_MINUTES = 15


class ToolError(Exception):
    """Bad arguments from the model. Carries text a person can read."""


@dataclass(frozen=True)
class ToolOutcome:
    """What a tool produced, plus the label the transcript records."""

    name: str
    data: dict[str, Any]


def _tz(master) -> ZoneInfo:
    tenant = getattr(master, "tenant", None)
    try:
        return ZoneInfo(getattr(tenant, "timezone", "") or "Europe/Moscow")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Europe/Moscow")


def _parse_date(raw: Any, *, field: str) -> date_cls:
    if isinstance(raw, date_cls) and not isinstance(raw, datetime):
        return raw
    text = str(raw or "").strip()
    if not text:
        raise ToolError(f"{field}: дата не указана")
    try:
        return date_cls.fromisoformat(text[:10])
    except ValueError as exc:
        raise ToolError(f"{field}: не понимаю дату {text!r}") from exc


def _day_bounds(day: date_cls, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), tzinfo=tz)
    return start, start + timedelta(days=1)


def _visit_dict(row, tz: ZoneInfo) -> dict[str, Any]:
    return {
        "time": row.visit_at.astimezone(tz).strftime("%H:%M") if row.visit_at else "",
        "duration_min": row.duration_min,
        "service": row.service_name or "",
        "client": row.client_name or "",
        "status": row.status,
    }


def my_day(master, *, date: Any, now: datetime | None = None) -> dict[str, Any]:
    """Every visit on one day, in the salon's timezone."""

    from apps.master_api.services.visit_source import master_visits

    tz = _tz(master)
    day = _parse_date(date, field="date")
    start, end = _day_bounds(day, tz)

    rows = master_visits(master, start=start, end=end)
    return {
        "date": day.isoformat(),
        "count": len(rows),
        "visits": [_visit_dict(r, tz) for r in rows[:MAX_ROWS]],
        "truncated": len(rows) > MAX_ROWS,
    }


def my_week(master, *, date_from: Any, date_to: Any) -> dict[str, Any]:
    """Per-day counts across a span, plus the total.

    Counts rather than rows: «сколько записей на неделе» is the question,
    and forty lines of detail would answer a different one.
    """

    from apps.master_api.services.visit_source import master_visits

    tz = _tz(master)
    start_day = _parse_date(date_from, field="date_from")
    end_day = _parse_date(date_to, field="date_to")
    if end_day < start_day:
        start_day, end_day = end_day, start_day
    span = (end_day - start_day).days + 1
    if span > MAX_SPAN_DAYS:
        raise ToolError(f"период больше {MAX_SPAN_DAYS} дней — такие сводки смотрят в кабинете")

    start, _ = _day_bounds(start_day, tz)
    _, end = _day_bounds(end_day, tz)
    rows = master_visits(master, start=start, end=end)

    per_day: dict[str, int] = {(start_day + timedelta(days=i)).isoformat(): 0 for i in range(span)}
    for row in rows:
        if row.visit_at is None:
            continue
        key = row.visit_at.astimezone(tz).date().isoformat()
        if key in per_day:
            per_day[key] += 1

    return {
        "date_from": start_day.isoformat(),
        "date_to": end_day.isoformat(),
        "total": len(rows),
        "per_day": per_day,
    }


def free_slots(master, *, date: Any, duration_min: Any = 60) -> dict[str, Any]:
    """Gaps of at least ``duration_min`` inside the working day."""

    from apps.master_api.services.visit_source import occupied_intervals

    tz = _tz(master)
    day = _parse_date(date, field="date")
    try:
        wanted = int(duration_min)
    except (TypeError, ValueError) as exc:
        raise ToolError(f"duration_min: не понимаю длительность {duration_min!r}") from exc
    if wanted < MIN_SLOT_MINUTES:
        wanted = MIN_SLOT_MINUTES

    day_start = datetime.combine(day, DEFAULT_DAY_START, tzinfo=tz)
    day_end = datetime.combine(day, DEFAULT_DAY_END, tzinfo=tz)
    bound_start, bound_end = _day_bounds(day, tz)

    busy = sorted(occupied_intervals(master, day_start=bound_start, day_end=bound_end))

    gaps: list[dict[str, str]] = []
    cursor = day_start
    for busy_start, busy_end in busy:
        local_start = busy_start.astimezone(tz)
        local_end = busy_end.astimezone(tz)
        if local_start > cursor:
            _append_gap(gaps, cursor, min(local_start, day_end), wanted, tz)
        cursor = max(cursor, local_end)
        if cursor >= day_end:
            break
    if cursor < day_end:
        _append_gap(gaps, cursor, day_end, wanted, tz)

    return {
        "date": day.isoformat(),
        "duration_min": wanted,
        "working_hours": f"{DEFAULT_DAY_START:%H:%M}–{DEFAULT_DAY_END:%H:%M}",
        "slots": gaps[:MAX_ROWS],
        "count": len(gaps),
    }


def _append_gap(
    gaps: list[dict[str, str]],
    start: datetime,
    end: datetime,
    wanted: int,
    tz: ZoneInfo,
) -> None:
    if (end - start) >= timedelta(minutes=wanted):
        gaps.append(
            {
                "from": start.astimezone(tz).strftime("%H:%M"),
                "to": end.astimezone(tz).strftime("%H:%M"),
            }
        )


#: Tool specs in the platform-canonical shape (`{name, description,
#: parameters}`) — the same one `apps/llm/providers` maps to each vendor.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "my_day",
        "description": (
            "Записи мастера на конкретный день: время, услуга, имя клиента. "
            "Используй для вопросов «что у меня сегодня / завтра / в четверг»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Дата в формате ГГГГ-ММ-ДД.",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "my_week",
        "description": (
            "Сколько записей у мастера по дням за период. Для вопросов "
            "«сколько записей на неделе», «загружен ли я в выходные»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Начало, ГГГГ-ММ-ДД."},
                "date_to": {"type": "string", "description": "Конец, ГГГГ-ММ-ДД."},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "free_slots",
        "description": (
            "Свободные окна мастера в рабочем дне под нужную длительность. "
            "Для вопросов «когда у меня окно на два часа»."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Дата, ГГГГ-ММ-ДД."},
                "duration_min": {
                    "type": "integer",
                    "description": "Нужная длительность в минутах. По умолчанию 60.",
                },
            },
            "required": ["date"],
        },
    },
]

#: Typed as a plain callable map: the three signatures differ by keyword,
#: and mypy otherwise infers the dict's value type from the first entry.
_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "my_day": my_day,
    "my_week": my_week,
    "free_slots": free_slots,
}


def run_tool(name: str, arguments: dict[str, Any], *, master) -> ToolOutcome:
    """Execute one tool for THIS master.

    ``master`` is keyword-only and supplied by the caller — never taken
    from ``arguments``, which the model controls.
    """

    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"неизвестный инструмент {name!r}")
    args = {k: v for k, v in (arguments or {}).items() if k != "master"}
    return ToolOutcome(name=name, data=handler(master, **args))


__all__ = [
    "MAX_ROWS",
    "MAX_SPAN_DAYS",
    "TOOL_SPECS",
    "ToolError",
    "ToolOutcome",
    "free_slots",
    "my_day",
    "my_week",
    "run_tool",
]
