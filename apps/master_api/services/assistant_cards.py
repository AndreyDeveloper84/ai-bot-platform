"""Карточки Ayla для мастера — структура вместо абзаца (DRF-2153, макет DRF-1187).

Макет: ответ о свободном времени — строки окон с «Создать запись»; день —
строки записей; «Не удалось проверить актуальное расписание» —
последние известные данные + «Проверить снова». Карточки собираются ИЗ
ДАННЫХ инструмента (``AssistantReply.tool_data``), не из текста модели:
текст короткий и может быть любым, карточка — всегда по факту.

Телефона клиента здесь нет ни в одном поле по построению (DRF-1039):
имя — «Анна П.» через :func:`~apps.master_api.services.bookings.name_initial`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlencode

from apps.master_api.services.bookings import name_initial

#: Четыре подсказки макета — фраза и подзаголовок; чип = отправка фразы.
CHIPS: list[dict[str, str]] = [
    {"text": "Что у меня сегодня?", "hint": "Покажите расписание дня"},
    {"text": "Когда я свободен завтра?", "hint": "Свободные окна на завтра"},
    {"text": "Добавить запись", "hint": "Создать новую запись в расписании"},
    {"text": "Изменить рабочий день", "hint": "Изменить график или недоступность"},
]

#: Тексты макета для «данные могли устареть» — дословно.
STALE_NOTICE = "Не удалось проверить актуальное расписание. Последние известные данные"
STALE_FOOTER = "Расписание могло измениться."
RECHECK_LABEL = "Проверить снова"


def _base(master: Any) -> str:
    """Поверхность мастера по типу кабинета: соло — ``/solo``, салон — ``/master``."""

    from apps.identity.services.solo_onboarding import is_solo_provider

    tenant = getattr(master, "tenant", None)
    return "/solo" if tenant is not None and is_solo_provider(tenant) else "/master"


def booking_form_url(master: Any, *, date: str = "", start: str = "", end: str = "") -> str:
    """Дверь в «Новую запись» (М-3); с окном — «Выбранное окно: 14:00–17:00»."""

    query: dict[str, str] = {}
    if date:
        query["date"] = date
    if start and end:
        query["from"] = start
        query["to"] = end
    return f"{_base(master)}/booking/new" + (f"?{urlencode(query)}" if query else "")


def booking_detail_url(master: Any, appointment_id: str) -> str:
    """Дверь в обычный экран деталей (М-4), не карточку внутри Ayla."""

    return f"{_base(master)}/bookings/{appointment_id}"


def client_option_label(row: dict[str, Any]) -> str:
    """«Анна П. · была 12.05» / «Анна П. · новый клиент» — различитель владельца."""

    last = row.get("last_visit_date")
    if last:
        y, m, d = str(last).split("-")
        return f"{row['name']} · была {d}.{m}"
    return f"{row['name']} · новый клиент"


def service_options(services: Iterable[Any]) -> list[dict[str, Any]]:
    return [
        {
            "service_id": str(s.id),
            "name": s.name,
            "duration_min": int(getattr(s, "duration_min", 0) or 0),
        }
        for s in services
    ]


def visit_card_rows(rows: Iterable[Any], tz) -> list[dict[str, Any]]:
    """Строки дня: время · клиент «Анна П.» · услуга · длительность."""

    out: list[dict[str, Any]] = []
    for r in rows:
        start = getattr(r, "visit_at", None)
        out.append(
            {
                "time": start.astimezone(tz).strftime("%H:%M") if start else "",
                "client": name_initial(getattr(r, "client_name", "") or ""),
                "service": getattr(r, "service_name", "") or "",
                "duration_min": int(getattr(r, "duration_min", 0) or 0),
            }
        )
    return out


def cards_for_tool(
    master: Any, tool_name: str, data: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Карточки по данным инструмента: ``free_slots`` → окна, ``my_day`` → день."""

    if not data:
        return []
    if tool_name == "free_slots":
        day = str(data.get("date") or "")
        stale = bool(data.get("stale"))
        windows = [
            {
                "start": g["from"],
                "end": g["to"],
                "book_url": booking_form_url(master, date=day, start=g["from"], end=g["to"]),
            }
            for g in data.get("slots") or []
        ]
        return [
            {
                "kind": "free_windows",
                "date": day,
                "day_off": bool(data.get("day_off")),
                "stale": stale,
                "notice": STALE_NOTICE if stale else None,
                "footer": STALE_FOOTER if stale else None,
                "recheck": RECHECK_LABEL if stale else None,
                "windows": windows,
                "book_url": booking_form_url(master, date=day),
            }
        ]
    if tool_name == "my_day":
        visits = data.get("visits") or []
        return [
            {
                "kind": "day",
                "date": str(data.get("date") or ""),
                "count": int(data.get("count") or 0),
                "visits": [
                    {
                        "time": str(v.get("time") or ""),
                        "client": name_initial(v.get("client") or ""),
                        "service": str(v.get("service") or ""),
                        "duration_min": int(v.get("duration_min") or 0),
                    }
                    for v in visits
                ],
            }
        ]
    return []


def today_context(master: Any, *, now: datetime) -> dict[str, Any]:
    """Контекст дня для стартового экрана: «Сегодня N записей · Следующая — …»."""

    from apps.master_api.services.dashboard import get_next_visit, get_tenant_tz
    from apps.master_api.services.visit_source import master_visits

    tz = get_tenant_tz(master.tenant)
    local = now.astimezone(tz)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start.replace(hour=23, minute=59, second=59)
    rows = master_visits(master, start=day_start, end=day_end)
    nxt = get_next_visit(master, now)
    next_payload = None
    if nxt is not None:
        next_payload = {
            "client_name_initial": name_initial(
                f"{getattr(nxt, 'client_first_name', '')} {getattr(nxt, 'client_last_initial', '')}".strip()
            ),
            "time": datetime.fromisoformat(nxt.visit_at).astimezone(tz).strftime("%H:%M"),
            "service_name": getattr(nxt, "service_name", "") or "",
            "duration_min": int(getattr(nxt, "duration_min", 0) or 0),
        }
    return {
        "date": local.date().isoformat(),
        "count": len(rows),
        "next": next_payload,
    }


__all__ = [
    "CHIPS",
    "RECHECK_LABEL",
    "STALE_FOOTER",
    "STALE_NOTICE",
    "booking_detail_url",
    "booking_form_url",
    "cards_for_tool",
    "client_option_label",
    "service_options",
    "today_context",
    "visit_card_rows",
]
