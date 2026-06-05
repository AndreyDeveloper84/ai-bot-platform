"""Observability URL routes (DRF-722 / Sprint 8 / D2 + AI quality Веха 3)."""

from __future__ import annotations

from django.urls import path

from apps.observability.views import shadow_dashboard
from apps.observability.views_ai_quality import ai_quality_dashboard

app_name = "observability"

urlpatterns = [
    # Sprint 8 / D2 — staff-only dashboard. Lives under `/admin/observability/`
    # so it shares the Django-admin login flow + nav.
    path("shadow/", shadow_dashboard, name="shadow-dashboard"),
    # AI observability epic #769 / Веха 3 (#772) — tech-lead verdict 2026-05-26
    # `/ai-quality/` slug matches the EPIC naming + user-facing concept
    # vs implementation-detail «metrics».
    path("ai-quality/", ai_quality_dashboard, name="ai-quality-dashboard"),
]
