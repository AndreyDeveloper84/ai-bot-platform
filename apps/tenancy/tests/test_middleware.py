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


class TestInternalEventsOptOut:
    """Staging round-trip finding: the cross-service event ingest must
    not be strict-blocked — Ayla's publisher never sends X-Tenant by
    design (tenancy lives per-event in the envelope and is enforced by
    apps.eventbus.ingest_tenancy). The middleware carries NO security
    function on this path; HMAC/timestamp/rate-limit/IP live in the
    view (fail-closed preserved: no signature → 401 there)."""

    def test_ingest_without_header_passes_tenant_gate(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        downstream_called: list[bool] = []

        def downstream(_):
            downstream_called.append(True)
            return ("ok", None)

        request = RequestFactory().post("/api/v1/internal/events/ingest")
        response, _ = _call(request, response_func=downstream)
        # Gate passes: downstream (the view) runs and answers 401 on its
        # own HMAC check — middleware must NOT pre-empt with TENANT_REQUIRED.
        assert downstream_called == [True]
        assert isinstance(response, tuple)

    def test_ingest_with_valid_header_still_resolves(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        Tenant.objects.create(slug="formula", name="F")
        request = RequestFactory().post(
            "/api/v1/internal/events/ingest",
            HTTP_X_TENANT="formula",
        )
        response, captured = _call(request)
        # Opting out of the REQUIREMENT doesn't break header resolution
        # when the header IS present.
        assert request.tenant is not None
        assert captured and captured[0] is not None
        assert isinstance(response, tuple)

    def test_other_internal_paths_still_require_tenant(self, settings):
        """Regression: the opt-out is scoped to /api/v1/internal/events/
        only — sibling internal paths keep the strict requirement."""
        settings.STRICT_TENANT_SCOPE = "strict"
        request = RequestFactory().post("/api/v1/internal/users/123/personal-context/")
        response, _ = _call(request)
        assert response.status_code == 400
        assert b"TENANT_REQUIRED" in response.content


class TestMasterAndIdentityOptOut:
    """DRF-1104 — the master surface and /api/v1/me must not be strict-blocked.

    Both resolve the tenant from verified initData → BotUser, exactly like
    /api/v1/customer/ and /api/v1/admin/ which were already opted out. The
    master surface was left opted-IN "to keep PR scope tight", which meant
    every master endpoint answered 400 TENANT_REQUIRED before any view ran:
    no master could reach a master screen, and /api/v1/me never returned a
    role, so every Mini App fell back to the customer surface.
    """

    def test_master_surface_opt_out(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        request = RequestFactory().get("/api/v1/master/me")
        response, _ = _call(request)
        assert isinstance(response, tuple)

    def test_master_nested_paths_opt_out(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        for path in (
            "/api/v1/master/dashboard",
            "/api/v1/master/schedule",
            "/api/v1/master/onboarding/accept",
        ):
            response, _ = _call(RequestFactory().get(path))
            assert isinstance(response, tuple), path

    def test_identity_me_opt_out(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        request = RequestFactory().get("/api/v1/me")
        response, _ = _call(request)
        assert isinstance(response, tuple)

    def test_identity_me_opt_out_tolerates_trailing_slash(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        response, _ = _call(RequestFactory().get("/api/v1/me/"))
        assert isinstance(response, tuple)

    def test_me_opt_out_is_exact_not_a_prefix(self, settings):
        """Regression: "/api/v1/me" must not exempt /api/v1/me*.

        Declared as an exact path precisely so a future /api/v1/media/ or
        /api/v1/messages/ does not inherit the exemption by accident.
        """
        settings.STRICT_TENANT_SCOPE = "strict"
        for path in ("/api/v1/media/upload", "/api/v1/messages/42", "/api/v1/members"):
            response, _ = _call(RequestFactory().get(path))
            assert response.status_code == 400, path
            assert b"TENANT_REQUIRED" in response.content

    def test_sibling_surfaces_still_require_tenant(self, settings):
        """The exemption is scoped — unrelated /api/v1/ paths are unchanged."""
        settings.STRICT_TENANT_SCOPE = "strict"
        response, _ = _call(RequestFactory().get("/api/v1/specialists/"))
        assert response.status_code == 400
        assert b"TENANT_REQUIRED" in response.content

    def test_header_still_resolves_when_present_on_master(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        Tenant.objects.create(slug="formula", name="F")
        request = RequestFactory().get("/api/v1/master/me", HTTP_X_TENANT="formula")
        response, captured = _call(request)
        # Opting out of the REQUIREMENT must not break header resolution.
        assert request.tenant is not None
        assert captured and captured[0] is not None
        assert isinstance(response, tuple)


class TestInternalChatOptOut:
    """DRF-1113 — the master↔admin internal chat must not be strict-blocked.

    Same shape as the master surface above: tenancy comes from verified
    initData inside @require_master_init_data / @require_admin_role, and
    the Mini App never sends X-Tenant. Measured on the pilot 2026-08-16,
    every internal-chat endpoint answered 400 TENANT_REQUIRED before any
    view ran, so /admin/internal-chat and /admin/chats were dead screens.
    """

    def test_admin_threads_opt_out(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        response, _ = _call(RequestFactory().get("/api/v1/internal-chat/admin/threads/"))
        assert isinstance(response, tuple)

    def test_master_threads_opt_out(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        response, _ = _call(RequestFactory().get("/api/v1/internal-chat/master/threads/"))
        assert isinstance(response, tuple)

    def test_nested_internal_chat_paths_opt_out(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        for path in (
            "/api/v1/internal-chat/admin/threads/abc/",
            "/api/v1/internal-chat/admin/threads/abc/messages/",
            "/api/v1/internal-chat/admin/threads/abc/close/",
            "/api/v1/internal-chat/master/threads/abc/escalate-to-founder/",
        ):
            response, _ = _call(RequestFactory().get(path))
            assert isinstance(response, tuple), path

    def test_lookalike_prefix_still_requires_tenant(self, settings):
        """The exemption is scoped to the hyphenated prefix, nothing near it."""
        settings.STRICT_TENANT_SCOPE = "strict"
        for path in ("/api/v1/internal-chats/", "/api/v1/internal/chat/"):
            response, _ = _call(RequestFactory().get(path))
            assert response.status_code == 400, path
            assert b"TENANT_REQUIRED" in response.content

    def test_header_still_resolves_when_present_on_internal_chat(self, settings):
        settings.STRICT_TENANT_SCOPE = "strict"
        Tenant.objects.create(slug="formula-ic", name="F")
        request = RequestFactory().get(
            "/api/v1/internal-chat/admin/threads/", HTTP_X_TENANT="formula-ic"
        )
        response, captured = _call(request)
        assert request.tenant is not None
        assert captured and captured[0] is not None
        assert isinstance(response, tuple)
