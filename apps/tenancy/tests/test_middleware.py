"""Tests for TenantContextMiddleware (DRF-418 / A3).

Ported from `Ayla origin/dev:tenants/tests/test_middleware_and_permission.py`
(blob `65ab8cc4`), middleware section only. Extended with tri-value
STRICT_TENANT_SCOPE coverage (audit / strict / off) and ContextVar
correctness checks.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from apps.tenancy.context import current_tenant
from apps.tenancy.middleware import TenantContextMiddleware
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _seen_tenant() -> "list[Tenant | None]":
    """A list whose only element will be the tenant visible to the view.

    Mutable list lets the response-func close over a single slot — gives
    us a clean read-back of the tenant inside the request scope.
    """
    return []


def _call(request, response_func=None):
    """Drive the middleware over a request with a downstream view that
    captures ``current_tenant()`` at the point the view would run.
    """

    captured = _seen_tenant()

    def default_view(_request):
        captured.append(current_tenant())
        return ("ok", current_tenant())

    mw = TenantContextMiddleware(get_response=response_func or default_view)
    response = mw(request)
    return response, captured


# ---------------------------------------------------------------------------
# Header → tenant resolution
# ---------------------------------------------------------------------------


class TestHeaderResolution:
    def test_no_header_leaves_tenant_none(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        request = RequestFactory().get("/api/v1/specialists/")
        _call(request)
        assert request.tenant is None

    def test_known_slug_resolves_to_tenant(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        t = Tenant.objects.create(slug="formula", name="Формула тела")
        request = RequestFactory().get(
            "/api/v1/specialists/",
            HTTP_X_TENANT="formula",
        )
        _, captured = _call(request)
        assert request.tenant is not None
        assert request.tenant.id == t.id
        # ContextVar is set during the view, reset after.
        assert captured == [t]

    def test_unknown_slug_falls_back_to_none(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        request = RequestFactory().get(
            "/api/v1/specialists/",
            HTTP_X_TENANT="ghost",
        )
        _call(request)
        assert request.tenant is None

    def test_inactive_tenant_treated_as_unknown(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        Tenant.objects.create(slug="dead", name="D", is_active=False)
        request = RequestFactory().get(
            "/api/v1/specialists/",
            HTTP_X_TENANT="dead",
        )
        _call(request)
        assert request.tenant is None

    def test_empty_header_treated_as_missing(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        request = RequestFactory().get("/api/v1/specialists/", HTTP_X_TENANT="")
        _call(request)
        assert request.tenant is None


# ---------------------------------------------------------------------------
# Excluded paths
# ---------------------------------------------------------------------------


class TestExcludedPaths:
    def test_admin_path_skips_resolution(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"  # even strict mode skips admin
        Tenant.objects.create(slug="formula", name="X")
        request = RequestFactory().get("/admin/login/", HTTP_X_TENANT="formula")
        response, _ = _call(request)
        # Strict mode would 400 on /api/v1/*; /admin is excluded.
        assert request.tenant is None
        assert isinstance(response, tuple)  # downstream view ran

    def test_healthz_excluded(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        request = RequestFactory().get("/healthz/")
        response, _ = _call(request)
        assert isinstance(response, tuple)
        assert request.tenant is None

    def test_readyz_excluded(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        request = RequestFactory().get("/readyz/")
        response, _ = _call(request)
        assert isinstance(response, tuple)


# ---------------------------------------------------------------------------
# Tri-value STRICT_TENANT_SCOPE
# ---------------------------------------------------------------------------


class TestStrictMode:
    def test_strict_missing_header_returns_400(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        downstream_called: list[bool] = []

        def downstream(_):
            downstream_called.append(True)
            return ("ok", None)

        request = RequestFactory().get("/api/v1/specialists/")
        response, _ = _call(request, response_func=downstream)
        assert downstream_called == []  # view never ran
        assert response.status_code == 400
        assert b"TENANT_REQUIRED" in response.content

    def test_strict_unknown_slug_returns_400(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        request = RequestFactory().get("/api/v1/specialists/", HTTP_X_TENANT="ghost")
        response, _ = _call(request)
        assert response.status_code == 400

    def test_strict_known_slug_passes_through(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        Tenant.objects.create(slug="formula", name="F")
        request = RequestFactory().get(
            "/api/v1/specialists/",
            HTTP_X_TENANT="formula",
        )
        response, captured = _call(request)
        assert request.tenant is not None
        assert captured and captured[0] is not None
        assert isinstance(response, tuple)

    def test_strict_auth_path_opt_out(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        request = RequestFactory().post("/api/v1/auth/login/")
        response, _ = _call(request)
        # /api/v1/auth/* is pre-tenant — registration must pass without header.
        assert isinstance(response, tuple)


class TestAuditMode:
    def test_audit_missing_header_passes(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        request = RequestFactory().get("/api/v1/specialists/")
        response, _ = _call(request)
        assert isinstance(response, tuple)
        assert request.tenant is None


class TestOffMode:
    def test_off_skips_resolution_entirely(self, settings):
        settings.STRICT_TENANT_SCOPE = "off"
        Tenant.objects.create(slug="formula", name="X")
        # Even with a valid header, off-mode does not resolve.
        request = RequestFactory().get(
            "/api/v1/specialists/",
            HTTP_X_TENANT="formula",
        )
        response, captured = _call(request)
        assert isinstance(response, tuple)
        assert request.tenant is None
        assert captured == [None]


# ---------------------------------------------------------------------------
# ContextVar reset correctness — leak across requests
# ---------------------------------------------------------------------------


class TestContextVarReset:
    def test_tenant_resets_after_request(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        Tenant.objects.create(slug="formula", name="F")

        # Before the request: no tenant.
        assert current_tenant() is None

        request = RequestFactory().get(
            "/api/v1/specialists/",
            HTTP_X_TENANT="formula",
        )
        _, captured = _call(request)

        # Inside the view: tenant was set.
        assert captured[0] is not None
        assert captured[0].slug == "formula"

        # After the middleware returns: context restored to None (no leak).
        assert current_tenant() is None

    def test_two_requests_dont_leak(self, settings):
        settings.STRICT_TENANT_SCOPE = "audit"
        t1 = Tenant.objects.create(slug="t1", name="T1")
        Tenant.objects.create(slug="t2", name="T2")

        r1 = RequestFactory().get("/api/v1/x/", HTTP_X_TENANT="t1")
        r2 = RequestFactory().get("/api/v1/x/", HTTP_X_TENANT="t2")

        _, captured1 = _call(r1)
        _, captured2 = _call(r2)

        assert captured1[0].slug == "t1"
        assert captured2[0].slug == "t2"
        assert current_tenant() is None
        # Sanity: r1's tenant didn't bleed into r2 (would happen with
        # threading.local under thread reuse).
        assert r1.tenant.id == t1.id
        assert r2.tenant.slug == "t2"
