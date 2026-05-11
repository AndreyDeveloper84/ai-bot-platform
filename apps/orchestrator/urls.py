"""URL routes for orchestrator surface (DRF-431 / E2).

Sprint 1 ships health/readiness endpoints. Real orchestrator routes
(pipeline, message handler) land sprint-by-sprint.
"""

from __future__ import annotations

from django.urls import path

from apps.orchestrator import views

urlpatterns = [
    path("healthz/", views.healthz, name="healthz"),
    path("readyz/", views.readyz, name="readyz"),
]
