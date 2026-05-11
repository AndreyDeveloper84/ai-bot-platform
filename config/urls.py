"""Root URL configuration.

Sprint 0 / A1: only Django admin.
Sprint 1: orchestrator (/healthz/, /readyz/).
Sprint 2 / D4: ingress webhook routes (/api/v1/ingress/<channel>/).
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.orchestrator.urls")),
    path("api/v1/ingress/", include("apps.ingress.urls", namespace="ingress")),
]
