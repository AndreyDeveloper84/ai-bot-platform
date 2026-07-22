"""Tenant context middleware — resolves X-Tenant header to a Tenant instance
and sets ``apps.tenancy.context._TENANT`` for the duration of the request
(DRF-418 / A3).

Ported from ``Ayla origin/dev:users/middleware.py::TenantContextMiddleware``
(blob ``c84ee7ca``). Adapted to use the platform's ContextVar primitives
(``apps.tenancy.context``) instead of stashing the tenant on
``request.tenant`` alone. Both layers are populated for backwards
compatibility: code reading ``request.tenant`` keeps working, code reading
``current_tenant()`` works inside async / Celery / Redis-Streams workers
where ``request`` doesn't exist.

Strict-mode rollout (per ADR-0001, review revision 5C):
- ``STRICT_TENANT_SCOPE="audit"``  → tolerance: missing/unknown header
  routes to ``request.tenant=None``. Audit log records the miss for
  later analysis.
- ``STRICT_TENANT_SCOPE="strict"`` → fail-fast: missing/unknown header
  on a non-excluded ``/api/v1/*`` path returns 400 TENANT_REQUIRED
  before the view runs.
- ``STRICT_TENANT_SCOPE="off"``    → no tenant resolution attempted
  (kept for the rare environment where multi-tenancy is fully disabled).
- Default value is ``"audit"`` in prod/staging settings; ``tests/conftest.py``
  flips it to ``"strict"`` so unit tests fail-fast on missing context.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.tenancy.context import tenant_scope

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant


logger = logging.getLogger(__name__)

# Paths the middleware skips entirely — no tenant lookup, no strict check.
# These surfaces either have no tenant concept (admin/static/health) or
# resolve tenancy on their own (service-to-service paths).
EXCLUDED_PATH_PREFIXES = (
    "/admin",
    "/api/schema/",
    "/api/docs/",
    "/api/redoc/",
    "/healthz",
    "/readyz",
    "/static/",
    "/media/",
)

# Paths that REQUIRE a tenant header when STRICT_TENANT_SCOPE="strict".
# Opt-outs are paths that resolve tenancy from a non-header source
# (channel-token mapping in ingress, registration handshake in auth).
STRICT_REQUIRED_PREFIXES = ("/api/v1/",)
STRICT_OPT_OUT_PREFIXES = (
    "/api/v1/auth/",
    # Sprint 2 / D4: webhook receivers resolve tenant from
    # channel-token → tenant mapping (apps.ingress.services._resolve_tenant)
    # before any view code runs. The X-Tenant header is never present.
    "/api/v1/ingress/",
    # Phase 1 / CH1 (DRF-848): Telegram webhook resolves tenant from
    # the URL path slug (and authenticates via the
    # X-Telegram-Bot-Api-Secret-Token header). The X-Tenant header is
    # never present on inbound Telegram updates.
    "/api/v1/channels/telegram/",
    # Customer Mini App API: tenant resolved from verified initData →
    # BotUser → tenant inside @require_init_data.
    "/api/v1/customer/",
    # Admin REST API (PR 2 / MM1-MM3): tenant resolved from verified
    # initData → BotUser → tenant inside @require_admin_role. The
    # X-Tenant header is never sent. (Master surface /api/v1/master/
    # follows the same pattern but is opted in to strict mode for
    # parity with PR 1 — left as-is to keep PR scope tight.)
    "/api/v1/admin/",
    # #732 (PRE_PILOT) — temporary 410 Gone for retired YooKassa
    # webhook. YooKassa has no X-Tenant header (external webhook).
    # Without this opt-out the strict-mode flip (2026-05-28 per
    # project_strict_tenant_refuse_soak) would return 400
    # TENANT_REQUIRED before the 410 view runs, defeating the
    # forensic-capture design. Removed by the cleanup PR alongside
    # the apps.integrations.yookassa_retired package.
    "/api/v1/yookassa/",
    # Cross-service event ingest (ADR-0009): the Ayla outbox publisher
    # never sends X-Tenant BY DESIGN — tenancy is per-event inside the
    # envelope and resolved by apps.eventbus.ingest_tenancy (fail-closed
    # when tenant_id mismatches user_id). The endpoint is multi-tenant
    # and carries its own security stack (HMAC-SHA256 + timestamp
    # anti-replay, rate limit, IP allowlist) — the same ingress pattern
    # as /api/v1/ingress/ above. Staging round-trip finding: without this
    # opt-out strict_block answered 400 TENANT_REQUIRED to every
    # legitimate booking.*/payment.*/billing.* delivery.
    "/api/v1/internal/events/",
)

# Tri-value setting (audit | strict | off). Default audit per ADR-0001.
VALID_SCOPE_MODES = ("audit", "strict", "off")


def _scope_mode() -> str:
    """Read the current scope mode, validate, default to audit."""

    mode = str(getattr(settings, "STRICT_TENANT_SCOPE", "audit")).lower()
    if mode not in VALID_SCOPE_MODES:
        logger.warning(
            "tenant.middleware.invalid_scope_mode mode=%r — defaulting to audit",
            mode,
        )
        return "audit"
    return mode


def _is_excluded(path: str) -> bool:
    return any(path.startswith(p) for p in EXCLUDED_PATH_PREFIXES)


def _strict_required(path: str) -> bool:
    """True if path falls under strict-mode enforcement (and is not opted out)."""

    if not any(path.startswith(p) for p in STRICT_REQUIRED_PREFIXES):
        return False
    if any(path.startswith(p) for p in STRICT_OPT_OUT_PREFIXES):
        return False
    return True


def _resolve_tenant(slug: str) -> "Tenant | None":
    """Look up the active Tenant for a slug, or None if unknown/inactive."""

    if not slug:
        return None

    # Local import — middleware loads during settings bootstrap, before
    # INSTALLED_APPS is fully ready.
    from apps.tenancy.models import Tenant

    try:
        return Tenant.objects.get(slug=slug)
    except Tenant.DoesNotExist:
        return None


class TenantContextMiddleware:
    """Django middleware: header → Tenant → ``request.tenant`` + ContextVar.

    Installed in ``MIDDLEWARE`` (config/settings/base.py). Runs once per
    request. Always pairs ``set_tenant`` and ``reset_tenant`` via
    ``tenant_scope()`` — the try/finally is what stops tenant leak across
    requests when the WSGI/ASGI server reuses threads.
    """

    HEADER_KEY = "HTTP_X_TENANT"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.tenant = None  # type: ignore[attr-defined]

        path = request.path
        mode = _scope_mode()

        if _is_excluded(path) or mode == "off":
            with tenant_scope(None):
                return self.get_response(request)

        slug = (request.META.get(self.HEADER_KEY) or "").strip()
        tenant = _resolve_tenant(slug)

        if tenant is None:
            if mode == "strict" and _strict_required(path):
                logger.info(
                    "tenant.middleware.strict_block path=%s slug=%r",
                    path,
                    slug or "<missing>",
                )
                return JsonResponse(
                    {
                        "error": {
                            "code": "TENANT_REQUIRED",
                            "message": "Valid X-Tenant header required.",
                        },
                    },
                    status=400,
                )
            if slug:  # audit-mode: only log when header was present but unresolvable
                logger.info(
                    "tenant.middleware.unknown_slug slug=%r path=%s mode=%s",
                    slug,
                    path,
                    mode,
                )

        request.tenant = tenant  # type: ignore[attr-defined]
        with tenant_scope(tenant):
            return self.get_response(request)
