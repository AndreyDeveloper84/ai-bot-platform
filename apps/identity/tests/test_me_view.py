"""Integration tests for the unified /api/v1/me endpoint (PR 1.5 / ADR-0008).

The endpoint is the launch-time identity probe for every Mini App.
These tests pin the response shape, the role detection, the phone-
masking contract, and cross-tenant isolation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest
from django.test import Client
from django.urls import reverse

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant, TenantStaff


BOT_TOKEN = "test-bot-token-me"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str, *, first_name: str = "Анна") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": first_name}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture
def tenant(db, settings) -> Tenant:
    t = Tenant.objects.create(slug="me-test", name="Студия Карина", timezone="Europe/Moscow")
    # Required so require_init_data can resolve the bot's tenant.
    settings.MAX_BOT_TENANT_SLUG = "me-test"
    return t


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(slug="me-other", name="Другой Салон")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="55501",
        display_name="Анна",
        client_name="Анна Петрова",
        chat_id="55501",
        phone="+79161234567",
    )


@pytest.fixture
def other_bot_user(other_tenant: Tenant) -> BotUser:
    """Same channel_user_id, different tenant — cross-tenant fixture."""

    return BotUser.all_tenants.create(
        tenant=other_tenant,
        channel="max",
        channel_user_id="77701",
        display_name="Не-та-Анна",
        phone="+79169999999",
    )


def _make_master(tenant: Tenant, bot_user: BotUser) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=2001,
        external_updated_at=datetime(2026, 5, 19, tzinfo=timezone.utc),
        name="Анна",
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        mode=CatalogMaster.Mode.INVITE,
        linked_bot_user=bot_user,
    )


def _url() -> str:
    return reverse("identity:me")


# --- auth ----------------------------------------------------------------


class TestMeAuth:
    def test_missing_header_returns_400(self, client: Client, tenant: Tenant) -> None:
        resp = client.get(_url())
        assert resp.status_code == 400
        assert resp.json()["error"] == "malformed"

    def test_bad_signature_returns_401(self, client: Client, tenant: Tenant) -> None:
        params = {
            "user": json.dumps({"id": 12345, "first_name": "Анна"}),
            "auth_date": str(int(time_module.time())),
        }
        raw = _sign(params, token="wrong-token")
        resp = client.get(_url(), HTTP_AUTHORIZATION=f"MaxInitData {raw}")
        assert resp.status_code == 401
        assert resp.json()["error"] == "bad_signature"


# --- response shape ------------------------------------------------------


class TestMeCustomer:
    def test_customer_only_shape(self, client: Client, tenant: Tenant, bot_user: BotUser) -> None:
        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        assert resp.status_code == 200
        data = resp.json()

        # Identity block
        assert data["user"]["id"] == str(bot_user.id)
        # Phone is ALWAYS masked, never raw.
        assert data["user"]["phone_masked"] == "+• ••• ••• ••67"
        assert "+79161234567" not in json.dumps(data)
        assert data["tenant"]["id"] == str(tenant.id)
        assert data["tenant"]["slug"] == "me-test"
        assert data["tenant"]["name"] == "Студия Карина"

        # Role block
        assert data["role"] == "customer"
        assert data["is_customer"] is True
        assert data["is_master"] is False
        assert data["is_admin"] is False
        assert data["is_owner"] is False
        assert data["is_receptionist"] is False
        assert data["master_id"] is None
        assert data["landing_path"] == "/"
        # Customer has empty capability set per ADR-0008.
        assert data["capabilities"] == []


class TestMeMaster:
    def test_master_response_includes_master_id(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        master = _make_master(tenant, bot_user)
        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["role"] == "master"
        assert data["is_master"] is True
        assert data["master_id"] == str(master.id)
        assert data["landing_path"] == "/master/dashboard"
        # Master capability surface — own-scope view + reply + safety promote.
        assert "view_conversation_list_own" in data["capabilities"]
        assert "send_reply_own" in data["capabilities"]
        assert "view_conversation_list_all" not in data["capabilities"]


class TestMeAdmin:
    def test_admin_capabilities_subset_matches_matrix(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.ADMIN
        )
        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        caps = set(data["capabilities"])
        assert resp.status_code == 200
        assert data["role"] == "admin"
        assert data["is_admin"] is True
        # Admin caps per §4
        assert "view_conversation_list_all" in caps
        assert "send_reply_all" in caps
        assert "demote_human_locked" in caps
        assert "approve_resume_after_locked" in caps
        assert "block_customer" in caps
        # NOT owner-only
        assert "manage_tenant_roles" not in caps
        assert "export_data" not in caps
        assert "tune_assistant" not in caps


class TestMeOwner:
    def test_owner_has_manage_tenant_roles(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER
        )
        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["role"] == "owner"
        assert data["landing_path"] == "/admin/dashboard"
        caps = set(data["capabilities"])
        # Owner-only privileges per §4
        assert "manage_tenant_roles" in caps
        assert "export_data" in caps
        assert "tune_assistant" in caps
        assert "override_sla" in caps


# --- multi-role ----------------------------------------------------------


class TestMeMultiRole:
    def test_master_plus_admin_resolves_to_admin_with_master_id(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        master = _make_master(tenant, bot_user)
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.ADMIN
        )
        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        assert resp.status_code == 200
        # Admin is the primary role (highest privilege).
        assert data["role"] == "admin"
        # But master_id is still surfaced — Anna can navigate to her
        # master dashboard even though admin is her landing.
        assert data["is_master"] is True
        assert data["master_id"] == str(master.id)
        # Combined capabilities include both scopes.
        caps = set(data["capabilities"])
        assert "view_conversation_list_all" in caps
        assert "view_conversation_list_own" in caps


# --- cross-tenant --------------------------------------------------------


class TestMeCrossTenantIsolation:
    def test_other_tenant_owner_does_not_leak(
        self,
        client: Client,
        tenant: Tenant,
        other_tenant: Tenant,
        bot_user: BotUser,
        other_bot_user: BotUser,
    ) -> None:
        """An Owner row in another tenant must NOT promote the calling user."""

        # Make other_bot_user the OWNER of other_tenant.
        TenantStaff.all_tenants.create(
            tenant=other_tenant,
            bot_user=other_bot_user,
            role=TenantStaff.Role.OWNER,
        )
        # Caller is the customer in `tenant` — their channel_user_id is
        # 55501, which is NOT the same as other_bot_user's. The auth
        # decorator resolves to the bot's configured tenant ("me-test")
        # via MAX_BOT_TENANT_SLUG.
        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        assert resp.status_code == 200
        # Caller remains a customer in their own tenant.
        assert data["role"] == "customer"
        assert data["is_owner"] is False
        assert data["tenant"]["slug"] == "me-test"


class TestMeIsSoloProvider:
    """Веха 3 — `is_solo_provider` field per Tau policy §3.1.

    Mini-app reads this flag to gate Universal UI smart defaults
    (hide team chrome for solo providers; show full features for teams).
    """

    def test_customer_tenant_reports_false(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Caller is a customer of a tenant with no other people →
        empty staff + empty masters → 0 distinct → False.

        Bootstrap-like state — customer-only tenant is NOT solo-provider.
        """
        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["is_solo_provider"] is False

    def test_solo_owner_master_reports_true(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Same bot_user as Owner + Admin + Master → 1 distinct → True."""
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER
        )
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.ADMIN
        )
        _make_master(tenant, bot_user)

        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["is_solo_provider"] is True
        # Role-detection still resolves normally
        assert data["is_owner"] is True
        assert data["is_master"] is True

    def test_team_tenant_reports_false(
        self, client: Client, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Two distinct people in staff (owner + admin) → False."""
        other = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="55502",
            display_name="Admin Vova",
        )
        TenantStaff.all_tenants.create(
            tenant=tenant, bot_user=bot_user, role=TenantStaff.Role.OWNER
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=other, role=TenantStaff.Role.ADMIN)

        resp = client.get(
            _url(),
            HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
        )
        data = resp.json()
        assert resp.status_code == 200
        assert data["is_solo_provider"] is False
        assert data["is_owner"] is True  # caller is still owner
