"""Security-critical tests for the dev-mode init-data bypass.

Coverage matrix mirrors apps/miniapp_api/dev_bypass.py docstring:

  | DEBUG  | X-Dev-Bypass | other headers      | Result                  |
  | False  | any          | any                | HMAC enforced, no log   |
  | True   | absent       | n/a                | HMAC enforced           |
  | True   | "1"          | valid user+tenant  | BYPASS + WARNING log    |
  | True   | "1"          | missing/invalid    | HMAC enforced + log     |

The bypass is OPT-IN per request and DEBUG-gated so it cannot accidentally
activate in production. These tests pin that contract.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time as time_module
import uuid
from urllib.parse import urlencode

import pytest
from django.test import Client, override_settings

from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


BOT_TOKEN = "test-bot-token-xyz"
AUTH_VERIFY_URL = "/api/v1/customer/auth/verify"


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _valid_init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


# --- fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


@pytest.fixture(autouse=True)
def _bind_bot_tenant(settings) -> None:
    """Customer require_init_data needs MAX_BOT_TENANT_SLUG set for HMAC path."""
    settings.MAX_BOT_TENANT_SLUG = "dev-bypass-test"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="dev-bypass-test",
        name="Dev Bypass Test Salon",
        timezone="Europe/Moscow",
    )


@pytest.fixture
def other_tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="dev-bypass-other",
        name="Dev Bypass Other Salon",
        timezone="Europe/Moscow",
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


@pytest.fixture
def cross_tenant_bot_user(other_tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=other_tenant,
        channel="max",
        channel_user_id="55555",
        display_name="Other",
        chat_id="55555",
    )


# --- 1. PROD-MODE SAFETY ---------------------------------------------------


class TestProdModeIgnoresBypass:
    """When DEBUG=False, dev headers MUST be a no-op regardless of payload."""

    @override_settings(DEBUG=False)
    def test_all_bypass_headers_present_still_enforces_hmac(
        self, tenant: Tenant, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(bot_user.id),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )

        # No Authorization header at all → 400 malformed (HMAC path runs).
        assert resp.status_code == 400
        # CRITICAL: no log line leaked. In prod the helper exits BEFORE
        # reading headers, so even probes don't generate signal.
        assert not any("DEV-BYPASS" in rec.getMessage() for rec in caplog.records)

    @override_settings(DEBUG=False)
    def test_prod_with_bypass_headers_and_bad_initdata_returns_401(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """Even with bypass headers, a tampered init-data must 401."""
        client = Client()
        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_AUTHORIZATION="MaxInitData garbage-not-signed",
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(bot_user.id),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        assert resp.status_code in (400, 401)
        body = resp.json()
        # NOT the bypass-resolved identity.
        assert "tenant" not in body or body.get("error") in {
            "malformed",
            "bad_signature",
        }


# --- 2. DEV-MODE HAPPY PATH ------------------------------------------------


class TestDevModeHappyPath:
    @override_settings(DEBUG=True)
    def test_correct_headers_bypass_succeeds(
        self, tenant: Tenant, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(bot_user.id),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )

        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body["user"]["id"] == str(bot_user.id)
        assert body["tenant"]["slug"] == tenant.slug

        # WARNING log fires with tenant + user + url.
        bypass_logs = [
            rec
            for rec in caplog.records
            if "DEV-BYPASS" in rec.getMessage() and "skipped" in rec.getMessage()
        ]
        assert len(bypass_logs) == 1
        msg = bypass_logs[0].getMessage()
        assert tenant.slug in msg
        assert str(bot_user.id) in msg
        assert "/api/v1/customer/auth/verify" in msg


# --- 3. DEV-MODE REJECTIONS ------------------------------------------------


class TestDevModeRejections:
    """Bypass header present but other headers missing / invalid."""

    @override_settings(DEBUG=True)
    def test_no_bypass_header_runs_hmac_path(
        self, tenant: Tenant, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        # Real init-data with no dev headers → HMAC path runs.
        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_AUTHORIZATION=_valid_init_data_header(),
        )
        assert resp.status_code == 200
        # No bypass log lines.
        assert not any("DEV-BYPASS" in rec.getMessage() for rec in caplog.records)

    @override_settings(DEBUG=True)
    def test_missing_user_id_header(
        self, tenant: Tenant, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        # No bypass → HMAC path runs → 400 malformed (no Auth header).
        assert resp.status_code == 400
        # Rejection logged.
        assert any(
            "DEV-BYPASS" in rec.getMessage() and "rejected" in rec.getMessage()
            for rec in caplog.records
        )

    @override_settings(DEBUG=True)
    def test_missing_tenant_slug_header(
        self, tenant: Tenant, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(bot_user.id),
        )
        assert resp.status_code == 400
        assert any(
            "DEV-BYPASS" in rec.getMessage() and "rejected" in rec.getMessage()
            for rec in caplog.records
        )

    @override_settings(DEBUG=True)
    def test_invalid_uuid_user_id(self, tenant: Tenant, caplog: pytest.LogCaptureFixture) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID="not-a-uuid",
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        assert resp.status_code == 400
        assert any(
            "DEV-BYPASS" in rec.getMessage() and "rejected" in rec.getMessage()
            for rec in caplog.records
        )

    @override_settings(DEBUG=True)
    def test_nonexistent_user_id(self, tenant: Tenant, caplog: pytest.LogCaptureFixture) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        ghost = uuid.uuid4()
        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(ghost),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        assert resp.status_code == 400  # HMAC path runs, no Auth header
        assert any(
            "DEV-BYPASS" in rec.getMessage() and "rejected" in rec.getMessage()
            for rec in caplog.records
        )

    @override_settings(DEBUG=True)
    def test_cross_tenant_user(
        self,
        tenant: Tenant,
        cross_tenant_bot_user: BotUser,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """BotUser exists but in another tenant → reject.

        Pins the tenant-scoping guard: the helper filters by
        (id, tenant=Tenant.objects.get(slug=...)), so a UUID that exists
        only in tenant-B is invisible from tenant-A's headers.
        """
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        # cross_tenant_bot_user is in other_tenant; we ask for `tenant`.
        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(cross_tenant_bot_user.id),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        assert resp.status_code == 400  # falls through to HMAC, malformed
        assert any(
            "DEV-BYPASS" in rec.getMessage() and "rejected" in rec.getMessage()
            for rec in caplog.records
        )

    @override_settings(DEBUG=True)
    def test_nonexistent_tenant_slug(
        self, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(bot_user.id),
            HTTP_X_DEV_TENANT_SLUG="ghost-tenant",
        )
        assert resp.status_code == 400
        assert any(
            "DEV-BYPASS" in rec.getMessage()
            and "rejected" in rec.getMessage()
            and "tenant not found" in rec.getMessage()
            for rec in caplog.records
        )

    @override_settings(DEBUG=True)
    def test_bypass_header_value_not_exactly_one(
        self, tenant: Tenant, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Only ``X-Dev-Bypass: 1`` activates the bypass — ``true`` / ``yes`` don't."""
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_X_DEV_BYPASS="true",  # not "1"
            HTTP_X_DEV_USER_ID=str(bot_user.id),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        assert resp.status_code == 400
        # Header value didn't match — no log (silent fall-through).
        assert not any("DEV-BYPASS" in rec.getMessage() for rec in caplog.records)


