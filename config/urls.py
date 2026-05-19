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
    # Phase 1 / B7 (DRF-843) — YooKassa hosted-checkout webhook receiver.
    path(
        "api/v1/yookassa/",
        include("apps.integrations.yookassa.urls", namespace="yookassa"),
    ),
    # Phase 1 / CH1 (DRF-848) — Telegram channel adapter webhook.
    # Tenant resolution happens from the URL slug, not the X-Tenant
    # header (Telegram has no equivalent). The view authenticates with
    # the X-Telegram-Bot-Api-Secret-Token header against the tenant's
    # ``telegram_webhook_secret`` field. See
    # ``docs/runbooks/telegram-bot-onboarding.md``.
    path(
        "api/v1/channels/telegram/",
        include("apps.channels.telegram.urls", namespace="telegram"),
    ),
    # Customer Mini App API (Phase 0b+).
    path(
        "api/v1/customer/",
        include("apps.miniapp_api.urls", namespace="miniapp_api"),
    ),
    # Phase 5 / KB-SYNC — Shiro-Py salon-knowledge consumer.
    # POST /api/v1/salon-knowledge/webhook/approved/ receives
    # ``knowledge.approved`` events from the colleague's service and
    # writes them as ``KbDocument`` rows in the matching tenant. HMAC
    # gate uses ``settings.SALON_KNOWLEDGE_WEBHOOK_SECRET``. See
    # ``apps/kb/webhooks.py`` + the architectural decision in
    # ``docs/design/2026-05-18-phase-5-architecture-comparison.md``.
    path(
        "api/v1/salon-knowledge/",
        include("apps.kb.urls", namespace="salon_knowledge_webhooks"),
    ),
]
