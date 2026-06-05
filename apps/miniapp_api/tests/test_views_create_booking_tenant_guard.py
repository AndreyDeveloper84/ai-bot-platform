"""Regression-lock: cross-tenant guard for ``miniapp_api.create_booking`` (#925).

Background
----------
Issue #925 (``MUST_FIX_PRE_PILOT``, ADR-0009 Rule 5) flagged a *potential*
cross-tenant write leak: the client-authenticated (MAX init-data) booking
endpoint delegates to
:func:`apps.booking.services.create.create_customer_booking`, and the concern
was that an attacker holding valid init-data for tenant *A* could pass a
``service_id`` / ``master_id`` belonging to tenant *B* in the request body and
have a booking row written outside their authorised scope.

Re-read of ``origin/dev`` (per orchestrator decision, variant 3) established
that **the leak is not exploitable today**: the service layer runs inside
``tenant_scope(inp.tenant)`` where ``inp.tenant`` is the *server-resolved*
``bot_user.tenant`` (the decorator binds the tenant from
``settings.MAX_BOT_TENANT_SLUG``, never from the request), and it looks the
catalog rows up scoped to that tenant
(``CatalogService.objects.get(...)`` and
``CatalogMaster.all_tenants...get(id=..., tenant_id=inp.tenant.id)``). A
foreign ``service_id`` / ``master_id`` therefore resolves to "not found" and no
``BookingRequest`` row is ever created.

These tests **assert that existing behaviour** so a future refactor of either
the view or the service layer cannot silently re-open the vector. Nothing in
production code is changed by this file — it is a defence-in-depth lock around
the boundary called out in #925.

Not covered here (intentionally):
* The ``TenantUserRelationship`` / 403 ``tenant_mismatch`` scenario from the W3
  spec — that model does not exist in this repo and ``bot_user.tenant`` is a
  plain FK, so the scenario is structurally impossible.
* The concurrent-slot race — it is held by ``select_for_update`` inside the
  service layer and is covered in ``apps/booking/tests/test_create_service.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster, CatalogService
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-925"


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


def _future_visit_at() -> str:
    """A far-future ISO-8601 instant (tenant tz is Europe/Moscow, +03:00).

    Both guard paths reject before slot resolution, so the value only needs
    to clear the ``visit_in_past`` check at the top of the service.
    """

    target = date.today() + timedelta(days=30)
    return f"{target.isoformat()}T12:00:00+03:00"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    """Tenant *A* — the one the authenticated ``bot_user`` belongs to.

    The decorator resolves the request's tenant from
    ``settings.MAX_BOT_TENANT_SLUG``, so it must match this slug.
    """

    t = Tenant.objects.create(
        slug="tg925-home",
        name="Tenant Guard Home",
        timezone="Europe/Moscow",
    )
    settings.MAX_BOT_TENANT_SLUG = "tg925-home"
    return t


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        display_name="Мария",
        chat_id="12345",
    )


@pytest.fixture
def home_service(tenant: Tenant) -> CatalogService:
    """A valid, bookable service in tenant *A* — lets the cross-tenant
    *master* test clear the service lookup before hitting the master guard.
    """

    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=42,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="home-service",
        name="Маникюр гель-лак",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def foreign_tenant(db) -> Tenant:
    """Tenant *B* — the attacker is NOT authorised for this tenant."""

    return Tenant.objects.create(
        slug="tg925-foreign",
        name="Tenant Guard Foreign",
        timezone="Europe/Moscow",
    )


@pytest.fixture
def foreign_service(foreign_tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=foreign_tenant,
        external_id=9042,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        slug="foreign-service",
        name="Чужой маникюр",
        duration_min=60,
        is_active=True,
    )


@pytest.fixture
def foreign_master(foreign_tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=foreign_tenant,
        external_id=9001,
        external_updated_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        name="Чужой мастер",
        is_active=True,
    )


@pytest.mark.django_db
class TestCreateBookingTenantGuard:
    """Cross-tenant ``service_id`` / ``master_id`` must not produce a row.

    Authenticated as ``bot_user`` of tenant *A*; the request body carries IDs
    owned by tenant *B*.
    """

    def test_cross_tenant_service_id_returns_404_and_writes_no_row(
        self,
        client: Client,
        bot_user: BotUser,
        foreign_service: CatalogService,
        foreign_master: CatalogMaster,
    ) -> None:
        # Sanity: clean slate — the only thing under test is the write attempt.
        assert BookingRequest.all_tenants.count() == 0

        resp = client.post(
            reverse("miniapp_api:create_booking"),
            data=json.dumps(
                {
                    "service_id": str(foreign_service.id),
                    "master_id": str(foreign_master.id),
                    "visit_at": _future_visit_at(),
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )

        assert resp.status_code == 404, resp.json()
        assert resp.json()["error"] == "service_not_found"
        # The leak the ticket worried about: no projection row in ANY tenant.
        assert BookingRequest.all_tenants.count() == 0

    def test_cross_tenant_master_id_returns_404_and_writes_no_row(
        self,
        client: Client,
        bot_user: BotUser,
        home_service: CatalogService,
        foreign_master: CatalogMaster,
    ) -> None:
        # Service is valid (tenant A) so the service lookup passes and we reach
        # the master guard with a foreign master_id.
        assert BookingRequest.all_tenants.count() == 0

        resp = client.post(
            reverse("miniapp_api:create_booking"),
            data=json.dumps(
                {
                    "service_id": str(home_service.id),
                    "master_id": str(foreign_master.id),
                    "visit_at": _future_visit_at(),
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )

        assert resp.status_code == 404, resp.json()
        # Foreign master resolves to "not found" under the tenant-scoped lookup;
        # the under-lock re-check would raise tenant_mismatch (403) if it ever
        # slipped through. Either way it must not be a 2xx and must write no row.
        assert resp.json()["error"] in {"master_not_bookable", "tenant_mismatch"}
        assert BookingRequest.all_tenants.count() == 0