# --- 4. PRECEDENCE: REAL INIT-DATA + DEV HEADERS ---------------------------


class TestRealInitDataStillWorks:
    @override_settings(DEBUG=True)
    def test_real_init_data_no_dev_headers_runs_hmac_path(
        self, tenant: Tenant, bot_user: BotUser
    ) -> None:
        client = Client()
        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_AUTHORIZATION=_valid_init_data_header(user_id="12345"),
        )
        assert resp.status_code == 200
        assert resp.json()["user"]["channel_user_id"] == "12345"

    @override_settings(DEBUG=True)
    def test_real_init_data_plus_dev_headers_bypass_wins(
        self, tenant: Tenant, bot_user: BotUser, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Documented precedence: when both are present, dev-bypass wins.

        Rationale: caller explicitly opted into dev mode by sending the
        bypass header. The "headers always work in dev" invariant is
        simpler than "init-data silently overrides headers".
        """
        client = Client()
        caplog.set_level(logging.WARNING, logger="apps.miniapp_api.dev_bypass")

        resp = client.post(
            AUTH_VERIFY_URL,
            HTTP_AUTHORIZATION=_valid_init_data_header(user_id="12345"),
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(bot_user.id),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        assert resp.status_code == 200
        # Bypass log fired → bypass path took precedence.
        assert any(
            "DEV-BYPASS" in rec.getMessage() and "skipped" in rec.getMessage()
            for rec in caplog.records
        )
