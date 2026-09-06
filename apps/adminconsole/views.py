"""Экран здоровья контура (DRF-1500).

Страница в админке, не система мониторинга: собирает
``apps.adminconsole.health.collect_report`` и рисует его поверх
админского шаблона. Только чтение — флаги отсюда не переключаются
(правило заморозки, ``docs/plans/2026-07-02-AGENT_OPERATING_RULES.md``).

Доступ — любому сотруднику админки (обе роли DRF-1495: экран читает
только агрегаты, персональных данных на нём нет). Проверка та же, что
у соседнего экрана наблюдаемости (``apps.observability.views``): не
staff → явный 403, а не редирект на логин, чтобы пробы доступа видели
отказ как отказ.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from apps.adminconsole.health import collect_report


def contour_health(request: HttpRequest) -> HttpResponse:
    """Сводный экран: свежесть каталога, расхождение зеркала, handoff, флаги."""
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponse("forbidden", status=403)
    report = collect_report()
    return render(
        request,
        "adminconsole/contour_health.html",
        {"report": report, "title": "Здоровье контура"},
    )
