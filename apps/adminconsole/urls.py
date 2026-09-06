"""Маршруты экрана здоровья контура (DRF-1500).

Монтируется в ``config/urls.py`` под ``/admin/health/`` до catch-all
админки — тот же приём, что у ``/admin/observability/`` (DRF-722):
экран живёт в админском логине и навигации, не выносясь наружу.
"""

from __future__ import annotations

from django.urls import path

from apps.adminconsole.views import contour_health

app_name = "adminconsole"

urlpatterns = [
    path("", contour_health, name="contour-health"),
]
