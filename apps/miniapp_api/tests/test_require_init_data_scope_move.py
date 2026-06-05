"""Tests for moving tenant_scope out of require_init_data (#1019 / EPIC #1014).

The decorator must no longer enter a tenant scope; views that need one stack
``with_request_tenant`` below it. Catalog reads (tenant-scoped ``.objects``)
must therefore still work under strict mode via the wrapper, and booking must
still enter the correct tenant scope.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest
from django.http import JsonResponse
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.catalog.models import CatalogService
from apps.identity.models import BotUser
from apps.miniapp_api.views import require_init_data, with_request_tenant
from apps.tenancy.context import current_tenant
from apps.tenancy.models import Tenant

BOT_TOKEN = "scope-move-token"
BOT_TENANT_SLUG = "scope-move-test"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bind_bot(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = BOT_TENANT_SLUG
    settings.STRICT_TENANT_SCOPE = "strict"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug=BOT_TENANT_SLUG, name="Scope Move Salon", timezone="Europe/Moscow"
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        display_name="Мария",
        chat_id="12345",
    )


def test_require_init_data_does_not_enter_scope(tenant, bot_user) -> None:
    """The decorator attaches request.tenant but does NOT enter tenant_scope."""
    seen: dict = {}

    @require_init_data
    def stub(request):
        seen["tenant"] = current_tenant()
        seen["request_tenant"] = request.tenant
        return JsonResponse({"ok": True})

    req = RequestFactory().get("/x", HTTP_AUTHORIZATION=_init_data_header("12345"))
    resp = stub(req)

    assert resp.status_code == 200, getattr(resp, "content", b"")
    assert seen["request_tenant"].id == tenant.id  # identity still resolved
    assert seen["tenant"] is None  # but NO scope entered by the decorator


def test_with_request_tenant_enters_scope(tenant) -> None:
    seen: dict = {}

    @with_request_tenant
    def stub(request):
        seen["tenant"] = current_tenant()
        return JsonResponse({})

    req = RequestFactory().get("/x")
    req.tenant = tenant  # type: ignore[attr-defined]
    stub(req)

    assert seen["tenant"] == tenant


def test_catalog_read_endpoint_still_scoped_under_strict(tenant, bot_user) -> None:
    """slots/services/masters use tenant-scoped .objects; the @with_request_tenant
    wrapper must restore scope so they don't raise CrossTenantError in strict —
    AND scope to the *correct* tenant (the seeded service round-trips).
    """
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="home-service",
        name="Маникюр гель-лак",
        duration_min=60,
        is_active=True,
    )
    client = Client()
    resp = client.get(
        reverse("miniapp_api:services_list"),
        HTTP_AUTHORIZATION=_init_data_header("12345"),
    )
    assert resp.status_code == 200, resp.content  # did not 500 on CrossTenantError
    services = resp.json().get("services", [])
    # The scope entered is the user's OWN tenant — the seeded service is visible.
    assert any(str(item.get("id")) == str(service.id) for item in services), services
