"""Права мастера в ``/me`` — из фактов, не константы (DRF-1805, M13).

До этого ``permissions`` в ``/me`` были захардкожены ``True`` («PR 1
hardcodes all three»), и ``can_edit_services`` обещал возможность, ручки
для которой нет (M10 ещё не построен): экран, поверивший флагу, нарисовал
бы кнопку в никуда.

Факт здесь — **проводка**: право есть ровно тогда, когда в URLconf
``master_api`` стоит именованный маршрут, который это действие принимает.
M10 добавит маршрут ``services_write`` — и флаг станет истиной без правки
``/me``; снять маршрут — флаг погаснет. Второго списка «что умеет кабинет»
нет: ``django.urls.reverse`` читает тот же URLconf, что и запрос.

Права роли/тенанта (кто вообще может править расписание в салоне) — не
этот срез; здесь только «есть ли куда нажать».
"""

from __future__ import annotations

from django.urls import NoReverseMatch, reverse

#: Право → имя маршрута ``master_api``, который его принимает.
PERMISSION_ROUTES: dict[str, str] = {
    # Расписание правится через заявку «помечу как недоступно» (M3).
    "can_edit_schedule": "master_api:availability_request",
    # Услуги и цены — ручка M10; пока её нет, право ложно.
    "can_edit_services": "master_api:services_write",
    # Ответ клиенту из кабинета (M5).
    "can_message_customers": "master_api:conversation_send_message",
}


def route_exists(name: str) -> bool:
    """Есть ли именованный маршрут в текущем URLconf (любой набор аргументов)."""
    for args in ((), ("00000000-0000-0000-0000-000000000000",)):
        try:
            reverse(name, args=args)
            return True
        except NoReverseMatch:
            continue
    return False


def permissions_from_facts() -> dict[str, bool]:
    """Блок ``permissions`` для ``/me``: право = проводка существует."""
    return {key: route_exists(route) for key, route in PERMISSION_ROUTES.items()}
