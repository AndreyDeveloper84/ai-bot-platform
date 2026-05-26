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
    # #428 (Bucket 6) — YooKassa webhook RETIRED. Per ADR-0009 §Domain
    # ownership matrix, YooKassa payment lifecycle (create, capture,
    # refund, webhook) lives in Ayla djangoproject only. YooKassa
    # Personal Cabinet webhook URL was switched to Ayla before this
    # PR merged (see PR body Q3 preconditions). bot-platform no
    # longer terminates YooKassa traffic.
    # Phase 0 / #432 (ADR-0009 §Mandatory event contract) — internal
    # events ingest channel from Ayla djangoproject. Stub today
    # (501 Not Implemented); full per-event dispatch arrives with
    # Beta #441 (event-contract.md) + Gamma #442-#446 consumers.
    path(
        "api/v1/internal/events/",
        include("apps.eventbus.urls", namespace="eventbus_internal"),
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
    # PR 1.5 / ADR-0008 — unified identity surface. Today carries the
    # /api/v1/me endpoint used by every Mini App on launch to learn the
    # caller's role + capabilities. Mounted at the bare /api/v1/ prefix
    # because /me is shared across customer / master / admin surfaces.
    path(
        "api/v1/",
        include("apps.identity.urls", namespace="identity"),
    ),
    # Master Mini App API (PR 1 / M0 onboarding) — claim-invite, accept,
    # reject, profile init, /me. Init-data verified on every call;
    # tenant resolved via the linked BotUser. See
    # ``docs/design/handoffs/2026-05-18-master-mobile-handoff.md`` §M0.
    path(
        "api/v1/master/",
        include("apps.master_api.urls", namespace="master_api"),
    ),
    # Admin REST API (PR 2 / MM1-MM3) — owner/admin master roster CRUD.
    # Role-gated via apps.admin_api.auth.require_admin_role (owner OR
    # admin; receptionist/master/customer → 403). See
    # ``docs/design/handoffs/2026-05-18-master-management-handoff.md``.
    path(
        "api/v1/admin/",
        include("apps.admin_api.urls", namespace="admin_api"),
    ),
    # Master ↔ Admin internal chat (PR 6 / handoff 2026-05-19). Production
    # blocker for earnings disputes / leave requests / review concerns /
    # substitution / offboarding tracks. Two parallel surfaces:
    #   /master/... — gated by master init-data decorator.
    #   /admin/... — gated by admin-role decorator.
    path(
        "api/v1/internal-chat/",
        include("apps.internal_chat.urls", namespace="internal_chat"),
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
