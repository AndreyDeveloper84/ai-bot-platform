"""Root URL configuration.

Sprint 0 / A1: only Django admin.
Sprint 1: orchestrator (/healthz/, /readyz/).
Sprint 2 / D4: ingress webhook routes (/api/v1/ingress/<channel>/).
Sprint 10 / C3: catalog webhook receiver (/api/v1/catalog/webhook/).
Phase 1 / B2: YClients admin webhook (/api/v1/yclients/webhook/).
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    # Sprint 8 / D2 (DRF-722) — observability dashboard mounted under
    # /admin/observability/. Must be registered BEFORE the Django admin
    # urlpatterns so the specific prefix wins over admin's catch-all
    # include (Django matches patterns in declared order).
    path("admin/observability/", include("apps.observability.urls", namespace="observability")),
    path("admin/", admin.site.urls),
    path("", include("apps.orchestrator.urls")),
    path("api/v1/ingress/", include("apps.ingress.urls", namespace="ingress")),
    path(
        "api/v1/catalog/",
        include("apps.catalog.webhooks.urls", namespace="catalog_webhooks"),
    ),
    # Phase 1 / B2 (DRF-838) — YClients admin-side webhook port.
    path(
        "api/v1/yclients/",
        include("apps.integrations.yclients.urls", namespace="yclients"),
    ),
]
