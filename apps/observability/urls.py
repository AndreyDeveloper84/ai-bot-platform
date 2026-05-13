"""Observability URL routes (DRF-722 / Sprint 8 / D2)."""

from __future__ import annotations

from django.urls import path

from apps.observability.views import shadow_dashboard

app_name = "observability"

urlpatterns = [
    # Sprint 8 / D2 — staff-only dashboard. Lives under `/admin/observability/`
    # so it shares the Django-admin login flow + nav.
    path("shadow/", shadow_dashboard, name="shadow-dashboard"),
]
