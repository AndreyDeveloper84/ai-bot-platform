"""Ссылки персоналу в Mini App салона — вместо телефона клиента в тексте (DRF-2129).

DRF-1039 / OD-W2-2: телефон клиента исполнителю и салону в текстах не
передаётся. Тексту менеджеру нужен путь к записи — им становится день
салона в admin Mini App (``/admin/day?date=…``; телефона там нет по
контракту, ``views_day``), а не номер в сообщении.

Ссылка, а не кнопка ``open_app``: отправитель этих текстов сегодня —
клиентский бот (DRF-2128 переносит их на салонный), а ``web_app`` чужого
бота в кнопке на MAX не проверен. Адрес — у записи реестра салонного бота
(``max_salon``): ``miniapp_url``, иначе ``web_app``; нет ни того ни другого
— ссылки нет, текст тот же.
"""

from __future__ import annotations

from datetime import date
from typing import Any


def _salon_app_base() -> str:
    from apps.channels.bot_registry import SALON_STREAM, effective_registry, resolve_by_stream

    entry: Any = resolve_by_stream(SALON_STREAM, effective_registry())
    if entry is None:
        return ""
    return (getattr(entry, "miniapp_url", "") or getattr(entry, "web_app", "") or "").strip()


def salon_day_link(day: date) -> str:
    """``https://…/admin/day?date=YYYY-MM-DD`` — или пустая строка."""

    base = _salon_app_base()
    if not base:
        return ""
    return f"{base.rstrip('/')}/admin/day?date={day.isoformat()}"


__all__ = ["salon_day_link"]
