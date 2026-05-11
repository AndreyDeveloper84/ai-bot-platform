"""Root URL configuration.

Sprint 0 / A1: only Django admin. Health endpoint and per-app routes land in
Sprint 1 (orchestrator) and Sprint 4 (channels/ingress).
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("apps.orchestrator.urls")),
]
